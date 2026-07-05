"""sheets_memoria.py — Google Sheets como memoria, tablero y panel de control.

Pestañas que gestiona (las crea si no existen):
  • Agenda    — el tablero vivo: cada fila es una acción sugerida. El editor
                marca la columna Estado ("hecho" / "descartado") y el vigía
                deja de insistir con ese tema.
  • Snapshot  — la última corrida (clusters), para calcular momentum real
                entre corridas. Se reemplaza entera en cada corrida.
  • Config    — parámetros editables por el editor sin tocar código:
                umbral de medios para alertar, watchlist de keywords.

Credenciales (env o inyectadas con configure()):
  GOOGLE_SERVICE_ACCOUNT_JSON  — el JSON completo de la service account
  SHEET_ID                     — el ID de la planilla (de su URL)

Si falta algo, todas las funciones degradan sin romper: disponible() da False
y los llamadores siguen con su comportamiento local.
"""
import os
import json
from datetime import datetime, timedelta, timezone

try:
    import gspread
except Exception:  # gspread no instalado: modo degradado
    gspread = None

_TZ_AR = timezone(timedelta(hours=-3))

AGENDA_HEADERS = ["Fecha", "Hora", "Accion", "Tema", "Medios", "Momentum",
                  "Motivo", "URL", "Estado", "Origen", "Clave"]
SNAPSHOT_HEADERS = ["RunTS", "Origen", "Titulo", "CantMedios", "TieneOle"]
CONFIG_DEFAULTS = [
    ["parametro", "valor", "descripcion"],
    ["umbral_medios", "4", "Mínimo de medios cubriendo un tema sin Olé para alertar"],
    ["watchlist", "river, boca, seleccion argentina", "Keywords a vigilar siempre (separadas por coma)"],
    ["horas_silencio", "48", "No repetir un aviso del mismo tema dentro de estas horas"],
]

_conf = {"json": None, "sheet_id": None}
_cache = {"sh": None}


def configure(service_account_json: str = None, sheet_id: str = None):
    """Permite inyectar credenciales desde st.secrets en vez de env."""
    if service_account_json:
        _conf["json"] = service_account_json
    if sheet_id:
        _conf["sheet_id"] = sheet_id
    _cache["sh"] = None


def _credenciales():
    sa = _conf["json"] or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    sid = _conf["sheet_id"] or os.environ.get("SHEET_ID", "")
    return sa, sid


def disponible() -> bool:
    sa, sid = _credenciales()
    return bool(gspread and sa and sid)


def _sheet():
    if _cache["sh"] is not None:
        return _cache["sh"]
    sa, sid = _credenciales()
    creds = json.loads(sa)
    client = gspread.service_account_from_dict(creds)
    _cache["sh"] = client.open_by_key(sid)
    return _cache["sh"]


def _ws(nombre: str, headers: list, defaults: list = None):
    sh = _sheet()
    try:
        ws = sh.worksheet(nombre)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=nombre, rows=200, cols=max(len(headers), 3))
        if defaults:
            ws.update(range_name="A1", values=defaults)
        else:
            ws.update(range_name="A1", values=[headers])
    return ws


def asegurar_estructura():
    _ws("Agenda", AGENDA_HEADERS)
    _ws("Snapshot", SNAPSHOT_HEADERS)
    _ws("Config", CONFIG_DEFAULTS[0], defaults=CONFIG_DEFAULTS)


# ── Config ────────────────────────────────────────────────────────────────────
def leer_config() -> dict:
    """Devuelve la config del Sheet con defaults sanos si algo falta."""
    cfg = {"umbral_medios": 4, "watchlist": [], "horas_silencio": 48}
    try:
        ws = _ws("Config", CONFIG_DEFAULTS[0], defaults=CONFIG_DEFAULTS)
        for fila in ws.get_all_values()[1:]:
            if len(fila) < 2:
                continue
            k, v = fila[0].strip().lower(), fila[1].strip()
            if k == "umbral_medios" and v.isdigit():
                cfg["umbral_medios"] = int(v)
            elif k == "horas_silencio" and v.isdigit():
                cfg["horas_silencio"] = int(v)
            elif k == "watchlist":
                cfg["watchlist"] = [w.strip().lower() for w in v.split(",") if w.strip()]
    except Exception:
        pass
    return cfg


# ── Snapshot (memoria de la corrida anterior, para momentum) ────────────────
def leer_snapshot_anterior() -> list:
    """Lee los clusters de la última corrida guardada. Formato compatible
    con calcular_momentum: [{titulo, cant_medios, tiene_ole}, ...]"""
    try:
        ws = _ws("Snapshot", SNAPSHOT_HEADERS)
        out = []
        for fila in ws.get_all_values()[1:]:
            if len(fila) < 5:
                continue
            out.append({
                "titulo": fila[2],
                "cant_medios": int(fila[3]) if str(fila[3]).isdigit() else 1,
                "tiene_ole": str(fila[4]).lower() in ("1", "true", "si", "sí"),
            })
        return out
    except Exception:
        return []


def guardar_snapshot(tendencias: list, origen: str, max_temas: int = 60):
    """Reemplaza la pestaña Snapshot con la corrida actual."""
    try:
        ws = _ws("Snapshot", SNAPSHOT_HEADERS)
        ts = datetime.now(_TZ_AR).strftime("%Y-%m-%d %H:%M")
        filas = [SNAPSHOT_HEADERS]
        for c in tendencias[:max_temas]:
            filas.append([ts, origen, c.get("titulo", "")[:200],
                          str(c.get("cant_medios", 1)),
                          "1" if c.get("tiene_ole") else "0"])
        ws.clear()
        ws.update(range_name="A1", values=filas)
        return True
    except Exception:
        return False


# ── Agenda (tablero + feedback del editor) ───────────────────────────────────
def leer_agenda_estados() -> dict:
    """Devuelve {clave: (estado, fecha_hora_str)} con la última fila por clave.
    Es el canal de vuelta: 'hecho' o 'descartado' silencian el tema."""
    estados = {}
    try:
        ws = _ws("Agenda", AGENDA_HEADERS)
        for fila in ws.get_all_values()[1:]:
            if len(fila) < 11 or not fila[10]:
                continue
            estados[fila[10]] = (fila[8].strip().lower(), f"{fila[0]} {fila[1]}")
    except Exception:
        pass
    return estados


def _es_reciente(fecha_hora: str, horas: int) -> bool:
    try:
        dt = datetime.strptime(fecha_hora.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=_TZ_AR)
        return datetime.now(_TZ_AR) - dt < timedelta(hours=horas)
    except Exception:
        return False


def filtrar_ya_tratados(items: list, horas_silencio: int = 48) -> list:
    """Quita de la lista los temas que el editor marcó hecho/descartado,
    y los ya avisados como pendientes dentro de la ventana de silencio."""
    estados = leer_agenda_estados()
    out = []
    for it in items:
        clave = it.get("clave", "")
        prev = estados.get(clave)
        if prev:
            estado, fh = prev
            if estado in ("hecho", "descartado", "ok", "listo"):
                continue
            if _es_reciente(fh, horas_silencio):
                continue
        out.append(it)
    return out


def agregar_a_agenda(items: list, origen: str):
    """Agrega filas nuevas al tablero. Cada item: dict de construir_agenda + clave."""
    if not items:
        return 0
    try:
        ws = _ws("Agenda", AGENDA_HEADERS)
        ahora = datetime.now(_TZ_AR)
        filas = []
        for it in items:
            if it.get("nuevo"):
                mom = "nuevo"
            elif it.get("delta", 0) > 0:
                mom = f"sube +{it['delta']}"
            elif it.get("delta", 0) < 0:
                mom = f"baja {it['delta']}"
            else:
                mom = "estable"
            filas.append([
                ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
                it.get("accion", ""), it.get("titulo", "")[:200],
                str(it.get("cant_medios", "")), mom,
                it.get("motivo", "")[:150], it.get("url") or "",
                "pendiente", origen, it.get("clave", ""),
            ])
        ws.append_rows(filas, value_input_option="USER_ENTERED")
        return len(filas)
    except Exception:
        return 0


def url_planilla() -> str:
    _, sid = _credenciales()
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else ""
