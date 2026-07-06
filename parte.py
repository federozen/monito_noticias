"""parte.py — El parte de la mañana del copiloto editorial.

Corre solo (GitHub Actions, todos los días 7:30 hora argentina). Scrapea el
panorama del momento, lo cruza con la memoria (qué venía creciendo, qué ya
dimos) y le pide a Claude un parte con 5 focos sugeridos para el día — cada
uno con su ángulo del framework y un título tentativo.

Sale por Telegram (si está configurado) y queda en la pestaña "Parte".
Necesita los mismos secrets del vigía + ANTHROPIC_API_KEY.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import monitor_core
from monitor_core import (
    calcular_tendencias, analizar_ole_vs_compecencia_safe, construir_agenda,
    call_claude, fetch_cobertura_ole_gnews, FRAMEWORK_ANGULOS, bloque_criterios,
)
import sheets_memoria as mem
from vigia import scrapear_todo, enviar_telegram

_TZ_AR = timezone(timedelta(hours=-3))


def contexto_tema(it: dict) -> str:
    linea = f"[{it['accion']}] {it['titulo']} ({it['cant_medios']} medios"
    if it.get("nuevo"):
        linea += ", nuevo"
    elif it.get("delta", 0) > 0:
        linea += f", subió +{it['delta']}"
    linea += f") — {it['motivo']}"
    titulares = ""
    if it.get("noticias"):
        titulares = "\n" + "\n".join(
            f"    · [{n['fuente']['nombre']}] {n['noticia']['titulo'][:110]}"
            for n in it["noticias"][:5]
        )
    return linea + titulares


def prompt_parte(agenda: list, fecha: str) -> str:
    temas = "\n\n".join(contexto_tema(it) for it in agenda[:12])
    return f"""Sos editor jefe de Olé. Son las 7:30 del {fecha} y este es el panorama que
armó el sistema durante la noche: los temas accionables, con su momentum, si ya
los dimos, y cómo los tituló cada medio.

{FRAMEWORK_ANGULOS}{bloque_criterios()}

Escribí el PARTE DE LA MAÑANA en español rioplatense, directo, sin relleno:

PANORAMA — 3 líneas: cómo amanece el día futbolero y qué domina la conversación.

5 FOCOS PARA HOY — en orden de prioridad. Por cada foco:
  • El tema y por qué HOY (una línea).
  • El ÁNGULO: elegí el del framework que mejor aplique y que NINGÚN medio haya
    usado todavía (tenés sus títulos a la vista). Nombrá el tipo de ángulo.
  • Un TÍTULO tentativo, filoso.
  • Si el sistema marca RETOMAR, decidí: ¿actualización, segunda vuelta o dejarlo?

OJO CON — 1 o 2 temas que todavía son chicos pero vienen creciendo: los que
conviene vigilar hoy para llegar primero mañana.

PANORAMA:
{temas}"""


def main():
    if not mem.disponible():
        print("Sin planilla configurada. Abortando.")
        sys.exit(1)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("Falta ANTHROPIC_API_KEY. Abortando.")
        sys.exit(1)

    cfg = mem.leer_config()
    monitor_core.CRITERIOS_EDITOR = cfg.get("criterios", "")

    print("1) Scrapeando el panorama de la mañana...")
    resultados = scrapear_todo()
    fuentes_ok = sum(1 for v in resultados.values() if v)
    if fuentes_ok < 5:
        print("   Muy pocas fuentes; sin panorama no hay parte.")
        sys.exit(1)

    tendencias = calcular_tendencias(resultados)
    if cfg.get("ignorar"):
        tendencias = [c for c in tendencias
                      if not any(w in c["titulo"].lower() for w in cfg["ignorar"])]
    ole = analizar_ole_vs_compecencia_safe(resultados)
    prev = mem.leer_snapshot_anterior()
    cubiertos = mem.cobertura_propia(dias=5)
    cubiertos += [{"titulo": t, "fecha": None} for t in fetch_cobertura_ole_gnews()]

    agenda = construir_agenda(tendencias, ole, prev, max_items=15, cubiertos=cubiertos)
    print(f"2) {len(tendencias)} clusters → {len(agenda)} temas para el parte "
          f"(memoria propia: {len(cubiertos)} temas)")
    if not agenda:
        print("   Mañana tranquila: no hay temas accionables para un parte.")
        return

    fecha = datetime.now(_TZ_AR).strftime("%A %d/%m/%Y")
    print("3) Escribiendo el parte con Claude...")
    parte = call_claude(prompt_parte(agenda, fecha), api_key, max_tokens=2500)
    print(f"   {len(parte)} caracteres")

    mem.guardar_informe(parte, f"parte matutino {fecha}")
    print(f"4) Guardado en la planilla → {mem.url_planilla()}")

    # Telegram: en tandas de 3500 caracteres (límite de 4096 por mensaje)
    enviado = False
    encabezado = f"☕ <b>Parte de la mañana — {fecha}</b>\n\n"
    cuerpo = encabezado + parte
    for i in range(0, min(len(cuerpo), 10500), 3500):
        if enviar_telegram(cuerpo[i:i + 3500]):
            enviado = True
    print(f"5) Telegram: {'enviado' if enviado else 'no configurado'}")


if __name__ == "__main__":
    main()
