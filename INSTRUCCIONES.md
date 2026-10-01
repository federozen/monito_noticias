# Piloto automático del Monitor — puesta en marcha (sin consola)

## Qué es cada archivo

| Archivo | Qué hace |
|---|---|
| `app.py` | La interfaz Streamlit de siempre (ahora importa el núcleo) |
| `monitor_core.py` | El cerebro compartido: scraping, clustering, agenda |
| `sheets_memoria.py` | La memoria en Google Sheets (tablero + feedback) |
| `ia_motores.py` | Los motores de IA: los gratuitos primero (Gemini, Mistral, Groq, OpenRouter) y Claude de respaldo |
| `para_ia.py` | Arma informes y notas para pegar en ChatGPT, Claude o Gemini (gratis, sin API key) |
| `vigia.py` | El piloto automático que corre solo cada hora |
| `.github/workflows/vigia.yml` | Le dice a GitHub cuándo correr el vigía |
| `requirements.txt` | Dependencias de la app |
| `requirements-vigia.txt` | Dependencias del vigía (más liviano, sin Streamlit) |

## Estructura del repo en GitHub

```
tu-repo/
├── app.py
├── monitor_core.py
├── sheets_memoria.py
├── ia_motores.py        ← NUEVO: subilo también
├── para_ia.py           ← NUEVO: subilo también
├── vigia.py
├── requirements.txt
├── requirements-vigia.txt
└── .github/
    └── workflows/
        └── vigia.yml      ← OJO: tiene que estar exactamente en esta carpeta
```

En GitHub web: **Add file → Create new file**, y en el nombre escribí
`.github/workflows/vigia.yml` (GitHub crea las carpetas solo).

## Paso 1 — Crear la planilla y la service account (una sola vez, ~10 min)

1. Creá un Google Sheet nuevo y vacío. De su URL copiá el **ID** (lo que está
   entre `/d/` y `/edit`).
2. Andá a https://console.cloud.google.com → creá un proyecto (nombre libre).
3. En "APIs y servicios → Biblioteca", buscá **Google Sheets API** y **Google
   Drive API**, habilitá las dos.
4. En "APIs y servicios → Credenciales" → **Crear credenciales → Cuenta de
   servicio**. Nombre libre, sin permisos extra, Listo.
5. Entrá a la cuenta de servicio creada → pestaña **Claves** → Agregar clave →
   Crear clave nueva → **JSON**. Se descarga un archivo `.json`: ese contenido
   completo es tu credencial.
6. En la cuenta de servicio vas a ver un email tipo
   `algo@proyecto.iam.gserviceaccount.com`. **Compartí tu Google Sheet con ese
   email** (botón Compartir, permiso Editor). Sin este paso no funciona nada.

## Paso 2 — Cargar los secrets

**En GitHub** (repo → Settings → Secrets and variables → Actions → New secret):
- `GOOGLE_SERVICE_ACCOUNT_JSON` → pegá el contenido completo del archivo .json
- `SHEET_ID` → el ID de la planilla
- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` → opcionales (paso 4)
- Para el parte, el informe y el radar: `ANTHROPIC_API_KEY` y/o las gratuitas `GEMINI_API_KEY`,
  `MISTRAL_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` (con una alcanza)

**En Streamlit Cloud** (app → Settings → Secrets):
```toml
# Motores de IA: alcanza con uno. Los gratuitos se usan primero; Claude solo si fallan todos.
GEMINI_API_KEY = "..."        # gratis: aistudio.google.com → Get API key
MISTRAL_API_KEY = "..."       # gratis: console.mistral.ai → API Keys
GROQ_API_KEY = "..."          # gratis: console.groq.com → API Keys
OPENROUTER_API_KEY = "..."    # gratis: openrouter.ai → Keys
ANTHROPIC_API_KEY = "sk-ant-..."   # pago, de respaldo
SHEET_ID = "el-id-de-tu-planilla"
GOOGLE_SERVICE_ACCOUNT_JSON = '''
{ ...pegá acá el JSON completo... }
'''
```

## Paso 3 — Probar el vigía a mano

Repo → pestaña **Actions** → "Vigía del monitor" → botón **Run workflow**.
En 2-3 minutos mirá el log: tiene que decir cuántas fuentes respondieron y
cuántas filas escribió. Abrí tu planilla: van a aparecer las pestañas
**Agenda**, **Snapshot** y **Config** llenándose solas.

## Paso 4 (opcional) — Avisos urgentes por Telegram

1. Instalá Telegram y buscá **@BotFather** → mandale `/newbot` → te da un
   **token**. Ese es `TELEGRAM_BOT_TOKEN`.
2. Mandale cualquier mensaje a tu bot nuevo (buscalo por el nombre que le
   pusiste).
3. Abrí en el navegador:
   `https://api.telegram.org/bot<TU_TOKEN>/getUpdates`
   y buscá `"chat":{"id":123456789` — ese número es `TELEGRAM_CHAT_ID`.
4. Cargá los dos como secrets en GitHub. Activale las notificaciones al chat.

## Cómo se usa en el día a día

- **La planilla es el tablero.** El vigía la llena solo, cada hora. Cada fila
  es una acción: SUBIR YA / REDACTAR / SEGUIR / EMPUJAR, con el tema, cuántos
  medios lo tienen y el momentum.
- **Vos respondés en la columna Estado**: escribí `hecho` o `descartado` y el
  vigía deja de insistir con ese tema. Si la dejás en `pendiente`, no te lo
  repite por 48 horas (configurable).
- **La pestaña Config es tu panel**: cambiá el umbral de medios o la watchlist
  (ej: `river, boca, seleccion argentina, scaloni`) directamente en la celda.
  No hace falta tocar código.
- **Telegram solo te habla si hay un SUBIR YA.** El silencio significa que no
  hay nada urgente.
- **La app de Streamlit sigue igual**, pero ahora su momentum es real (compara
  contra la última corrida del vigía, no contra tu último refresh) y en la
  barra lateral tenés el link directo a la planilla.

## Si algo falla

- El log de cada corrida queda en la pestaña Actions del repo.
- Si el vigía aborta con "muy pocas fuentes respondieron", es protección:
  prefiere no escribir nada antes que ensuciar la memoria con una corrida mala.
- El horario está en el yml (`cron`, en hora UTC = Argentina + 3). Hoy corre
  de 7 a 23 hora argentina, una vez por hora.

## Motores de IA (gratis primero, Claude de respaldo)

La app y los scripts ya no dependen solo de Claude. Prueban en este orden:
**Gemini → Mistral → Groq → OpenRouter → Claude**. Si uno llega al tope de su plan
gratuito, tiene la clave mal o el pedido le queda grande, pasa solo al siguiente.
Claude (pago) se usa únicamente si fallan todos los gratuitos.

- En la barra lateral, **🤖 Motores de IA** muestra el orden y deja elegir:
  *Automático · gratis* (el normal), *Claude primero* o uno en particular.
- Las claves van en los **Secrets** (de Streamlit y de GitHub), nunca dentro del código.
  También se pueden pegar en la barra lateral para una sesión.
- Debajo de cada nota dice qué motor la escribió.
- Opcionales: `IA_ORDEN` (por ejemplo `mistral,gemini,groq,openrouter`) y
  `IA_RESPALDO_CLAUDE = "no"` para que nunca use Claude.
- GitHub Models (`GITHUB_TOKEN`) no está: GitHub cerró ese servicio el 30/07/2026.
- El vigía no tiene claves de IA en su workflow, así que sigue sin mandar el parte
  inteligente. Si lo querés, sumá las claves al bloque `env` de `vigia.yml`.

## Nota con IA desde un tema caliente

En **🎯 Agenda** y en **📊 Tendencias**, cada tema tiene el botón **✍️ Nota**:

1. Se abre ahí mismo con las notas del tema; ya vienen sugeridas una por medio
   (hasta 5). Podés sacar o sumar con el selector, o con **Todas** / **Ninguna**.
2. **➕ Sumar otras notas**: buscás una palabra y agregás notas de otros títulos o medios.
3. Elegís estilo (Informativa, Analítica, Urgente) y qué querés (nota completa,
   breve, titulares o esqueleto). En *Lo que sabés vos* va un dato propio o el ángulo.
4. **✦ Escribir**: lee el texto de las notas y escribe una sola nota de Olé.

El resultado (igual en Nota Rápida y en Canasta) viene en pestañas:
**Nota** (título, bajada y cuerpo, editable y para descargar), **Verificación**
(cada dato con su fuente: ✅ ⚠️ ❌), **Para completar** (lo que falta chequear),
**Datos** (el inventario de la IA) y **Títulos y ángulos**.

Cómo escribe la IA: arma un inventario de datos con su fuente, separa lo
confirmado de lo atribuido, elige un ángulo del framework de Olé, escribe con
palabras propias sin nombrar a los otros medios ("trascendió que" para lo no
oficial), pone **[DATO A CONFIRMAR: …]** en lugar de inventar y controla cada
dato antes de entregar.

## Informes y notas para ChatGPT o Claude (gratis, sin API key)

La pestaña **📤 Para ChatGPT/Claude** arma el pedido completo (instrucciones + material)
para que lo pegues en ChatGPT, Claude o Gemini. No usa ninguna API key.

**Informes**: elegís el tipo y los medios (todos, nacionales o internacionales) y tocás
**📋 Armar el informe**:
- **📋 Parte del día**: lo que domina, los huecos de Olé, lo que tiene solo Olé, lo que crece y 5 notas propuestas.
- **⭐ Olé vs. la competencia**: dónde llega Olé, dónde no, exclusivos y cómo titula cada medio.
- **🔁 Mercado de pases**: tablero por club con estado de cada operación y nivel de certeza.
- **🌍 Argentinos en el mundo**: qué dice la prensa internacional y notas posibles.
- **💬 Panorama para preguntar**: todos los titulares, para hacerle preguntas.

**Notas**: elegís un tema, marcás las notas y tocás **📋 Pedido para ChatGPT/Claude**.
Lee el texto de cada nota y arma el pedido con el mismo método que la nota con IA
(inventario de datos, ángulo, nota y control). El mismo botón está en **✍️ Nota**
(Agenda y Tendencias) y en la **Canasta**.

Con el pedido listo: el ícono de copiar está arriba a la derecha del recuadro
(en *Ver el pedido completo*), o **📥 Descargar .txt** para adjuntarlo si es muy largo.
Los botones **Abrir ChatGPT / Claude / Gemini** abren cada uno en otra pestaña.
