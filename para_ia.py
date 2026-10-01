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


# ═══════════════ Informe de una categoría (sin Olé) y tema por palabra clave ═══════════════
from monitor_core import (FILTROS_TEMATICOS, FUENTES_INT, FUENTES_ESP, TODAS_FUENTES,  # noqa: E402
                          calcular_tendencias, _norm_texto)


def categorias() -> dict:
    """Categorías para el informe "qué pasa en…", como las secciones de los panoramas."""
    c = {"todo": "📰 Todo el panorama",
         "nac": "🇦🇷 Medios nacionales",
         "int": "🌍 Medios internacionales",
         "esp": "📡 Primicias e instituciones"}
    c.update({k: v["titulo"] for k, v in FILTROS_TEMATICOS.items()})
    c["palabras"] = "🔎 Club, nombre o palabras que elijas"
    return c


def recorte_categoria(resultados: dict, cat: str, palabras: str = "", con_ole: bool = False) -> dict:
    """Los titulares de una categoría, medio por medio (sin Olé, salvo que se pida)."""
    ids_int = {f["id"] for f in FUENTES_INT}
    ids_esp = {f["id"] for f in FUENTES_ESP}
    kws = None
    if cat in FILTROS_TEMATICOS:
        kws = [_norm_texto(k) for k in FILTROS_TEMATICOS[cat]["keywords"]]
    elif cat == "palabras":
        kws = [_norm_texto(k.strip()) for k in palabras.split(",") if k.strip()]
    out = {}
    for f in TODAS_FUENTES:
        fid = f["id"]
        if fid == "ole" and not con_ole:
            continue
        if cat == "nac" and fid not in FUENTES_NAC_IDS:
            continue
        if cat == "int" and fid not in ids_int:
            continue
        if cat == "esp" and fid not in ids_esp:
            continue
        notas = resultados.get(fid) or []
        if kws is not None:
            notas = [n for n in notas if any(k in _norm_texto(n.get("titulo", "")) for k in kws)]
        if notas:
            out[fid] = notas
    return out


def pedido_categoria(resultados: dict, cat: str, palabras: str = "", con_ole: bool = False,
                     max_por_medio: int = 15, max_historias: int = 80) -> str:
    """Informe de todo lo que pasa en una categoría, sin compararse con Olé.
    Las historias que comparten varios medios van primero; después, el resto medio por medio."""
    nombre = categorias().get(cat, cat)
    if cat == "palabras":
        nombre = f"🔎 {palabras.strip()}"
    rec = recorte_categoria(resultados, cat, palabras, con_ole)
    nombres = {f["id"]: f["nombre"] for f in TODAS_FUENTES}
    hist = calcular_tendencias(rec)[:max_historias]
    usados = set()
    lineas_h = []
    for k, t in enumerate(hist, 1):
        medios = sorted({n["fuente"]["nombre"] for n in t["noticias"]})
        otros, vistos = [], {frozenset(normalizar_titulo(t["titulo"]))}
        for n in t["noticias"]:
            usados.add((n["fuente"]["id"], n["noticia"]["titulo"]))
            kk = frozenset(normalizar_titulo(n["noticia"]["titulo"]))
            if kk not in vistos and len(otros) < 3:
                vistos.add(kk); otros.append(f"[{n['fuente']['nombre']}] {n['noticia']['titulo']}")
        linea = f"[H{k}] {len(medios)} medios · {t['titulo']}\n      Medios: {', '.join(medios)}"
        if otros:
            linea += "\n      Otros títulos: " + " | ".join(otros)
        lineas_h.append(linea)
    sueltas, total = [], 0
    for fid, notas in rec.items():
        resto = [n["titulo"] for n in notas if (fid, n["titulo"]) not in usados][:max_por_medio]
        if resto:
            sueltas.append(f"\n--- {nombres.get(fid, fid)} ---\n" + "\n".join(f"- {t}" for t in resto))
            total += len(resto)
    n_medios = len(rec)
    if not rec:
        material = "(no hay titulares cargados en esta categoría: probá con otra o actualizá las fuentes)"
    else:
        material = (f"=== HISTORIAS QUE PUBLICAN VARIOS MEDIOS ({len(lineas_h)}) ===\n"
                    + ("\n".join(lineas_h) or "(ninguna historia la tienen dos medios o más)")
                    + f"\n\n=== EL RESTO, MEDIO POR MEDIO ({total} titulares) ==="
                    + ("".join(sueltas) or "\n(nada más)"))
    return f"""Sos un periodista deportivo argentino que le cuenta a la redacción, de forma clara y ordenada, todo lo que está pasando en esta categoría: {nombre}. Abajo están los titulares de hoy ({_fecha()}) de {n_medios} medios.

Armá un informe con estas partes:
1. EN 5 LÍNEAS: lo más importante que está pasando.
2. TEMA POR TEMA: de la historia que más medios tienen a la que menos. Para cada una: qué se sabe, qué es rumor o versión de un solo medio, si hay versiones distintas entre medios, y qué viene. Citá los medios entre corchetes.
3. LO QUE PUEDE CRECER: temas chicos que conviene mirar en las próximas horas.
4. PREGUNTAS ABIERTAS: lo que todavía no se sabe y habría que chequear.
5. NOTAS POSIBLES: 5 títulos con su ángulo, cada uno con las historias [H…] de las que sale.
Después, quedate esperando mis preguntas sobre este material.

{_reglas()}

{material}"""


def pedido_tema(tema: str, notas_enriquecidas: list) -> str:
    """Informe de un tema a partir de varias notas (con su texto): todo lo que se sabe,
    lo confirmado, lo que no, las versiones y las declaraciones."""
    con = [t for t in notas_enriquecidas if t.get("ok")]
    sin = [t for t in notas_enriquecidas if not t.get("ok")]
    medios = sorted({t["fuente"]["nombre"] for t in notas_enriquecidas})
    bloque = "\n\n".join(
        f"── [{k}] {t['fuente']['nombre']} — {t['noticia']['titulo']}\nLink: {t['noticia'].get('url') or '(sin link)'}\nTEXTO:\n{t['cuerpo']}"
        for k, t in enumerate(con, 1))
    titulos = "\n".join(f"  • [{t['fuente']['nombre']}] {t['noticia']['titulo']}" for t in sin)
    material = ""
    if con:
        material += f"=== NOTAS CON TEXTO ({len(con)}) ===\n{bloque}"
    if titulos:
        material += f"\n\n=== SOLO TÍTULOS ({len(sin)}) ===\n{titulos}"
    return f"""Sos un periodista deportivo argentino. Te paso {len(notas_enriquecidas)} notas de {len(medios)} medio(s) sobre este tema: {tema}. Hoy es {_fecha()}.

Armá un INFORME DEL TEMA con estas partes:
1. EN 3 LÍNEAS: qué está pasando.
2. LO CONFIRMADO: los hechos oficiales o que publican varios medios, cada uno con el medio entre corchetes.
3. LO QUE NO ESTÁ CONFIRMADO: rumores, trascendidos y lo que publica un solo medio, con quién lo dice.
4. VERSIONES DISTINTAS: dónde no coinciden los medios.
5. DECLARACIONES: las frases textuales, con quién las dijo y dónde.
6. CRONOLOGÍA: el orden de los hechos, si el material tiene fechas u horarios.
7. QUÉ FALTA SABER: las preguntas que habría que chequear antes de publicar.
8. NOTAS POSIBLES: 3 títulos con su ángulo.
Después, quedate esperando mis pedidos (por ejemplo: "escribí la nota", "haceme 5 títulos", "resumilo para redes").

REGLAS: usá solo el material de abajo; no inventes datos, cifras, resultados, fechas ni declaraciones. Separá siempre lo confirmado de lo que no. Español rioplatense, directo.{bloque_criterios()}

=== MATERIAL ===
{material}"""


# ═══════════════ Pedido de nota para pegar en ChatGPT o Claude ═══════════════
LARGOS_CHAT = {
    "Nota completa": "entre 400 y 600 palabras, con un primer párrafo suelto y después 2 o 3 intertítulos concretos y periodísticos (\"La lesión y los plazos\", nunca \"Contexto\")",
    "Nota breve": "entre 150 y 250 palabras, en 3 o 4 párrafos, sin intertítulos",
}
ESTILOS_CHAT = {
    "Informativa": "informativa, con el tono de Olé: futbolera, ágil y directa, sin opinión",
    "Analítica": "analítica: además de qué pasó, por qué importa, qué cambia y qué viene, siempre con lo que dice el material y sin opinión propia",
    "Urgente/Flash": "urgente: un despacho corto (hasta 120 palabras) donde la primera oración cuenta toda la noticia",
}


def pedido_nota_chat(tema: str, notas_enriquecidas: list, estilo: str = "Informativa",
                     tipo: str = "Nota completa", contexto: str = "") -> str:
    """El pedido para que ChatGPT o Claude escriban una nota lista para Olé.
    A diferencia de la nota que se escribe dentro de la app, acá la IA muestra todo su trabajo
    en pasos (inventario, lo que no sabemos, ángulo, nota, control) y termina con la versión
    final lista para copiar y los datos para la web."""
    if tipo not in LARGOS_CHAT:
        # Titulares o esqueleto: sirve el pedido de siempre
        from monitor_core import prompt_nota_rapida
        return prompt_nota_rapida(tema, notas_enriquecidas, estilo, tipo, contexto)
    con = [t for t in notas_enriquecidas if t.get("ok")]
    sin = [t for t in notas_enriquecidas if not t.get("ok")]
    medios = sorted({t["fuente"]["nombre"] for t in notas_enriquecidas})
    largo = "hasta 120 palabras, en 2 o 3 párrafos cortos" if estilo == "Urgente/Flash" else LARGOS_CHAT[tipo]
    bloque = "\n\n".join(
        f"### [{k}] {t['fuente']['nombre']} — {t['noticia']['titulo']}\nLink: {t['noticia'].get('url') or '(sin link)'}\n\n{t['cuerpo']}"
        for k, t in enumerate(con, 1))
    titulos = "\n".join(f"- [{t['fuente']['nombre']}] {t['noticia']['titulo']}" for t in sin)
    material = ""
    if con:
        material += f"## NOTAS COMPLETAS ({len(con)})\n\n{bloque}"
    else:
        material += "(No se pudo leer el texto de ninguna nota: trabajá con los títulos, avisá que el material es limitado y usá [COMPLETAR] donde falte.)"
    if titulos:
        material += f"\n\n## OTROS TÍTULOS SOBRE ESTA HISTORIA ({len(sin)}) — solo sirven para saber qué publicó cada medio\n{titulos}"
    if contexto:
        material += (f"\n\n## LO QUE APORTA EL REDACTOR DE OLÉ\n{contexto}\n"
                     "(Esto es información propia de Olé: podés usarlo como dato CONFIRMADO, con fuente \"Redactor\".)")

    return f"""Sos redactor de Olé, el diario deportivo argentino. Tenés que escribir una nota lista para publicar en la web de Olé, para lectores argentinos, sobre esta historia: {tema}.
Usá ÚNICAMENTE el material de abajo: lo que publicaron {len(medios)} medio(s) ({len(con)} nota(s) completa(s) y {len(sin)} título(s) más).
Hoy es {_fecha()}: tenelo en cuenta para "hoy", "ayer" y "mañana".

ANTES DE EMPEZAR
- No busques en internet ni uses otras fuentes. Si tenés la búsqueda web activada, no la uses: trabajá solo con este material.
- LA REGLA MÁS IMPORTANTE: NO AGREGUES NADA QUE NO ESTÉ EN EL MATERIAL. Ni datos, ni cifras, ni fechas, ni edades, ni estadísticas, ni resultados, ni antecedentes, ni contexto histórico, ni declaraciones, ni nombres, aunque creas saberlos. Tu memoria puede estar desactualizada y un dato falso publicado es un error grave. Si para que la nota quede completa hace falta un dato que no está, escribí [COMPLETAR: qué dato falta] en ese lugar y seguí.

Trabajá EN PASOS, en este orden, y mostrá cada paso con su título.

PASO 1 · INVENTARIO DE DATOS
Numerá cada dato utilizable del material: [D1], [D2], etc. Para cada uno: el dato, el medio que lo publica y si es CONFIRMADO (oficial, declaración pública o lo dicen varios medios) o ATRIBUIDO (un solo medio, una fuente anónima o un trascendido). Copiá las declaraciones textuales tal cual, con quién las dijo; si están en otro idioma, traducilas y marcalas como traducción. Si dos medios dicen cosas distintas sobre un mismo dato, anotá las dos versiones.

PASO 2 · LO QUE NO SABEMOS
La lista de lo que la nota NO puede afirmar porque el material no lo dice (por ejemplo: cifras, fechas, si ya firmó, qué dijo el club). Esto no puede aparecer como hecho en la nota.

PASO 3 · EL ÁNGULO
Elegí UN ángulo y explicá en dos líneas por qué. El título compite por el significado, no por la información; el primer párrafo instala el ángulo, no la crónica. Usá el ángulo argentino (un club, un jugador argentino, la Selección) solo si está en el material; si la historia es del exterior y no lo tiene, no lo fuerces.
{FRAMEWORK_ANGULOS}

PASO 4 · LA NOTA
- Tres opciones de título: cortos (hasta 12 palabras), directos, con fuerza y en el estilo de Olé, con el nombre propio adelante cuando ayude a encontrarla en Google. Ninguno puede prometer lo que el cuerpo no sostiene. Marcá el que elegís.
- Bajada: una o dos oraciones con el dato principal.
- Cuerpo: {largo}. Estilo {ESTILOS_CHAT.get(estilo, ESTILOS_CHAT["Informativa"])}. Arrancá con lo más fuerte y nuevo; después los detalles y las declaraciones; cerrá con lo que viene, solo si el material lo dice.
- La nota es de Olé: NO nombres a los medios de donde sale la información. Los medios y periodistas del inventario son solo para tu control.
- Escribí con tus propias palabras: no copies oraciones ni párrafos de las notas del material.
- Lo CONFIRMADO va como hecho. Lo ATRIBUIDO nunca va como hecho: usá "trascendió que", "en el club aseguran", "habría", "estaría", sin nombrar al medio. Nunca escribas "pudo saber Olé" ni presentes nada como averiguación propia. Si hay versiones distintas, contá las dos sin decir quién publicó cada una.
- Las citas textuales se atribuyen a la persona que las dijo ("dijo Gallardo en conferencia"), nunca al medio. Si salen de una entrevista con otro medio, poné "en una entrevista", sin nombrarlo.
- Español rioplatense. Clubes como los nombra la prensa argentina: River, Boca, Racing, San Lorenzo, Independiente, Huracán, Vélez, Lanús. "La Selección" (no "la Albiceleste"). Jugadores por apellido desde la segunda mención, sin apodos. Cargos en minúscula ("el entrenador Scaloni").
- Párrafos cortos (hasta 60 palabras). Sin relleno, sin frases hechas ("en este contexto", "cabe destacar", "a su vez") y sin adjetivos que valoren lo que el material no valora ("histórico", "increíble").{bloque_criterios()}

PASO 5 · CONTROL
Recorré la nota oración por oración y mostrá una lista: cada afirmación con el dato del inventario que la sostiene ([D3], [D7]…). Si una afirmación no tiene respaldo, sacala o reemplazala por [COMPLETAR]. Revisá que los títulos no digan más que el cuerpo, que lo ATRIBUIDO esté en condicional o con "trascendió", que no aparezca el nombre de ningún medio y que no haya oraciones copiadas del material. Después listá todos los [COMPLETAR] que quedaron.

PASO 6 · VERSIÓN FINAL
La nota corregida después del control, completa y lista para copiar y pegar, en un solo bloque: título elegido, bajada y cuerpo (con sus intertítulos, si lleva). Sin las referencias [D…] ni comentarios tuyos.

PASO 7 · PARA LA WEB Y REDES
- Título SEO (hasta 70 caracteres) y descripción para Google (hasta 155 caracteres).
- 5 etiquetas (nombres propios y temas).
- Un texto para redes (hasta 280 caracteres).
Todo sin agregar datos que no estén en la nota.

Al final, preguntame si quiero otro ángulo, una versión más corta o cambiar el título.

=== MATERIAL ===

{material}
"""


# ═══════════════ Los 30 temas del deporte (todos los titulares, sin mirar a Olé) ═══════════════
def pedido_30_temas(resultados: dict, alcance: str = "todo", con_ole: bool = True, cantidad: int = 30,
                    max_por_medio: int = 30) -> str:
    """Todos los titulares nacionales e internacionales para que la IA los agrupe en los
    temas principales del deporte. Olé entra como un medio más (o se saca), sin comparar."""
    ids_int = {f["id"] for f in FUENTES_INT}
    bloques, total, medios = [], 0, 0
    for f in TODAS_FUENTES:
        fid = f["id"]
        if fid == "ole" and not con_ole:
            continue
        if alcance == "nac" and fid not in FUENTES_NAC_IDS and fid not in FUENTES_ESP_IDS:
            continue
        if alcance == "int" and fid not in ids_int:
            continue
        vistos, lineas = set(), []
        for n in (resultados.get(fid) or []):
            k = frozenset(normalizar_titulo(n.get("titulo", "")))
            if not k or k in vistos:
                continue
            vistos.add(k); lineas.append(n["titulo"])
            if len(lineas) >= max_por_medio:
                break
        if lineas:
            origen = "internacional" if fid in ids_int else "nacional"
            bloques.append(f"\n--- {f['nombre']} ({origen}) ---\n" + "\n".join(f"- {t}" for t in lineas))
            total += len(lineas); medios += 1
    nombre_alc = ALCANCES.get(alcance, ALCANCES["todo"])
    return f"""Sos editor de deportes. Abajo están TODOS los titulares de hoy ({_fecha()}) de {medios} medios ({nombre_alc.lower()}), medio por medio: {total} titulares en total. Quiero entender qué está pasando en el deporte, sin importar qué medio lo publica.

Agrupá los titulares en los {cantidad} TEMAS PRINCIPALES, ordenados del que más medios tiene al que menos. Un tema es una misma historia o asunto (un partido, un pase, una lesión, una polémica, un torneo), aunque cada medio lo titule distinto.

Para cada tema:
1. Nombre del tema (corto) y deporte o competencia.
2. Cuántos medios lo tienen y cuáles; aclará si es más nacional, más internacional o de los dos.
3. Qué pasa, en dos o tres oraciones: lo que se sabe y lo que es rumor o versión de un solo medio.
4. Dos o tres títulos de ejemplo, textuales, con el medio entre corchetes.

Después de los {cantidad}:
- OTROS TEMAS: los que quedaron afuera, en una línea cada uno.
- LO QUE MIRA EL MUNDO Y LO QUE MIRA LA ARGENTINA: qué temas dominan en los medios internacionales y cuáles en los nacionales, y cuáles comparten.
- PARA SEGUIR: 5 temas que pueden crecer en las próximas horas.

REGLAS: usá solo estos titulares; no inventes resultados, cifras, nombres ni datos que no estén. Si un título está en otro idioma, escribí el tema en español. Si dos títulos parecen del mismo tema pero no estás seguro, separalos. Español rioplatense, claro y sin relleno. Si el texto es muy largo para leerlo de una vez, avisame antes de responder.

=== TITULARES, MEDIO POR MEDIO ==={''.join(bloques) or chr(10) + '(no hay titulares cargados: actualizá las fuentes)'}"""
