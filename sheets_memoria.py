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
HISTORIAL_HEADERS = ["Fecha", "Hora", "Titulo", "CantMedios", "TieneOle"]
INFORMES_HEADERS = ["Fecha", "Periodo", "Informe"]
CONFIG_DEFAULTS = [
    ["parametro", "valor", "descripcion"],
    ["umbral_medios", "4", "Mínimo de medios cubriendo un tema sin Olé para alertar"],
    ["watchlist", "river, boca, seleccion argentina", "Keywords a vigilar siempre (separadas por coma)"],
    ["horas_silencio", "48", "No repetir un aviso del mismo tema dentro de estas horas"],
    ["dias_archivo", "3", "Mover a la pestaña Archivo las filas de Agenda más viejas que esto"],
    ["ignorar", "", "Temas a no mostrar nunca (keywords separadas por coma, ej: tenis, nba)"],
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
    cfg = {"umbral_medios": 4, "watchlist": [], "horas_silencio": 48,
           "dias_archivo": 3, "ignorar": []}
    try:
        ws = _ws("Config", CONFIG_DEFAULTS[0], defaults=CONFIG_DEFAULTS)
        filas = ws.get_all_values()
        vistos = set()
        for fila in filas[1:]:
            if len(fila) < 2:
                continue
            k, v = fila[0].strip().lower(), fila[1].strip()
            vistos.add(k)
            if k == "umbral_medios" and v.isdigit():
                cfg["umbral_medios"] = int(v)
            elif k == "horas_silencio" and v.isdigit():
                cfg["horas_silencio"] = int(v)
            elif k == "dias_archivo" and v.isdigit():
                cfg["dias_archivo"] = int(v)
            elif k == "watchlist":
                cfg["watchlist"] = [w.strip().lower() for w in v.split(",") if w.strip()]
            elif k == "ignorar":
                cfg["ignorar"] = [w.strip().lower() for w in v.split(",") if w.strip()]
        # auto-agregar al Sheet los parámetros nuevos que falten (updates del sistema)
        for fila_def in CONFIG_DEFAULTS[1:]:
            if fila_def[0] not in vistos:
                ws.append_row(fila_def, value_input_option="RAW")
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


def _parse_fecha(texto: str):
    """Tolera los formatos con que Sheets puede mostrar la fecha según la
    configuración regional de la planilla."""
    texto = texto.strip()
    for fmt in ("%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, fmt).replace(tzinfo=_TZ_AR)
        except Exception:
            continue
    return None


def _es_reciente(fecha_hora: str, horas: int) -> bool:
    dt = _parse_fecha(fecha_hora)
    if dt is None:
        return False
    return datetime.now(_TZ_AR) - dt < timedelta(hours=horas)


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


# ── Historial (registro acumulado para inteligencia semanal) ─────────────────
def registrar_historial(tendencias: list, max_temas: int = 30):
    """Deja constancia compacta de qué se publicó en esta corrida.
    Solo clusters con 2+ medios (el resto es ruido para el análisis)."""
    try:
        ws = _ws("Historial", HISTORIAL_HEADERS)
        ahora = datetime.now(_TZ_AR)
        filas = [
            [ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"),
             c.get("titulo", "")[:200], str(c.get("cant_medios", 1)),
             "1" if c.get("tiene_ole") else "0"]
            for c in tendencias[:max_temas] if c.get("cant_medios", 1) >= 2
        ]
        if filas:
            ws.append_rows(filas, value_input_option="RAW")
        return len(filas)
    except Exception:
        return 0


def leer_historial(dias: int = 7) -> list:
    """Devuelve las filas del Historial de los últimos N días."""
    out = []
    try:
        ws = _ws("Historial", HISTORIAL_HEADERS)
        limite = datetime.now(_TZ_AR) - timedelta(days=dias)
        for fila in ws.get_all_values()[1:]:
            if len(fila) < 5:
                continue
            dt = _parse_fecha(f"{fila[0]} {fila[1]}") or _parse_fecha(fila[0])
            if dt is None or dt < limite:
                continue
            out.append({
                "fecha": fila[0], "hora": fila[1], "titulo": fila[2],
                "cant_medios": int(fila[3]) if str(fila[3]).isdigit() else 1,
                "tiene_ole": str(fila[4]) == "1",
            })
    except Exception:
        pass
    return out


# ── Archivo (limpieza automática de la Agenda) ───────────────────────────────
def archivar_agenda_vieja(dias: int = 3) -> int:
    """Mueve a la pestaña Archivo las filas de Agenda más viejas que N días.
    La Agenda queda corta y fresca; el Archivo conserva todo el historial."""
    try:
        ws = _ws("Agenda", AGENDA_HEADERS)
        filas = ws.get_all_values()
        if len(filas) <= 1:
            return 0
        limite = datetime.now(_TZ_AR) - timedelta(days=dias)
        quedan, van = [], []
        for fila in filas[1:]:
            dt = _parse_fecha(fila[0]) if fila else None
            (van if (dt is not None and dt < limite) else quedan).append(fila)
        if not van:
            return 0
        ws_arch = _ws("Archivo", AGENDA_HEADERS)
        ws_arch.append_rows(van, value_input_option="RAW")
        ws.clear()
        ws.update(range_name="A1", values=[AGENDA_HEADERS] + quedan,
                  value_input_option="USER_ENTERED")
        return len(van)
    except Exception:
        return 0


# ── Informes (análisis semanal generado por IA) ──────────────────────────────
def guardar_informe(texto: str, periodo: str):
    try:
        ws = _ws("Informes", INFORMES_HEADERS)
        ws.append_row(
            [datetime.now(_TZ_AR).strftime("%Y-%m-%d"), periodo, texto[:45000]],
            value_input_option="RAW",
        )
        return True
    except Exception:
        return False


def url_planilla() -> str:
    _, sid = _credenciales()
    return f"https://docs.google.com/spreadsheets/d/{sid}" if sid else ""
