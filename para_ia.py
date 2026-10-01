"""
Pedidos para pegar en ChatGPT, Claude o Gemini (gratis, sin API key).

Arma un texto con instrucciones + material (las historias agrupadas, con cuántos
medios las tienen y si Olé está) para que lo lleves a cualquier IA de chat.
No llama a ninguna IA: solo junta y ordena lo que ya cargó el monitor.
"""
from datetime import datetime

from monitor_core import (FRAMEWORK_ANGULOS, FUENTES_NAC, FUENTES_NAC_IDS, FUENTES_ESP_IDS,
                          bloque_criterios, es_tema_de_pases, normalizar_titulo,
                          notas_exterior_relevantes, exportar_panorama_total)

TIPOS_INFORME = {
    "parte": ("📋 Parte del día", "Qué domina la agenda, qué le falta a Olé, qué tiene solo Olé y 5 notas propuestas con ángulo."),
    "competencia": ("⭐ Olé vs. la competencia", "Dónde llega Olé, dónde no, qué tiene en exclusiva y cómo titula cada medio."),
    "pases": ("🔁 Mercado de pases", "Tablero de pases por club: quién, en qué estado está cada negociación y con qué nivel de certeza."),
    "exterior": ("🌍 Argentinos en el mundo", "Qué dice la prensa internacional de los argentinos y del fútbol argentino, y notas posibles."),
    "libre": ("💬 Panorama para preguntar", "Todos los titulares del día: lo pegás y después le hacés las preguntas que quieras."),
}
ALCANCES = {"todo": "Todos los medios", "nac": "Solo nacionales", "int": "Solo internacionales"}


def _fecha() -> str:
    try:
        from zoneinfo import ZoneInfo
        a = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    except Exception:
        a = datetime.now()
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    return f"{dias[a.weekday()]} {a.strftime('%d/%m/%Y, %H:%M')} (hora argentina)"


def _en_alcance(fid: str, alcance: str) -> bool:
    if alcance == "nac":
        return fid in FUENTES_NAC_IDS or fid in FUENTES_ESP_IDS
    if alcance == "int":
        return fid not in FUENTES_NAC_IDS and fid not in FUENTES_ESP_IDS or fid == "ole"
    return True


def _historias(tendencias: list, alcance: str, filtro=None, maximo: int = 70) -> list:
    """Las historias (clusters) en formato compacto para la IA."""
    out = []
    for t in tendencias or []:
        notas = [n for n in t["noticias"] if _en_alcance(n["fuente"]["id"], alcance)]
        medios = sorted({n["fuente"]["nombre"] for n in notas})
        otros_medios = {n["fuente"]["id"] for n in notas if n["fuente"]["id"] != "ole"}
        if not otros_medios or (alcance == "todo" and len(medios) < 2):
            continue
        if filtro and not filtro(t):
            continue
        vistos, otros = {frozenset(normalizar_titulo(t["titulo"]))}, []
        for n in notas:
            k = frozenset(normalizar_titulo(n["noticia"]["titulo"]))
            if k not in vistos and len(otros) < 3:
                vistos.add(k); otros.append(f"[{n['fuente']['nombre']}] {n['noticia']['titulo']}")
        tiene_ole = any(n["fuente"]["id"] == "ole" for n in t["noticias"])
        linea = (f"[H{len(out) + 1}] {len(medios)} medio{'s' if len(medios) != 1 else ''} · Olé: {'SÍ' if tiene_ole else 'NO'} · {t['titulo']}\n"
                 f"      Medios: {', '.join(medios)}")
        if otros:
            linea += "\n      Otros títulos: " + " | ".join(otros)
        out.append(linea)
        if len(out) >= maximo:
            break
    return out


def _titulos_ole(resultados: dict, maximo: int = 50) -> list:
    return [n["titulo"] for n in (resultados.get("ole") or [])[:maximo]]


def _reglas() -> str:
    return ("REGLAS: trabajá solo con los titulares de abajo; no inventes datos, resultados, cifras ni nombres "
            "que no estén. Si algo es un rumor (lo tiene un solo medio o el título habla en condicional), decilo. "
            "Español rioplatense, directo y sin relleno. Usá los números [H…] para referirte a las historias."
            + bloque_criterios())


def pedido_informe(resultados: dict, tendencias: list, ole_analisis: dict, tipo: str = "parte",
                   alcance: str = "todo") -> str:
    """Texto completo (instrucciones + material) para pegar en ChatGPT, Claude o Gemini."""
    fecha = _fecha()
    nombre_alc = ALCANCES.get(alcance, ALCANCES["todo"])
    ole = _titulos_ole(resultados)
    excl = [n["titulo"] for n in (ole_analisis or {}).get("exclusivos_ole", [])[:20]]

    if tipo == "libre":
        material = exportar_panorama_total(resultados)
        return f"""Sos un asistente de la redacción de Olé, el diario deportivo argentino. Abajo está el panorama completo de titulares de hoy ({fecha}), medio por medio.

Leelo entero y respondé mis preguntas usando SOLO estos titulares. Si algo no está, decí que no está; si es un rumor, aclaralo. Cuando cites algo, decí de qué medio sale. Empezá con un resumen de 5 líneas de lo más importante y esperá mis preguntas.

{material}"""

    if tipo == "exterior":
        notas = notas_exterior_relevantes(resultados, max_items=80)
        lineas = "\n".join(f"[{n['fuente']['nombre']}] {n['titulo']}" for n in notas) or "(no hay notas del exterior con impacto argentino)"
        return f"""Sos editor de Olé, el diario deportivo argentino. Abajo están los titulares de medios internacionales de hoy ({fecha}) que mencionan a jugadores, técnicos o clubes argentinos (entre corchetes, el medio).

Armá un informe con estas partes:
1. EN UNA LÍNEA: cómo ve hoy la prensa del mundo al fútbol argentino.
2. LOS ARGENTINOS DEL DÍA: por jugador o técnico, qué dicen de él, en qué medios y con qué tono (elogio, crítica, neutro, rumor), con un título textual de prueba.
3. POR PAÍS: qué prensa elogia y cuál pega.
4. RUMORES Y MERCADO: lo que vincula a argentinos con otros clubes, aclarando que es lo que publica cada medio.
5. 5 NOTAS PARA OLÉ: título, ángulo y de qué titulares sale cada una.

{_reglas()}

=== TITULARES DEL EXTERIOR CON IMPACTO ARGENTINO ({len(notas)}) ===
{lineas}"""

    filtro = (lambda t: es_tema_de_pases(t["titulo"]) or any(es_tema_de_pases(n["noticia"]["titulo"]) for n in t["noticias"])) \
        if tipo == "pases" else None
    hist = _historias(tendencias, alcance, filtro, maximo=90 if tipo == "pases" else 70)
    bloque_hist = "\n".join(hist) or "(no hay historias que compartan varios medios con ese filtro)"
    bloque_ole = "\n".join(f"- {t}" for t in ole) or "(sin titulares de Olé cargados)"
    bloque_excl = "\n".join(f"- {t}" for t in excl) or "(ninguno detectado)"
    material = f"""=== HISTORIAS DEL DÍA ({len(hist)}) — {nombre_alc} ===
Cada historia: cuántos medios la tienen, si Olé la tiene, el título principal, qué medios y cómo la titularon otros.
{bloque_hist}

=== LO QUE TIENE OLÉ AHORA (sus últimos títulos) ===
{bloque_ole}

=== LO QUE TIENE SOLO OLÉ (exclusivos detectados) ===
{bloque_excl}"""

    if tipo == "competencia":
        instrucciones = """Sos analista de medios de Olé, el diario deportivo argentino. Compará la cobertura de Olé con la de la competencia y armá un informe con estas partes:
1. RESUMEN: en tres líneas, cómo está parado Olé hoy frente a los demás.
2. HUECOS: las historias que tienen varios medios y Olé no (Olé: NO), de la más importante a la menos, con una línea de por qué importa y un ángulo para entrar tarde pero distinto.
3. DONDE OLÉ LLEGA: las historias compartidas; compará el título de Olé con los de los otros (más claro, más atractivo, más completo) y proponé un título mejor cuando haga falta.
4. EXCLUSIVOS: lo que tiene solo Olé y cómo empujarlo (redes, segunda vuelta, seguimiento).
5. PATRONES: qué temas o tipos de nota trabaja más la competencia que Olé."""
    elif tipo == "pases":
        instrucciones = """Sos el especialista en mercado de pases de Olé, el diario deportivo argentino. Con las historias de abajo (filtradas por mercado de pases), armá un TABLERO DE PASES:
1. POR CLUB (empezá por los argentinos grandes): cada operación en una línea con jugador, de dónde a dónde, ESTADO (rumor / sondeo / negociación / acuerdo / confirmado) y NIVEL DE CERTEZA (alto si lo tienen varios medios y hay fuente oficial; bajo si es un solo medio o está en condicional), con cuántos medios lo publican.
2. LO MÁS CALIENTE: las 5 operaciones que más se mueven hoy y por qué.
3. LO QUE OLÉ NO TIENE: operaciones que publican otros y Olé no.
4. CONTRADICCIONES: cuando un medio dice una cosa y otro otra.
5. 3 NOTAS PARA OLÉ: título y ángulo, sin dar por hecho lo que no está confirmado."""
    else:
        instrucciones = f"""Sos el editor jefe de Olé, el diario deportivo argentino. Con el panorama de abajo, armá el PARTE DEL DÍA:
1. LO QUE DOMINA: las 5 historias más fuertes, cada una con por qué le importa al hincha (no cuántos medios la tienen).
2. HUECOS DE OLÉ: lo que tienen varios medios y Olé no (Olé: NO), ordenado por urgencia, con un ángulo para cada uno.
3. PARA EMPUJAR: lo que tiene solo Olé y cómo aprovecharlo.
4. LO QUE CRECE: temas que pueden explotar en las próximas horas.
5. 5 NOTAS PROPUESTAS: título filoso, ángulo (del framework de abajo), historias de las que sale [H…] y qué dato hay que chequear antes de publicar.

{FRAMEWORK_ANGULOS}"""

    return f"""{instrucciones}

Hoy es {fecha}. Alcance: {nombre_alc}.
{_reglas()}

{material}"""
