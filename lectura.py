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
    l = [f"📰 LECTURA HOME OLÉ · {ahora.strftime('%d/%m %H:%M')}",
         f"Cobertura de agenda: {a['cobertura_pct']}% (faltan {len(a['faltantes'])} de {a['temas_agenda']})",
         f"Vencidas: {len(a['vencidas'])} · Duplicados: {len(a['duplicados'])} · Notas en home: {a['notas_home']}",
         f"Voz propia: {len(a['voz_propia'])} · Sin par en la competencia: {len(a['sin_par'])}",
         f"Commodity: {a['commodity_pct']}% · Desvío de mix: {a['desvio_top3']} pts", ""]
    if a["faltantes"] and not lectura:   # con IA, la lista filtrada viene en la lectura
        l.append("FALTAN (automático, puede traer falsos positivos):")
        l += [f"· {f['titulo'][:90]} ({f['n_nac']} nac. / {f['n_total']} total)" for f in a["faltantes"][:6]]
        l.append("")
    if a["vencidas"]:
        l.append("VENCIDAS:")
        l += [f"· #{v['posicion']} {v['titulo'][:90]}" for v in a["vencidas"][:5]]
        l.append("")
    if a["enterrados"]:
        l.append("TEMAS GRANDES ABAJO:")
        l += [f"· #{e['posicion']} {e['titulo'][:90]}" for e in a["enterrados"][:4]]
        l.append("")
    if lectura:
        l += [lectura, ""]
    if url_planilla:
        l.append(f"Detalle: {url_planilla}")
    return "\n".join(l)[:3900]


# ─── 5. CORRIDAS ─────────────────────────────────────────────────────────────
def main():
    ahora = datetime.now(_TZ_AR)
    import sheets_memoria as mem
    from vigia import scrapear_todo, enviar_telegram

    print("1) Scrapeando la home de Olé completa...")
    home = scrapear_home_ole()
    print(f"   {len(home)} notas en la home")
    if len(home) < 20:
        print("   Muy pocas notas: probablemente cambió la estructura de la home. Abortando.")
        sys.exit(1)

    print("2) Scrapeando la competencia...")
    resultados = scrapear_todo()

    dic = dic_entidades(mem.leer_config().get("entidades_extra", "") if mem.disponible() else "")
    a = analizar_home(home, resultados, dic)
    print(f"3) Cobertura {a['cobertura_pct']}% · faltan {len(a['faltantes'])} · "
          f"vencidas {len(a['vencidas'])} · duplicados {len(a['duplicados'])}")
    if a["medios_ok"] < 6:
        print(f"   Ojo: sólo respondieron {a['medios_ok']} medios nacionales; los números son poco confiables.")

    print("4) Lectura con IA...")
    lectura = escribir_lectura(a, home)
    print("   " + (lectura[:300] or "(sin IA: va sólo con números)"))

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
