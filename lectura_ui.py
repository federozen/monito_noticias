"""lectura_ui.py — La lectura de la home de Olé dentro del Streamlit.

Se muestra como sub-pestaña de "⭐ Olé vs Todos". Usa la competencia que la app
ya scrapeó (no la vuelve a bajar): sólo lee la home de Olé completa y cruza.
Toda la lógica está en lectura.py, la misma que usa GitHub Actions.
"""
import streamlit as st

import lectura
import monitor_core
import sheets_memoria


def _tabla(filas: list, columnas: dict, link: str = None, alto: int = None):
    """filas: lista de dicts · columnas: {clave_en_dict: encabezado}."""
    import pandas as pd
    if not filas:
        st.caption("Nada para mostrar.")
        return
    df = pd.DataFrame([{v: f.get(k, "") for k, v in columnas.items()} for f in filas])
    cfg = {}
    if link and link in df.columns:
        cfg[link] = st.column_config.LinkColumn(link, display_text="abrir")
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=cfg,
                 height=alto or min(38 + 35 * len(df), 420))


def _tendencia():
    """Gráfico con las corridas guardadas en la pestaña Lectura de la planilla."""
    import pandas as pd
    if not sheets_memoria.disponible():
        st.caption("Conectá la planilla para ver la tendencia.")
        return
    try:
        filas = sheets_memoria._ws("Lectura", lectura.LECTURA_HEADERS).get_all_records()
    except Exception as e:
        st.caption(f"No pude leer la pestaña Lectura: {str(e)[:80]}")
        return
    if len(filas) < 2:
        st.caption("La tendencia aparece desde la segunda corrida guardada.")
        return
    df = pd.DataFrame(filas)
    df["Corrida"] = df["Fecha"].astype(str) + " " + df["Hora"].astype(str)
    for c in ["Cobertura%", "Commodity%", "Vencidas", "Faltantes", "VozPropia"]:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", "."), errors="coerce")
    df = df.set_index("Corrida").tail(60)
    c1, c2 = st.columns(2)
    with c1:
        st.caption("Cobertura de agenda y commodity (%)")
        st.line_chart(df[["Cobertura%", "Commodity%"]])
    with c2:
        st.caption("Faltantes, vencidas y voz propia (cantidad)")
        st.line_chart(df[["Faltantes", "Vencidas", "VozPropia"]])


def render(resultados: dict, api_key: str = ""):
    ss = st.session_state
    st.caption("La home de Olé completa contra la agenda de los medios nacionales que ya cargó la app. "
               "Lo mismo que manda el aviso de Telegram, pero a demanda.")

    c1, c2, c3 = st.columns([1.3, 1, 1.4])
    leer = c1.button("🗞️ Leer el día y la home", type="primary", key="lect_leer")
    con_ia = c1.checkbox("Con IA (resumen del día y cruce fino)", value=True, key="lect_con_ia",
                         help="Sin IA el cruce es por palabras y puede marcar faltantes que la home sí tiene")
    excluir = c3.text_input("Excluir títulos con", value=ss.get("lect_excluir", lectura.excluir_de_config()),
                            key="lect_excluir",
                            help="Keywords separadas por coma. Ej: olé summit, brand studio, suramericanos")
    with c2.popover("📋 Pegar la home"):
        st.caption("Si la lectura automática falla: copiá la home y pegala acá, un título por línea.")
        pegado = st.text_area("Títulos", key="lect_pegado", height=200, label_visibility="collapsed")
        usar_pegado = st.button("Usar estos títulos", key="lect_usar_pegado")

    if leer or usar_pegado:
        try:
            if usar_pegado and pegado.strip():
                lineas = [l.strip() for l in pegado.splitlines() if len(l.strip()) >= 20]
                home = [{"titulo": t, "url": "", "posicion": i} for i, t in enumerate(lineas, 1)]
            else:
                with st.spinner("Leyendo la home de Olé..."):
                    home = lectura.scrapear_home_ole()
            home, fuera = lectura.filtrar_home(home, excluir)
            if len(home) < 20:
                st.error(f"Sólo encontré {len(home)} títulos. Si la home cambió de estructura, usá 'Pegar la home'.")
                return
            extra = ""
            if sheets_memoria.disponible():
                try:
                    extra = sheets_memoria.leer_config().get("entidades_extra", "")
                except Exception:
                    pass
            dic = monitor_core.dic_entidades(extra)
            if con_ia:
                with st.spinner("La IA está leyendo el día y la home (30-60 segundos)..."):
                    a = lectura.leer_todo(home, resultados, dic, api_key=api_key or "")
                if not a.get("leido_por_ia"):
                    st.warning("La IA no respondió bien: muestro el cruce por palabras, que puede traer falsos positivos.")
            else:
                a = lectura.analizar_home(home, resultados, dic)
                a.update({"resumen": [], "errores": [], "lectura": "", "leido_por_ia": False})
            ss.lect_home, ss.lect_fuera, ss.lect_a = home, fuera, a
        except Exception as e:
            st.error(f"No pude leer la home: {str(e)[:150]}. Probá con 'Pegar la home'.")
            return

    a = ss.get("lect_a")
    if not a:
        st.info("Apretá **Leer la home ahora**.")
        with st.expander("📈 Tendencia de corridas anteriores"):
            _tendencia()
        return
    home, fuera = ss.lect_home, ss.get("lect_fuera", [])

    if a["medios_ok"] < 6:
        st.warning(f"Sólo hay {a['medios_ok']} medios nacionales cargados: los números son poco confiables. "
                   "Actualizá las fuentes antes de leer.")

    if a.get("resumen"):
        st.subheader("🗞️ El día")
        for r in a["resumen"]:
            marca = f"✅ en la home ({r['ole']})" if r["ole_pos"] else "❌ **no está en la home**"
            st.markdown(f"- {r['linea']} · _{r['n_nac']} medios nacionales_ · {marca}")
        st.divider()

    st.subheader("📰 La home de Olé")
    if a.get("lectura"):
        st.info(a["lectura"])
    m = st.columns(5)
    m[0].metric("Cobertura de agenda", f"{a['cobertura_pct']}%",
                help=f"{a['cubiertos']} de {a['temas_agenda']} temas que tienen {lectura.UMBRAL_AGENDA}+ medios nacionales")
    m[1].metric("Faltan", len(a["faltantes"]), help="Puede traer falsos positivos: revisalos o usá la lectura con IA")
    m[2].metric("Vencidas", len(a["vencidas"]), help="Previas o servicio de eventos que ya tienen resultado")
    m[3].metric("Voz propia", len(a["voz_propia"]), help="'con Olé', enviados, exclusivas, datos propios")
    m[4].metric("Commodity", f"{a['commodity_pct']}%",
                help=f"Títulos de la home cuyo tema tienen {lectura.UMBRAL_COMMODITY}+ medios nacionales")
    st.caption(f"{a['notas_home']} títulos en la home · {len(fuera)} excluidos · "
               f"{a['medios_ok']} medios nacionales · {a['titulos_nac']} títulos de la competencia · "
               f"desvío de mix en los 3 temas top: {a['desvio_top3']} pts")

    if st.button("💾 Guardar en la planilla", key="lect_guardar",
                 help="Suma una fila a la pestaña Lectura (la tendencia) y reemplaza Lectura detalle"):
        from datetime import datetime
        try:
            ok = lectura.guardar_planilla(a, home, a.get("lectura", ""), datetime.now(lectura._TZ_AR))
            st.success("Guardado.") if ok else st.warning("La planilla no está configurada.")
        except Exception as e:
            st.error(f"No se pudo guardar: {str(e)[:120]}")

    t1, t2, t3, t4, t5 = st.tabs([
        f"❌ Faltan ({len(a['faltantes'])})",
        f"⏰ Vencidas, duplicadas y errores ({len(a['vencidas']) + len(a['duplicados']) + len(a.get('errores', []))})",
        f"⬇️ Temas grandes abajo ({len(a['enterrados'])})",
        f"⭐ Voz propia y sin par ({len(a['voz_propia'])})",
        "🏠 Home y mix",
    ])
    with t1:
        st.caption("Temas que tienen varios medios nacionales y no están en la home. "
                   + ("Cruce hecho por la IA." if a.get("leido_por_ia")
                      else "Cruce por palabras: si la home lo tiene con otras palabras, es falso positivo."))
        _tabla(a["faltantes"], {"n_nac": "Nac.", "n_total": "Total", "titulo": "Tema",
                                "medios": "Medios", "url": "Link"}, link="Link")
    with t2:
        st.markdown("**Posiblemente vencidas** — previas o servicio de algo que ya tiene resultado")
        _tabla(a["vencidas"], {"posicion": "#", "titulo": "Título en la home", "evidencia": "Por qué"})
        st.markdown("**Errores en títulos**")
        _tabla(a.get("errores", []), {"posicion": "#", "titulo": "Título", "error": "Error"})
        st.markdown("**Duplicadas** — títulos casi iguales en la home")
        _tabla(a["duplicados"], {"posicion": "#", "titulo": "Título", "igual_a": "Igual a"})
    with t3:
        st.caption(f"Temas que tienen {lectura.UMBRAL_COMMODITY}+ medios nacionales y en la home "
                   f"están después de la posición {lectura.ZONA_SCROLL}.")
        _tabla(a["enterrados"], {"posicion": "#", "titulo": "Título", "n_nac": "Medios nac."})
    with t4:
        st.markdown("**Voz propia**")
        _tabla(a["voz_propia"], {"posicion": "#", "titulo": "Título"})
        st.markdown("**Sin par en la competencia** — ningún nacional tiene ese tema")
        _tabla(a["sin_par"], {"posicion": "#", "titulo": "Título"}, alto=300)
    with t5:
        st.markdown("**Mix por tema** — % de títulos de Olé vs % de títulos nacionales")
        import pandas as pd
        if a["mix"]:
            df_mix = (pd.DataFrame(a["mix"]).set_index("entidad")[["ole_pct", "nac_pct"]]
                      .rename(columns={"ole_pct": "Olé %", "nac_pct": "Nacionales %"}))
            try:
                st.bar_chart(df_mix, horizontal=True, stack=False)
            except TypeError:  # Streamlit viejo
                st.dataframe(df_mix, use_container_width=True)
        st.markdown("**La home, en orden**")
        _tabla([dict(n, zona=lectura.zona(n["posicion"])) for n in home],
               {"posicion": "#", "zona": "Zona", "titulo": "Título", "url": "Link"}, link="Link", alto=420)
        if fuera:
            st.caption("Excluidos: " + " · ".join(n["titulo"][:60] for n in fuera))

    with st.expander("📈 Tendencia de corridas anteriores"):
        _tendencia()
