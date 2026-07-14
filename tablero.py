# -*- coding: utf-8 -*-
"""🚦 TABLERO PREDICTIVO — app independiente del monitor.
Lee todo de la planilla (no scrapea): estado del dataset de métricas,
entrenamiento del semáforo, y el panorama del día clasificado 🟢🟡🔴.
Se despliega como segunda app de Streamlit desde este mismo repo
(main file: tablero.py) con los mismos secrets que la app principal.
"""
import streamlit as st
from collections import Counter

st.set_page_config(page_title="Tablero Predictivo", page_icon="🚦", layout="wide")

from monitor_core import entrenar_semaforo, predecir_semaforo  # noqa: E402
import sheets_memoria  # noqa: E402

st.title("🚦 Tablero Predictivo")
st.caption("El círculo de resultados: cuántos datos hay, qué tan bien predice el modelo, y el panorama de hoy semaforizado. No scrapea: lee la planilla que alimentan el vigía y los reportes diarios.")

if not sheets_memoria.disponible():
    st.error("Sin conexión con la planilla. Verificá que esta app tenga los mismos Secrets que la principal (Settings → Secrets).")
    st.stop()

# ─── 1) SALUD DEL DATASET ─────────────────────────────────────────────────────
st.header("📊 El dataset")
metricas = sheets_memoria.leer_metricas()
if not metricas:
    st.info("Todavía no hay métricas guardadas. Subí reportes diarios desde la app principal (pestaña 📈 Resultados).")
    st.stop()

por_fecha = Counter(m.get("Fecha", "") for m in metricas if m.get("Fecha"))
fechas = sorted(por_fecha.keys(), key=lambda f: (f.split("/")[1] if "/" in f else "0",
                                                 f.split("/")[0]))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Notas acumuladas", f"{len(metricas):,}".replace(",", "."))
c2.metric("Días cargados", len(fechas))
c3.metric("Promedio por día", round(len(metricas) / max(len(fechas), 1)))
umbral = "🟢 listo" if len(metricas) >= 500 else ("🟡 preliminar" if len(metricas) >= 150 else "🔴 juntando")
c4.metric("Estado para entrenar", umbral)

with st.expander("Detalle por día (para detectar huecos o días flacos)"):
    for f in fechas:
        n = por_fecha[f]
        barra = "█" * min(n // 4, 20)
        alerta = "  ⚠️ día flaco (¿faltó re-subir?)" if n < 40 else ""
        st.text(f"{f}  {barra} {n}{alerta}")

st.markdown("---")

# ─── 2) EL MODELO ────────────────────────────────────────────────────────────
st.header("🧠 El modelo")
if st.button("Entrenar / actualizar con los datos de hoy", type="primary"):
    with st.spinner("Entrenando..."):
        pack = entrenar_semaforo(metricas)
        if "error" in pack:
            st.warning(pack["error"])
        else:
            st.session_state.pack = pack
            sheets_memoria.guardar_evolucion(pack, len(metricas))

pack = st.session_state.get("pack")
if pack:
    mejora = pack["acc"] - pack["acc_base"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Precisión", f"{pack['acc']:.0%}")
    m2.metric("Base (sin modelo)", f"{pack['acc_base']:.0%}")
    m3.metric("Mejora real", f"{mejora:+.0%}")
    m4.metric("Algoritmo ganador", "Random Forest" if pack["tipo"] == "rf" else "Reg. logística")
    if mejora < 0.05:
        st.warning("⚠️ El modelo todavía no le gana claro a la base. Con más días de datos, los patrones emergen — no lo uses para decidir aún.")
    elif pack.get("preliminar"):
        st.info("Modo preliminar (menos de 500 notas): orientativo.")
    st.caption("⬆️ Lo que empuja a 🟢: " + " · ".join(n for n, _ in pack["factores_verde"][:7]))
    st.caption("⬇️ Lo que frena: " + " · ".join(n for n, _ in reversed(pack["frena_verde"])))
    st.caption(f"Competencia interna: logística {pack['acc_logit']:.0%} vs Random Forest {pack['acc_rf']:.0%} — manda el mejor; las razones las explica siempre la logística.")

    evol = sheets_memoria.leer_evolucion()
    if evol:
        with st.expander(f"📈 Evolución del modelo ({len(evol)} entrenamientos registrados)"):
            st.text(f"{'Fecha':<12}{'Notas':>8}{'Base':>7}{'Logít.':>8}{'R.Forest':>10}{'Mejora':>8}  Ganador")
            for e in evol[-20:]:
                st.text(f"{e.get('Fecha',''):<12}{e.get('NotasDataset',''):>8}{e.get('Base',''):>7}"
                        f"{e.get('Logistica',''):>8}{e.get('RandomForest',''):>10}{e.get('Mejora',''):>8}  {e.get('Ganador','')}")
else:
    st.info("Entrená el modelo para habilitar el semáforo de abajo.")

st.markdown("---")

# ─── 3) EL PANORAMA DE HOY, SEMAFORIZADO ─────────────────────────────────────
st.header("🚦 El panorama de hoy")
st.caption("Los temas del último panorama del vigía (Snapshot), clasificados por el modelo: en qué conviene invertir energía.")
if not pack:
    st.info("Primero entrená el modelo.")
else:
    try:
        temas = sheets_memoria.leer_snapshot_anterior()
    except Exception:
        temas = []
    if not temas:
        st.info("No hay Snapshot reciente — esperá la próxima corrida del vigía.")
    else:
        filas = []
        for t in temas:
            titulo = t.get("titulo", "") if isinstance(t, dict) else str(t)
            medios = t.get("cant_medios", "") if isinstance(t, dict) else ""
            if not titulo:
                continue
            r = predecir_semaforo(pack, titulo, "", True)
            p_verde = dict(r["probas"]).get("verde", 0)
            filas.append((r["clase"], p_verde, titulo, medios, r["empuja"][:3]))
        orden_clase = {"verde": 0, "amarillo": 1, "rojo": 2}
        filas.sort(key=lambda x: (orden_clase[x[0]], -x[1]))
        iconos = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}
        n_v = sum(1 for f in filas if f[0] == "verde")
        st.caption(f"{len(filas)} temas en el panorama · {n_v} clasificados 🟢")
        for clase, pv, titulo, medios, empuja in filas:
            extra = f" · {medios} medios" if medios else ""
            razones = f"  _({', '.join(empuja)})_" if clase == "verde" and empuja else ""
            st.markdown(f"{iconos[clase]} **{pv:.0%}** · {titulo[:120]}{extra}{razones}")

st.markdown("---")

# ─── 4) PROBADOR ─────────────────────────────────────────────────────────────
st.header("🧪 Probar un tema")
if not pack:
    st.info("Primero entrená el modelo.")
else:
    ct1, ct2, ct3, ct4 = st.columns([3, 1.3, 1.1, 0.9])
    with ct1:
        titulo_test = st.text_input("Título o tema", placeholder="ej: Mastantuono se lesiona en la práctica de River")
    with ct4:
        franja = st.selectbox("Horario", ["(s/d)", "mañana", "mediodía", "tarde", "noche"])
    with ct2:
        sec = st.selectbox("Sección", ["(sin sección)", "Mundial | Mundial 2026", "River Plate",
                                       "Boca Juniors", "Selección Argentina", "Fútbol de Primera",
                                       "Racing Club", "Tenis"])
    with ct3:
        caliente = st.toggle("Tema caliente", help="¿Está creciendo en el panorama ahora?")
    if titulo_test.strip():
        _hmap = {"mañana": "09:00", "mediodía": "12:00", "tarde": "17:00", "noche": "21:00"}
        r = predecir_semaforo(pack, titulo_test, "" if sec == "(sin sección)" else sec, caliente,
                              _hmap.get(franja, ""))
        iconos = {"verde": "🟢", "amarillo": "🟡", "rojo": "🔴"}
        probas_txt = " · ".join(f"{iconos[c]} {p:.0%}" for c, p in r["probas"])
        st.markdown(f"## {iconos[r['clase']]} {r['clase'].upper()}  ·  {probas_txt}")
        st.caption(f"⬆️ Empuja: {', '.join(r['empuja']) or '—'}   ·   ⬇️ Frena: {', '.join(r['frena']) or '—'}")
        st.caption("El semáforo informa tu decisión, no la reemplaza.")
