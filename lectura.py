"""lectura.py — Lectura de la home de Olé contra la agenda del día.

Corre a demanda (GitHub Actions → "Lectura de la home" → Run workflow) o con
horario fijo si se activa en .github/workflows/lectura.yml. En cada corrida:
  1. Scrapea la competencia (las mismas fuentes del Monitor).
  2. Scrapea la home de Olé COMPLETA, con la posición de cada nota.
  3. Cruza: qué temas de la agenda nacional faltan en la home, qué notas de
     la home quedaron vencidas, duplicados, voz propia y commodity.
  4. Agrega una fila de indicadores en la pestaña "Lectura" (tendencia) y
     reemplaza la pestaña "Lectura detalle" con el detalle del día.
  5. Manda un resumen por Telegram, con 3-4 líneas de lectura escritas por IA.

Sin IA configurada, manda igual los números (sin la lectura escrita).
Sin planilla, corre en modo simulacro: imprime todo y no escribe nada.

Prueba local sin internet (con un Panorama Total exportado y la home pegada):
    python lectura.py --desde-archivos panorama.txt home.txt
"""
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import monitor_core
from monitor_core import (
    TODAS_FUENTES, FUENTES_NAC_IDS, HEADERS, calcular_tendencias,
    normalizar_titulo, similitud_jaccard, coincide_cobertura,
    detectar_entidades, dic_entidades, _norm_texto, _extraer_ole,
)

_TZ_AR = timezone(timedelta(hours=-3))

# ─── PARÁMETROS (ajustables) ──────────────────────────────────────────────────
OLE_HOME_URL = "https://www.ole.com.ar/"
MAX_NOTAS_HOME = 250          # tope de notas a leer de la home (antes: 50)
UMBRAL_AGENDA = 3             # un tema es "agenda" si lo tienen N+ medios nacionales
UMBRAL_AGENDA_TOTAL = 6       # ...o si lo tienen 2+ nacionales y N+ medios en total (incluye exterior)
UMBRAL_MISMO_TEMA = 0.30      # parecido (pesado por rareza) para dar un tema por cubierto con otras palabras
UMBRAL_COMMODITY = 5          # un título es commodity si su tema lo tienen N+ nacionales
ZONA_APERTURA = 12            # posiciones 1-12 = apertura
ZONA_SCROLL = 40              # 13-40 = primer scroll; el resto, abajo
# Títulos de la home que no cuentan como tema editorial (keywords, sin tildes).
# Se puede pisar desde la planilla, pestaña Config, parámetro "lectura_excluir".
EXCLUIR_DEFAULT = "ole summit, brand studio"
# Medios nacionales comparables: los directos, sin Olé ni la cuenta oficial de AFA
NAC_COMPARABLES = FUENTES_NAC_IDS - {"ole", "afa"}

RE_PREVIA = re.compile(
    r"a que hora|como ver|donde ver|por donde|horario|en vivo|va por|palpit|"
    r"previa|formacion|probable|juega hoy|juegan hoy|ultimo momento|"
    r"busca |enfrenta|recibe a|se mide|se miden|que necesita")
RE_RESULTADO = re.compile(
    r"\bgano\b|vencio|empato|perdio|termino|elimino|cayo|campeon|consagr|"
    r"golea|goleo|goleado|derroto|igualo|se quedo con|fue \d|\b\d-\d\b|"
    r"\b\d a \d\b|avanzo|clasifico|se impuso")
PALABRAS_SERVICIO = {
    "hora", "ver", "vivo", "horario", "horarios", "donde", "como", "formaciones",
    "formacion", "canal", "online", "juega", "juegan", "hoy", "octavos", "cuartos",
    "torneo", "clausura", "apertura", "previa", "dia", "dias", "pasan", "transmite",
    "streaming", "seguilo", "cuando", "fecha", "todo", "saber",
}
RE_VOZ_PROPIA = re.compile(
    r"con ole\b|enviad[oa]s? especial|exclusiv|rayos x|informe de ole|"
    r"especial multimedia|datos de ole|ole te cuenta")


# ─── 1. HOME DE OLÉ COMPLETA, CON POSICIÓN ────────────────────────────────────
def _limpio(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip()


def scrapear_home_ole() -> list:
    """La home de Olé entera, en el orden en que aparece en la página.
    Usa el mismo extractor del Monitor pero sin el tope de 50, y ordena cada
    título por dónde aparece en el texto de la página (aprox. la jerarquía)."""
    resp = requests.get(OLE_HOME_URL, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    html = resp.text
    tope_original = monitor_core.MAX_ITEMS
    try:
        monitor_core.MAX_ITEMS = MAX_NOTAS_HOME
        notas = _extraer_ole(html, {"id": "ole", "es_ole": True})
    finally:
        monitor_core.MAX_ITEMS = tope_original
    texto = _limpio(BeautifulSoup(html, "html.parser").get_text(" "))
    for n in notas:
        p = texto.find(_limpio(n["titulo"]))
        n["_orden"] = p if p >= 0 else 10 ** 9
    notas.sort(key=lambda n: n["_orden"])
    for i, n in enumerate(notas, 1):
        n["posicion"] = i
        n.pop("_orden", None)
    return notas


def filtrar_home(home: list, excluir: str = EXCLUIR_DEFAULT) -> tuple:
    """Saca de la home los títulos con alguna keyword de 'excluir' (separadas
    por coma). Conserva la posición original en la página. Devuelve (home, sacados)."""
    claves = [_norm_texto(k.strip()) for k in (excluir or "").split(",") if k.strip()]
    if not claves:
        return home, []
    quedan, fuera = [], []
    for n in home:
        (fuera if any(k in _norm_texto(n["titulo"]) for k in claves) else quedan).append(n)
    return quedan, fuera


def excluir_de_config() -> str:
    try:
        import sheets_memoria as mem
        if mem.disponible():
            filas = mem._ws("Config", ["parametro", "valor", "descripcion"]).get_all_values()
            for f in filas:
                if f and f[0].strip() == "lectura_excluir" and len(f) > 1:
                    return f[1]
    except Exception:
        pass
    return EXCLUIR_DEFAULT


def zona(pos: int) -> str:
    if pos <= ZONA_APERTURA:
        return "apertura"
    if pos <= ZONA_SCROLL:
        return "primer scroll"
    return "abajo"


# ─── 2. ANÁLISIS (sin IA, sin red: testeable) ────────────────────────────────
def analizar_home(home: list, resultados: dict, dic: dict = None) -> dict:
    """home: [{titulo, url, posicion}] · resultados: {fuente_id: [noticias]}
    (la competencia; si trae 'ole' se reemplaza por la home completa)."""
    dic = dic or dic_entidades("")
    res = dict(resultados)
    res["ole"] = [{"titulo": n["titulo"], "url": n.get("url")} for n in home]
    pos_de = {n["titulo"]: n["posicion"] for n in home}
    home_keys = [(n, normalizar_titulo(n["titulo"])) for n in home]

    nac_heads = [(fid, n) for fid in NAC_COMPARABLES for n in res.get(fid, [])]
    n_medios_ok = sum(1 for fid in NAC_COMPARABLES if res.get(fid))

    # Temas (clusters) del Monitor, contados por medios nacionales distintos
    temas = []
    for c in calcular_tendencias(res):
        nac_ids = sorted({x["fuente"]["id"] for x in c["noticias"]} & NAC_COMPARABLES)
        if not nac_ids:
            continue
        notas_ole = [x["noticia"]["titulo"] for x in c["noticias"] if x["fuente"]["id"] == "ole"]
        temas.append({"c": c, "nac_ids": nac_ids, "n_nac": len(nac_ids), "notas_ole": notas_ole})

    # Segunda chance contra títulos creativos de Olé: cuenta como cubierto si
    # se parece a algún título de la home pesando cada palabra por su rareza
    # en el día (comparten "Obelisco" pesa mucho; comparten "Boca" casi nada).
    todos = [normalizar_titulo(c["titulo"]) for _, c in nac_heads] + [hk for _, hk in home_keys]
    pesos = monitor_core._calcular_idf(todos)

    def _mismo_tema(a, b, ambas_previa=False):
        if monitor_core.similitud_ponderada(a, b, pesos) >= UMBRAL_MISMO_TEMA:
            return True
        # dos previas/servicio del mismo partido ("River vs Lanús: hora y TV")
        g = monitor_core.GENERICOS_FUTBOL | PALABRAS_SERVICIO
        return ambas_previa and len((a - g) & (b - g)) >= 2

    for t in temas:
        if t["notas_ole"]:
            continue
        for x in t["c"]["noticias"][:6]:
            k = normalizar_titulo(x["noticia"]["titulo"])
            prev_x = bool(RE_PREVIA.search(_norm_texto(x["noticia"]["titulo"])))
            match = next((n["titulo"] for n, hk in home_keys
                          if _mismo_tema(k, hk, prev_x and bool(RE_PREVIA.search(_norm_texto(n["titulo"]))))), None)
            if match:
                t["notas_ole"] = [match]
                break

    agenda = [t for t in temas if t["n_nac"] >= UMBRAL_AGENDA
              or (t["n_nac"] >= 2 and t["c"]["cant_medios"] >= UMBRAL_AGENDA_TOTAL)]
    cubiertos = [t for t in agenda if t["notas_ole"]]
    faltantes = sorted([t for t in agenda if not t["notas_ole"]],
                       key=lambda t: (-t["n_nac"], -t["c"]["cant_medios"]))
    nombre = {f["id"]: f["nombre"] for f in TODAS_FUENTES}

    faltantes_out = [{
        "titulo": t["c"]["titulo"],
        "url": t["c"].get("url") or "",
        "n_nac": t["n_nac"],
        "n_total": t["c"]["cant_medios"],
        "medios": ", ".join(nombre.get(i, i) for i in t["nac_ids"]),
    } for t in faltantes]

    # Temas grandes que Olé tiene, pero abajo
    enterrados = []
    for t in cubiertos:
        if t["n_nac"] < UMBRAL_COMMODITY:
            continue
        mejor = min(pos_de.get(x, 10 ** 6) for x in t["notas_ole"])
        if ZONA_SCROLL < mejor < 10 ** 6:
            enterrados.append({"titulo": t["notas_ole"][0], "posicion": mejor,
                               "n_nac": t["n_nac"]})

    # Commodity: títulos de Olé cuyo tema tienen 5+ nacionales
    commodity_titulos = set()
    for t in temas:
        if t["n_nac"] >= UMBRAL_COMMODITY:
            commodity_titulos.update(x for x in t["notas_ole"] if x in pos_de)
    commodity_pct = round(100 * len(commodity_titulos) / max(len(home), 1), 1)

    # Sin par: títulos de Olé que ningún nacional tiene
    en_tema = {x for t in temas for x in t["notas_ole"]}
    sin_par = [n for n, hk in home_keys if n["titulo"] not in en_tema
               and not any(_mismo_tema(hk, normalizar_titulo(c["titulo"])) for _, c in nac_heads)]

    voz_propia = [n for n in home if RE_VOZ_PROPIA.search(_norm_texto(n["titulo"]))]

    # Vencidas: previas/servicio cuando ya hay resultado (en la competencia o en la propia home)
    con_resultado = [(c["titulo"], normalizar_titulo(c["titulo"])) for _, c in nac_heads
                     if RE_RESULTADO.search(_norm_texto(c["titulo"]))]
    con_resultado += [(n["titulo"], hk) for n, hk in home_keys
                      if RE_RESULTADO.search(_norm_texto(n["titulo"]))]
    vencidas = []
    for n, hk in home_keys:
        if not RE_PREVIA.search(_norm_texto(n["titulo"])):
            continue
        ev = next((tit for tit, rk in con_resultado if tit != n["titulo"] and coincide_cobertura(hk, rk)), None)
        if ev:
            vencidas.append({"titulo": n["titulo"], "posicion": n["posicion"], "evidencia": ev})

    # Duplicados en la home: títulos casi iguales
    duplicados, vistos = [], []
    for n, hk in home_keys:
        par = next((m for m, mk in vistos if similitud_jaccard(hk, mk) >= 0.5), None)
        if par:
            duplicados.append({"titulo": n["titulo"], "posicion": n["posicion"], "igual_a": par["titulo"]})
        else:
            vistos.append((n, hk))

    # Mix por entidad: % de títulos de Olé vs % de títulos nacionales
    def _cuenta(titulos):
        c = {}
        for tt in titulos:
            for e in set(detectar_entidades(re.sub(r"[^\w\s]", " ", tt), dic)):
                c[e] = c.get(e, 0) + 1
        return c
    ole_c = _cuenta([n["titulo"] for n in home])
    nac_c = _cuenta([c["titulo"] for _, c in nac_heads])
    tot_o, tot_n = max(len(home), 1), max(len(nac_heads), 1)
    mix = sorted([{"entidad": e, "ole_pct": round(100 * ole_c.get(e, 0) / tot_o, 1),
                   "nac_pct": round(100 * v / tot_n, 1)} for e, v in nac_c.items()],
                 key=lambda m: -m["nac_pct"])[:8]
    desvio_top3 = max([abs(m["ole_pct"] - m["nac_pct"]) for m in mix[:3]] or [0])

    cobertura = round(100 * len(cubiertos) / max(len(agenda), 1), 1)
    return {
        "notas_home": len(home),
        "medios_ok": n_medios_ok,
        "titulos_nac": len(nac_heads),
        "temas_agenda": len(agenda),
        "cubiertos": len(cubiertos),
        "cobertura_pct": cobertura,
        "faltantes": faltantes_out,
        "enterrados": [e for e in enterrados if e["titulo"] not in {v["titulo"] for v in vencidas}],
        "commodity_pct": commodity_pct,
        "sin_par": [{"titulo": n["titulo"], "posicion": n["posicion"]} for n in sin_par],
        "voz_propia": [{"titulo": n["titulo"], "posicion": n["posicion"]} for n in voz_propia],
        "vencidas": vencidas,
        "duplicados": duplicados,
        "mix": mix,
        "desvio_top3": round(desvio_top3, 1),
    }


# ─── 3. LECTURA CON IA (opcional) ────────────────────────────────────────────
def prompt_lectura(a: dict, home: list) -> str:
    falt = "\n".join(f"- {f['titulo']} ({f['n_nac']} medios: {f['medios']})" for f in a["faltantes"][:15]) or "- ninguno"
    venc = "\n".join(f"- #{v['posicion']} {v['titulo']}  ← ya hay: {v['evidencia']}" for v in a["vencidas"][:10]) or "- ninguna"
    voz = "\n".join(f"- #{v['posicion']} {v['titulo']}" for v in a["voz_propia"][:10]) or "- ninguna"
    titulos = "\n".join(f"{n['posicion']}. {n['titulo']}" for n in home)
    return f"""Sos editor de Olé. Leé estos números de la home contra la agenda de {a['medios_ok']} medios nacionales.

Cobertura de agenda: {a['cobertura_pct']}% ({a['cubiertos']} de {a['temas_agenda']} temas que tienen {UMBRAL_AGENDA}+ medios)
Commodity: {a['commodity_pct']}% de la home son temas que tienen {UMBRAL_COMMODITY}+ medios
Desvío de mix en los 3 temas top: {a['desvio_top3']} puntos

TEMAS QUE FALTAN:
{falt}

POSIBLES NOTAS VENCIDAS:
{venc}

VOZ PROPIA:
{voz}

TODOS LOS TÍTULOS DE LA HOME, EN ORDEN:
{titulos}

Escribí en español rioplatense, texto plano para celular (sin #, ** ni ---):
FALTAN DE VERDAD: hasta 5 temas de la lista de faltantes, uno por línea, que la
home realmente no tiene. Antes de incluir uno, revisá la lista completa de
títulos: si la home lo tiene con otras palabras, es falso positivo y no va.
LECTURA: 3 o 4 líneas. Qué faltó que importa de verdad (ignorá falsos positivos
evidentes: temas que la home sí tiene con otras palabras), qué sobra o está
vencido, y una sola recomendación concreta para la próxima actualización.
ERRORES: errores de tipeo, tildes o palabras faltantes en los títulos de la home,
uno por línea con su número. Si no hay, escribí "ninguno". No inventes."""


def escribir_lectura(a: dict, home: list) -> str:
    try:
        import ia_motores
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not ia_motores.hay_motor(api_key):
            return ""
        txt = monitor_core.call_claude(prompt_lectura(a, home), api_key, max_tokens=900,
                                       modelo=monitor_core.MODELO_ECONOMICO)
        return txt.replace("**", "").replace("##", "").strip()
    except Exception as e:
        print(f"   IA no disponible: {str(e)[:80]}")
        return ""


# ─── 3b. EL DÍA + COBERTURA, LEÍDOS POR IA ───────────────────────────────────
# Las reglas de palabras se equivocan cuando Olé titula distinto ("Arranque de
# terror para Pochettino" vs "Liverpool goleó al United"). Acá la IA hace el
# cruce tema por tema, pero sólo puede citar números de tema y de título que le
# damos: todo lo que devuelve se puede verificar y lo inventado se descarta.
MAX_TEMAS_IA = 35


def temas_del_dia(resultados: dict) -> list:
    """Temas de la competencia (sin Olé), con al menos 2 medios nacionales
    o 1 nacional y 5+ en total. Ordenados por peso."""
    res = {k: v for k, v in resultados.items() if k != "ole"}
    nombre = {f["id"]: f["nombre"] for f in TODAS_FUENTES}
    out = []
    for c in calcular_tendencias(res):
        nac = sorted({x["fuente"]["id"] for x in c["noticias"]} & NAC_COMPARABLES)
        if len(nac) >= 2 or (nac and c["cant_medios"] >= 5):
            vistos, titulos = set(), []
            for x in c["noticias"]:
                t = x["noticia"]["titulo"]
                if t not in vistos and len(titulos) < 4:
                    vistos.add(t)
                    titulos.append({"titulo": t, "medio": x["fuente"]["nombre"],
                                    "url": x["noticia"].get("url") or ""})
            out.append({"n_nac": len(nac), "n_total": c["cant_medios"], "titulos": titulos,
                        "nac_ids": nac, "todos_ids": sorted(c["fuente_ids"]),
                        "medios": ", ".join(nombre.get(i, i) for i in nac),
                        "url": c.get("url") or ""})
    out.sort(key=lambda t: (-t["n_nac"], -t["n_total"]))
    for i, t in enumerate(out[:MAX_TEMAS_IA], 1):
        t["id"] = i
    return out[:MAX_TEMAS_IA]


def prompt_dia(temas: list, home: list, fecha: str) -> str:
    bloque_temas = "\n".join(
        f"T{t['id']} · {t['n_nac']} medios nacionales, {t['n_total']} en total\n"
        + "\n".join(f"   - [{x['medio']}] {x['titulo']}" for x in t["titulos"])
        for t in temas)
    bloque_home = "\n".join(f"H{n['posicion']}. {n['titulo']}" for n in home)
    return f"""Sos editor jefe de Olé, diario deportivo argentino. Hoy es {fecha}.
Te paso (A) los temas del día según la competencia, agrupados automáticamente
(T1 es el más cubierto), y (B) todos los títulos de la home de Olé en orden
(H1 es la apertura).

(A) TEMAS DE LA COMPETENCIA
{bloque_temas}

(B) HOME DE OLÉ
{bloque_home}

Devolvé SOLO un JSON válido, sin texto antes ni después, con esta forma:
{{
 "resumen": [{{"temas": [1, 7], "linea": "..."}}],
 "cobertura": [{{"tema": 1, "home": [3, 17]}}],
 "vencidas": [{{"home": 23, "motivo": "..."}}],
 "lectura": "...",
 "errores": [{{"home": 12, "error": "..."}}]
}}

Reglas, todas obligatorias:
1. "resumen": los 8 a 10 temas más importantes del día para un lector argentino,
   ordenados por importancia (no sólo por cantidad de medios). "temas": TODOS los
   T que cuentan esa misma historia (el agrupamiento automático a veces la parte
   en varios: juntalos acá). "linea": qué pasó, en una oración concreta con
   nombres y resultados que figuren en los títulos. Ignorá ruido (páginas de
   estadísticas, títulos sin noticia).
2. "cobertura": para CADA tema T del 1 al {len(temas)}, la lista de títulos H de
   la home que cuentan ESA MISMA historia, aunque usen otras palabras (un título
   de Olé sobre el mismo partido, la misma declaración o el mismo hecho cuenta;
   uno que sólo comparte el club o el jugador pero habla de otra cosa, NO).
   Lista vacía si la home no la tiene. Revisá la home entera antes de poner [].
3. "vencidas": títulos H que hablan en futuro o son servicio ("a qué hora",
   "cómo ver", "va por", "último momento", la formación) de algo que, según los
   títulos de (A) o de la propia home, YA pasó. "motivo": qué pasó, citando el
   título que lo prueba. Si no hay prueba en los títulos, no la incluyas. Un
   partido que se juega más tarde hoy NO está vencido.
4. "lectura": 3 o 4 oraciones en español rioplatense sobre cómo quedó parada la
   home frente al día: qué tema importante falta o está mal ubicado (con su H o T),
   qué se destaca como propio y una recomendación concreta para la próxima
   actualización. Prohibido lo genérico ("buena cobertura", "seguir así",
   "diversificar"). Cada afirmación tiene que poder chequearse en los datos.
5. "errores": errores de tipeo, tildes o palabras faltantes evidentes en los H.
   Si no hay, [].
No inventes hechos, números de tema ni de título que no estén arriba."""


def _json_de(texto: str):
    import json
    if not texto:
        return None
    t = texto.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(t[a:b + 1])
    except Exception:
        return None


def leer_dia_con_ia(temas: list, home: list, fecha: str, api_key: str = None):
    """Devuelve el JSON validado de la IA, o None si no hay IA o no respondió bien."""
    try:
        import ia_motores
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") if api_key is None else api_key
        if not ia_motores.hay_motor(api_key):
            return None
        crudo = monitor_core.call_claude(prompt_dia(temas, home, fecha), api_key, max_tokens=4000)
    except Exception as e:
        print(f"   IA no disponible: {str(e)[:80]}")
        return None
    d = _json_de(crudo)
    if not isinstance(d, dict):
        print("   La IA no devolvió un JSON válido.")
        return None
    ids_t = {t["id"] for t in temas}
    ids_h = {n["posicion"] for n in home}

    def _int(x):
        try:
            return int(str(x).lstrip("THth"))
        except Exception:
            return None

    out = {"resumen": [], "cobertura": {}, "vencidas": [], "errores": [],
           "lectura": str(d.get("lectura") or "").replace("**", "").strip()}
    for r in d.get("resumen") or []:
        if not isinstance(r, dict) or not r.get("linea"):
            continue
        crudos = r.get("temas") if isinstance(r.get("temas"), list) else [r.get("tema")]
        ks = [k for k in (_int(x) for x in crudos) if k in ids_t]
        if ks:
            out["resumen"].append({"temas": ks, "linea": str(r["linea"]).strip()})
    for c in d.get("cobertura") or []:
        k = _int(c.get("tema")) if isinstance(c, dict) else None
        if k in ids_t:
            out["cobertura"][k] = [h for h in (_int(x) for x in (c.get("home") or [])) if h in ids_h]
    for v in d.get("vencidas") or []:
        h = _int(v.get("home")) if isinstance(v, dict) else None
        if h in ids_h:
            out["vencidas"].append({"home": h, "motivo": str(v.get("motivo") or "").strip()})
    for e in d.get("errores") or []:
        h = _int(e.get("home")) if isinstance(e, dict) else None
        if h in ids_h and e.get("error"):
            out["errores"].append({"home": h, "error": str(e["error"]).strip()})
    if len(out["cobertura"]) < len(temas) * 0.6:   # contestó a medias: no confiable
        print(f"   La IA cubrió sólo {len(out['cobertura'])} de {len(temas)} temas.")
        return None
    return out


def combinar(a: dict, temas: list, ia: dict, home: list) -> dict:
    """Pisa faltantes, vencidas y cobertura del análisis por reglas con lo que
    leyó la IA. Deja el resto (voz propia, commodity, mix, duplicados)."""
    a = dict(a)
    tit = {n["posicion"]: n["titulo"] for n in home}
    por_id = {t["id"]: t for t in temas}
    es_agenda = lambda t: t["n_nac"] >= UMBRAL_AGENDA or (t["n_nac"] >= 2 and t["n_total"] >= UMBRAL_AGENDA_TOTAL)
    agenda = [t for t in temas if es_agenda(t)]
    cub = [t for t in agenda if ia["cobertura"].get(t["id"])]
    a["temas_agenda"], a["cubiertos"] = len(agenda), len(cub)
    a["cobertura_pct"] = round(100 * len(cub) / max(len(agenda), 1), 1)
    a["faltantes"] = [{"titulo": t["titulos"][0]["titulo"], "url": t["titulos"][0]["url"] or t["url"],
                       "n_nac": t["n_nac"], "n_total": t["n_total"], "medios": t["medios"]}
                      for t in agenda if not ia["cobertura"].get(t["id"])]
    a["vencidas"] = [{"titulo": tit[v["home"]], "posicion": v["home"], "evidencia": v["motivo"]}
                     for v in ia["vencidas"]]
    a["enterrados"] = []
    for t in agenda:
        hs = ia["cobertura"].get(t["id"]) or []
        if t["n_nac"] >= UMBRAL_COMMODITY and hs and min(hs) > ZONA_SCROLL:
            a["enterrados"].append({"titulo": tit[min(hs)], "posicion": min(hs), "n_nac": t["n_nac"]})
    a["resumen"] = []
    for r in ia["resumen"]:
        ts = [por_id[k] for k in r["temas"]]
        nac = set().union(*[set(t["nac_ids"]) for t in ts])
        tot = set().union(*[set(t["todos_ids"]) for t in ts])
        hs = sorted({h for t in ts for h in (ia["cobertura"].get(t["id"]) or [])})
        a["resumen"].append({"linea": r["linea"], "n_nac": len(nac), "n_total": len(tot),
                             "ole": f"H{hs[0]}" if hs else "no está", "ole_pos": hs[0] if hs else None,
                             "url": ts[0]["titulos"][0]["url"] or ts[0]["url"]})
    a["errores"] = [{"posicion": e["home"], "titulo": tit[e["home"]], "error": e["error"]} for e in ia["errores"]]
    a["lectura"] = ia["lectura"]
    a["leido_por_ia"] = True
    return a


def leer_todo(home: list, resultados: dict, dic: dict = None, api_key: str = None,
              fecha: str = "") -> dict:
    """Análisis completo: reglas + IA (si hay). Lo usan GitHub y el Streamlit."""
    a = analizar_home(home, resultados, dic)
    a.update({"resumen": [], "errores": [], "lectura": "", "leido_por_ia": False})
    temas = temas_del_dia(resultados)
    ia = leer_dia_con_ia(temas, home, fecha or datetime.now(_TZ_AR).strftime("%d/%m/%Y"), api_key)
    return combinar(a, temas, ia, home) if ia else a


# ─── 4. SALIDAS: planilla y Telegram ─────────────────────────────────────────
LECTURA_HEADERS = ["Fecha", "Hora", "NotasHome", "MediosNac", "TemasAgenda", "Cubiertos",
                   "Cobertura%", "Faltantes", "Enterrados", "VozPropia", "SinPar",
                   "Commodity%", "Vencidas", "Duplicados", "DesvioMix", "Lectura"]
DETALLE_HEADERS = ["Tipo", "Posición", "Título", "Medios", "Detalle", "URL"]


def guardar_planilla(a: dict, home: list, lectura: str, ahora: datetime) -> bool:
    import sheets_memoria as mem
    if not mem.disponible():
        return False
    fila = [ahora.strftime("%Y-%m-%d"), ahora.strftime("%H:%M"), a["notas_home"], a["medios_ok"],
            a["temas_agenda"], a["cubiertos"], a["cobertura_pct"], len(a["faltantes"]),
            len(a["enterrados"]), len(a["voz_propia"]), len(a["sin_par"]), a["commodity_pct"],
            len(a["vencidas"]), len(a["duplicados"]), a["desvio_top3"], lectura[:4000]]
    mem._ws("Lectura", LECTURA_HEADERS).append_row([str(x) for x in fila], value_input_option="USER_ENTERED")

    filas = [DETALLE_HEADERS]
    filas += [["Día", r["ole"], r["linea"], r["n_nac"], "", r["url"]] for r in a.get("resumen", [])]
    filas += [["Error", e["posicion"], e["titulo"], "", e["error"], ""] for e in a.get("errores", [])]
    filas += [["Falta", "", f["titulo"], f["n_nac"], f["medios"], f["url"]] for f in a["faltantes"]]
    filas += [["Enterrado", e["posicion"], e["titulo"], e["n_nac"], "tema grande, ubicado abajo", ""] for e in a["enterrados"]]
    filas += [["Vencida", v["posicion"], v["titulo"], "", f"ya hay: {v['evidencia']}", ""] for v in a["vencidas"]]
    filas += [["Duplicado", d["posicion"], d["titulo"], "", f"igual a: {d['igual_a']}", ""] for d in a["duplicados"]]
    filas += [["Voz propia", v["posicion"], v["titulo"], "", "", ""] for v in a["voz_propia"]]
    filas += [["Sin par", s["posicion"], s["titulo"], "", "ningún nacional lo tiene", ""] for s in a["sin_par"]]
    filas += [["Mix", "", m["entidad"], "", f"Olé {m['ole_pct']}% · nacionales {m['nac_pct']}%", ""] for m in a["mix"]]
    filas += [["Home", n["posicion"], n["titulo"], "", zona(n["posicion"]), n.get("url") or ""] for n in home]
    ws = mem._ws("Lectura detalle", DETALLE_HEADERS)
    ws.clear()
    ws.update(range_name="A1", values=[[str(c) for c in f] for f in filas], value_input_option="RAW")
    return True


def texto_telegram(a: dict, lectura: str, ahora: datetime, url_planilla: str = "") -> str:
    l = [f"🗞️ EL DÍA · {ahora.strftime('%d/%m %H:%M')}"]
    if a.get("resumen"):
        for r in a["resumen"]:
            marca = f"✅ {r['ole']}" if r["ole_pos"] else "❌ no está en la home"
            l.append(f"· {r['linea']} ({r['n_nac']} medios) {marca}")
    else:
        l.append("(sin IA no hay resumen del día)")
    l += ["", f"📰 LA HOME DE OLÉ",
          f"Cobertura de agenda: {a['cobertura_pct']}% (faltan {len(a['faltantes'])} de {a['temas_agenda']})",
          f"Vencidas: {len(a['vencidas'])} · Duplicados: {len(a['duplicados'])} · Títulos: {a['notas_home']}",
          f"Voz propia: {len(a['voz_propia'])} · Commodity: {a['commodity_pct']}%", ""]
    lectura = lectura or a.get("lectura", "")
    if lectura:
        l += [lectura, ""]
    if a["faltantes"]:
        l.append("FALTAN:" if a.get("leido_por_ia") else "FALTAN (automático, puede traer falsos positivos):")
        l += [f"· {f['titulo'][:90]} ({f['n_nac']} nac.)" for f in a["faltantes"][:6]]
        l.append("")
    if a["vencidas"]:
        l.append("VENCIDAS:")
        l += [f"· H{v['posicion']} {v['titulo'][:70]} → {v['evidencia'][:80]}" for v in a["vencidas"][:5]]
        l.append("")
    if a["enterrados"]:
        l.append("TEMAS GRANDES ABAJO:")
        l += [f"· H{e['posicion']} {e['titulo'][:90]}" for e in a["enterrados"][:4]]
        l.append("")
    if a.get("errores"):
        l.append("ERRORES EN TÍTULOS:")
        l += [f"· H{e['posicion']}: {e['error'][:90]}" for e in a["errores"][:5]]
        l.append("")
    if url_planilla:
        l.append(f"Detalle: {url_planilla}")
    return "\n".join(l)[:3900]


# ─── 5. CORRIDAS ─────────────────────────────────────────────────────────────
def main():
    ahora = datetime.now(_TZ_AR)
    import sheets_memoria as mem
    from vigia import scrapear_todo, enviar_telegram

    print("1) Scrapeando la home de Olé completa...")
    home, fuera = filtrar_home(scrapear_home_ole(), excluir_de_config())
    print(f"   {len(home)} notas en la home ({len(fuera)} excluidas por lectura_excluir)")
    if len(home) < 20:
        print("   Muy pocas notas: probablemente cambió la estructura de la home. Abortando.")
        sys.exit(1)

    print("2) Scrapeando la competencia...")
    resultados = scrapear_todo()

    dic = dic_entidades(mem.leer_config().get("entidades_extra", "") if mem.disponible() else "")
    print("3) Cruzando y leyendo el día con IA...")
    a = leer_todo(home, resultados, dic, fecha=ahora.strftime("%d/%m/%Y"))
    print(f"   leído por IA: {'sí' if a['leido_por_ia'] else 'no (sólo reglas)'}")
    print(f"   Cobertura {a['cobertura_pct']}% · faltan {len(a['faltantes'])} · "
          f"vencidas {len(a['vencidas'])} · duplicados {len(a['duplicados'])}")
    if a["medios_ok"] < 6:
        print(f"   Ojo: sólo respondieron {a['medios_ok']} medios nacionales; los números son poco confiables.")

    lectura = a.get("lectura", "")

    try:
        ok = guardar_planilla(a, home, lectura, ahora)
        print(f"5) Planilla: {'guardada → ' + mem.url_planilla() if ok else 'sin configurar (simulacro)'}")
        url = mem.url_planilla() if ok else ""
    except Exception as e:
        print(f"5) Planilla: ERROR {str(e)[:120]}")
        url = ""

    msg = texto_telegram(a, lectura, ahora, url)
    print("6) Telegram:\n" + msg)
    print("   enviado" if enviar_telegram(msg, html=False) else "   no enviado (sin bot configurado o error)")


def _desde_archivos(ruta_panorama: str, ruta_home: str):
    """Prueba sin red: Panorama Total exportado + home pegada (un título por línea)."""
    por_nombre = {f["nombre"]: f["id"] for f in TODAS_FUENTES}
    resultados, fid = {}, None
    for linea in open(ruta_panorama, encoding="utf-8"):
        m = re.match(r"=== (.+?) \(\d+ titulares\) ===", linea)
        if m:
            fid = por_nombre.get(m.group(1))
            continue
        if linea.startswith("═") or not linea.strip() or not fid:
            continue
        resultados.setdefault(fid, []).append({"titulo": linea.strip(), "url": ""})
    home = [{"titulo": t.strip(), "url": "", "posicion": i}
            for i, t in enumerate([x for x in open(ruta_home, encoding="utf-8") if x.strip()], 1)]
    a = analizar_home(home, resultados)
    print(texto_telegram(a, "", datetime.now(_TZ_AR)))
    print("\nFALTANTES:"); [print(f"  {f['n_nac']:2d} · {f['titulo']}  [{f['medios']}]") for f in a["faltantes"]]
    print("\nVENCIDAS:"); [print(f"  #{v['posicion']} {v['titulo']}\n      ← {v['evidencia']}") for v in a["vencidas"]]
    print("\nDUPLICADOS:"); [print(f"  #{d['posicion']} {d['titulo']}\n      = {d['igual_a']}") for d in a["duplicados"]]
    print("\nVOZ PROPIA:"); [print(f"  #{v['posicion']} {v['titulo']}") for v in a["voz_propia"]]
    print("\nMIX:"); [print(f"  {m['entidad']:20s} Olé {m['ole_pct']:5.1f}%  nac {m['nac_pct']:5.1f}%") for m in a["mix"]]
    return a


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--desde-archivos":
        _desde_archivos(sys.argv[2], sys.argv[3])
    else:
        main()
