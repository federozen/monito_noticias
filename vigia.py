"""vigia.py — Piloto automático del Monitor Deportivo.

Corre solo (GitHub Actions, cada hora). En cada corrida:
  1. Scrapea las mismas fuentes que la app (monitor_core).
  2. Calcula tendencias y las compara con el Snapshot anterior del Sheet
     (momentum real entre corridas, aunque nadie haya abierto la app).
  3. Arma la agenda de acciones y descarta lo que el editor ya marcó
     como hecho/descartado en la planilla, y lo ya avisado hace poco.
  4. Escribe las acciones nuevas en la pestaña Agenda del Sheet.
  5. Si hay algo urgente (SUBIR YA) y hay bot configurado, avisa por Telegram.

Sin credenciales de Sheets corre en modo simulacro: imprime lo que haría.
"""
import os
import sys
import requests as _rq
from concurrent.futures import ThreadPoolExecutor, as_completed

import monitor_core
from monitor_core import (
    TODAS_FUENTES, fetch_fuente, calcular_tendencias,
    analizar_ole_vs_compecencia_safe, construir_agenda, normalizar_titulo,
    fetch_cobertura_ole_gnews,
)
import sheets_memoria as mem


def clave_tema(titulo: str) -> str:
    return " ".join(sorted(normalizar_titulo(titulo)))[:180]


def scrapear_todo() -> dict:
    resultados = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_fuente, f): f for f in TODAS_FUENTES}
        for fut in as_completed(futs):
            f = futs[fut]
            try:
                r = fut.result()
                noticias = r.get("noticias") or []
                error = r.get("error")
            except Exception as e:
                noticias, error = [], str(e)
            resultados[f["id"]] = noticias
            estado = f"{len(noticias):3d} notas" if not error else f"ERROR: {str(error)[:60]}"
            print(f"  [{f['id']:<12}] {estado}")
    return resultados


def matches_watchlist(titulo: str, watchlist: list) -> str:
    t = titulo.lower()
    for w in watchlist:
        if w and w in t:
            return w
    return ""


def enviar_telegram(texto: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    try:
        r = _rq.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": texto,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print(f"  Telegram falló: {e}")
        return False


def main():
    simulacro = not mem.disponible()
    print("=== VIGÍA ===", "(modo simulacro: sin Sheet configurado)" if simulacro else "")

    cfg = mem.leer_config() if not simulacro else {
        "umbral_medios": 4, "watchlist": [], "horas_silencio": 48}
    print(f"config: umbral={cfg['umbral_medios']} medios · "
          f"watchlist={cfg['watchlist']} · silencio={cfg['horas_silencio']}h")

    print("\n1) Scrapeando fuentes...")
    resultados = scrapear_todo()
    total = sum(len(v) for v in resultados.values())
    fuentes_ok = sum(1 for v in resultados.values() if v)
    print(f"   {total} noticias de {fuentes_ok}/{len(TODAS_FUENTES)} fuentes")
    if fuentes_ok < 5:
        print("   Muy pocas fuentes respondieron; aborto para no ensuciar la memoria.")
        sys.exit(1)

    print("\n2) Tendencias y momentum...")
    tendencias = calcular_tendencias(resultados)
    if cfg.get("ignorar"):
        antes = len(tendencias)
        tendencias = [c for c in tendencias
                      if not matches_watchlist(c["titulo"], cfg["ignorar"])]
        if antes - len(tendencias):
            print(f"   {antes - len(tendencias)} temas descartados por lista 'ignorar'")
    ole = analizar_ole_vs_compecencia_safe(resultados)
    prev = mem.leer_snapshot_anterior() if not simulacro else []
    monitor_core.CRITERIOS_EDITOR = cfg.get("criterios", "")
    cubiertos = []
    if not simulacro:
        cubiertos = mem.cobertura_propia(dias=5)
        cubiertos += [{"titulo": t, "fecha": None} for t in fetch_cobertura_ole_gnews()]
    print(f"   {len(tendencias)} clusters · snapshot anterior: {len(prev)} temas · "
          f"memoria de cobertura propia: {len(cubiertos)} temas")

    agenda = construir_agenda(tendencias, ole, prev, max_items=20, cubiertos=cubiertos)
    for it in agenda:
        it["clave"] = clave_tema(it["titulo"])

    # Watchlist: temas vigilados entran aunque Olé ya los tenga
    for c in tendencias:
        w = matches_watchlist(c["titulo"], cfg["watchlist"])
        if w and not any(a["clave"] == clave_tema(c["titulo"]) for a in agenda):
            agenda.append({
                "accion": "SEGUIR", "motivo": f"watchlist: '{w}'",
                "titulo": c["titulo"], "url": c.get("url"),
                "cant_medios": c["cant_medios"], "delta": 0, "nuevo": False,
                "clave": clave_tema(c["titulo"]),
            })

    # Filtro de urgencia: solo pasa lo que supera el umbral o es watchlist/exclusivo
    accionables = [
        it for it in agenda
        if (it["accion"] == "SUBIR YA" and it["cant_medios"] >= cfg["umbral_medios"])
        or it["accion"] in ("SEGUIR", "EMPUJAR", "RETOMAR")
        or (it["accion"] == "REDACTAR" and it["cant_medios"] >= cfg["umbral_medios"])
    ]

    print("\n3) Filtrando lo ya tratado/avisado...")
    if not simulacro:
        nuevos = mem.filtrar_ya_tratados(accionables, cfg["horas_silencio"])
    else:
        nuevos = accionables
    print(f"   {len(accionables)} accionables → {len(nuevos)} nuevos")

    # Memoria y limpieza: SIEMPRE, haya o no avisos nuevos
    if not simulacro:
        mem.guardar_snapshot(tendencias, origen="vigia")
        n_hist = mem.registrar_historial(tendencias)
        n_arch = mem.archivar_agenda_vieja(cfg.get("dias_archivo", 3))
        if cfg.get("_formato_v") != mem.FORMATO_VERSION:
            print("   aplicando formato al tablero:", "ok" if mem.formatear_tablero() else "falló")
        n_limp = mem.limpiar_historial()
        if n_limp:
            print(f"   historial recortado: {n_limp} filas viejas")
        print(f"   memoria: snapshot ok · {n_hist} temas al Historial"
              + (f" · {n_arch} filas archivadas" if n_arch else ""))

    if not nuevos:
        print("\nNada nuevo que avisar. Silencio = todo bajo control.")
        return

    print("\n4) Escribiendo en la Agenda del Sheet...")
    if not simulacro:
        n = mem.agregar_a_agenda(nuevos, origen="vigia")
        print(f"   {n} filas agregadas → {mem.url_planilla()}")
    else:
        for it in nuevos:
            print(f"   [{it['accion']:8}] {it['titulo'][:70]}")

    urgentes = [it for it in nuevos if it["accion"] == "SUBIR YA"]
    if urgentes:
        lineas = "\n".join(
            f"🔴 <b>{it['titulo'][:120]}</b>\n   {it['cant_medios']} medios y Olé no"
            + (f" · <a href=\"{it['url']}\">ver</a>" if it.get("url") else "")
            for it in urgentes[:5]
        )
        extra = f"\n\n(+{len(nuevos) - len(urgentes)} acciones más en la planilla)" \
            if len(nuevos) > len(urgentes) else ""
        link = f"\n📋 {mem.url_planilla()}" if not simulacro else ""
        ok = enviar_telegram(f"<b>SUBIR YA</b>\n\n{lineas}{extra}{link}")
        print(f"\n5) Telegram: {'enviado' if ok else 'no configurado / falló'}")


if __name__ == "__main__":
    main()
