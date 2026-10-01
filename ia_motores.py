"""
Motores de IA del Monitor: los gratuitos primero y Claude de respaldo.

Las claves NUNCA van en el código: se cargan en los Secrets de Streamlit Cloud
(app) y en los Secrets de GitHub (vigía, parte, informe, radar), con estos nombres:

  GEMINI_API_KEY       Gemini (Google AI Studio), gratis
  MISTRAL_API_KEY      Mistral (console.mistral.ai, plan "Experiment"), gratis
  GROQ_API_KEY         Groq (console.groq.com), gratis
  OPENROUTER_API_KEY   OpenRouter (openrouter.ai, solo modelos gratuitos), gratis
  ANTHROPIC_API_KEY    Claude, pago

Opcionales:
  IA_ORDEN             orden de los gratuitos (por defecto gemini,mistral,groq,openrouter)
  IA_RESPALDO_CLAUDE   "no" para que nunca use Claude como respaldo
  GEMINI_MODELO / GEMINI_MODELO_RAPIDO, MISTRAL_MODELO / ..., GROQ_MODELO / ...,
  OPENROUTER_MODELO / ..., para cambiar los modelos sin tocar código.

Motor "auto" (el de siempre): prueba los gratuitos en orden y, si fallan todos
(tope del plan, clave mal cargada, pedido demasiado largo), recién ahí usa Claude.
"claude": Claude primero y, si falla, los gratuitos. Uno en particular: ese primero
y después el resto.

Este archivo no depende de Streamlit: lo usan la app y los scripts de GitHub.
"""
import os
import re
import time

import requests

NOMBRES = {"gemini": "Gemini", "mistral": "Mistral", "groq": "Groq",
           "openrouter": "OpenRouter", "claude": "Claude"}
GRATIS = ["gemini", "mistral", "groq", "openrouter"]
ENV = {"gemini": "GEMINI_API_KEY", "mistral": "MISTRAL_API_KEY", "groq": "GROQ_API_KEY",
       "openrouter": "OPENROUTER_API_KEY", "claude": "ANTHROPIC_API_KEY"}
MODELOS = {   # (modelo para notas y análisis, modelo rápido/económico)
    "gemini": ("gemini-flash-latest", "gemini-3.5-flash-lite"),
    "mistral": ("mistral-medium-latest", "mistral-small-latest"),
    "groq": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
    "openrouter": ("openrouter/free", "openrouter/free"),
    "claude": ("claude-sonnet-5", "claude-haiku-4-5-20251001"),
}
# Valor que la app usa como "api_key" cuando no hay clave de Claude pero sí motores gratuitos
SIN_CLAVE = "motores-gratis"
TIMEOUT = 120
PRESUPUESTO_SEG = 240        # si los motores tardan y fallan, no se prueban más después de este tiempo

_CONF = {"claves": {}, "motor": "auto"}
ULTIMO = {"motor": "", "nombre": "", "modelo": ""}   # quién respondió el último pedido (para mostrarlo)


class ErrorMotor(Exception):
    def __init__(self, motor, mensaje, tope=False):
        super().__init__(mensaje)
        self.motor, self.tope = motor, tope


def _es_clave(k: str) -> bool:
    u = (k or "").upper()
    return bool(k) and not any(x in u for x in ("PEGÁ", "PEGA_TU", "TU_CLAVE", "SK-ANT-...", "XXXX"))


def configurar(claves: dict = None, motor: str = None):
    """La app llama a esto en cada recarga con lo que hay en la barra lateral."""
    if claves is not None:
        _CONF["claves"] = {k: (v or "").strip() for k, v in claves.items()}
    if motor:
        _CONF["motor"] = motor


def clave(m: str) -> str:
    k = (_CONF["claves"].get(m) or os.environ.get(ENV[m], "") or "").strip()
    return k if _es_clave(k) else ""


def _orden() -> list:
    o = [x.strip().lower() for x in os.environ.get("IA_ORDEN", "").split(",") if x.strip().lower() in GRATIS]
    return o + [g for g in GRATIS if g not in o]


def disponibles(anthropic_key: str = "") -> list:
    """Motores con clave cargada: primero los gratuitos (en orden) y al final Claude."""
    libres = [m for m in _orden() if clave(m)]
    tiene_claude = _es_clave(anthropic_key) and anthropic_key != SIN_CLAVE or bool(clave("claude"))
    return libres + (["claude"] if tiene_claude else [])


def hay_motor(anthropic_key: str = "") -> bool:
    return bool(disponibles(anthropic_key))


def cadena(motor: str = None, anthropic_key: str = "") -> list:
    """En qué orden se prueban los motores."""
    motor = (motor or _CONF["motor"] or "auto").lower()
    disp = disponibles(anthropic_key)
    libres = [m for m in disp if m != "claude"]
    claude = ["claude"] if "claude" in disp else []
    if motor == "claude":
        return claude + libres
    respaldo = claude if os.environ.get("IA_RESPALDO_CLAUDE", "si").strip().lower() not in ("no", "0", "false") or not libres else []
    if motor in libres:
        return [motor] + [m for m in libres if m != motor] + respaldo
    return libres + respaldo


def descripcion_cadena(anthropic_key: str = "", motor: str = None) -> str:
    c = cadena(motor, anthropic_key)
    return " → ".join(NOMBRES[m] for m in c) if c else "ninguno (falta cargar una clave)"


def _modelo(m: str, nivel: str) -> str:
    base = MODELOS[m][1 if nivel == "rapido" else 0]
    var = ("CLAUDE" if m == "claude" else m.upper()) + ("_MODELO_RAPIDO" if nivel == "rapido" else "_MODELO")
    return os.environ.get(var, "").strip() or base


# ─────────────────────────── cada motor ───────────────────────────
def _error_http(m, r, modelo):
    try:
        err = r.json().get("error", {})
        msg = err.get("message", "") if isinstance(err, dict) else str(err)
    except Exception:
        msg = r.text[:200]
    nombre = NOMBRES[m]
    if r.status_code == 429:
        return ErrorMotor(m, f"{nombre} llegó al tope de su plan por ahora", tope=True)
    if r.status_code == 413 or "too large" in msg.lower() or "tokens per minute" in msg.lower() or "context" in msg.lower():
        return ErrorMotor(m, f"el pedido es demasiado largo para {nombre}", tope=True)
    if r.status_code in (401, 403):
        return ErrorMotor(m, f"la clave de {nombre} no es válida (revisá {ENV[m]})")
    if r.status_code == 404:
        return ErrorMotor(m, f"el modelo {modelo} no está disponible en {nombre}")
    return ErrorMotor(m, f"{nombre}: {msg[:200] or r.status_code}")


def _gemini(prompt, system, modelo, max_tokens):
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # Los modelos nuevos "piensan" antes de responder y eso también cuenta: se deja margen
            "generationConfig": {"maxOutputTokens": min(max_tokens * 2, 16000), "temperature": 0.4}}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    try:
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{modelo}:generateContent",
                          headers={"x-goog-api-key": clave("gemini"), "content-type": "application/json"},
                          json=body, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ErrorMotor("gemini", f"no se pudo conectar con Gemini ({str(e)[:80]})")
    if r.status_code != 200:
        raise _error_http("gemini", r, modelo)
    d = r.json()
    cand = (d.get("candidates") or [{}])[0]
    texto = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought")).strip()
    if not texto:
        raise ErrorMotor("gemini", f"Gemini no devolvió texto ({cand.get('finishReason') or 'sin motivo'})")
    return texto


def _compatible(m, url, prompt, system, modelo, max_tokens, extra=None):
    """Groq, Mistral y OpenRouter usan el mismo formato que OpenAI."""
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = {"model": modelo, "messages": msgs, "max_tokens": max_tokens, "temperature": 0.4}
    if m == "groq":
        body["max_tokens"] = min(max_tokens, 3000)   # el plan gratuito permite pocos tokens por minuto
        if "gpt-oss" in modelo:
            body["reasoning_effort"] = "low"
    try:
        r = requests.post(url, json=body, timeout=TIMEOUT,
                          headers={"Authorization": f"Bearer {clave(m)}", "content-type": "application/json", **(extra or {})})
    except requests.RequestException as e:
        raise ErrorMotor(m, f"no se pudo conectar con {NOMBRES[m]} ({str(e)[:80]})")
    if r.status_code != 200:
        raise _error_http(m, r, modelo)
    d = r.json()
    if d.get("error"):
        e = d["error"]
        raise ErrorMotor(m, f"{NOMBRES[m]}: {(e.get('message') if isinstance(e, dict) else str(e))[:200]}")
    texto = ((d.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if isinstance(texto, list):
        texto = "".join(x.get("text", "") for x in texto if isinstance(x, dict))
    texto = re.sub(r"<think>.*?</think>", "", texto.strip(), flags=re.DOTALL).strip()
    if not texto:
        raise ErrorMotor(m, f"{NOMBRES[m]} no devolvió texto")
    return texto


def _claude(prompt, system, modelo, max_tokens, anthropic_key):
    key = anthropic_key if (_es_clave(anthropic_key) and anthropic_key != SIN_CLAVE) else clave("claude")
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        kw = {"system": system} if system else {}
        msg = client.messages.create(model=modelo, max_tokens=max_tokens,
                                     messages=[{"role": "user", "content": prompt}], **kw)
    except Exception as e:
        t = str(e)
        raise ErrorMotor("claude", f"Claude: {t[:200]}", tope="429" in t or "rate" in t.lower())
    texto = "\n".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
    if not texto:
        raise ErrorMotor("claude", "Claude no devolvió texto")
    return texto


def generar(prompt: str, max_tokens: int = 2000, nivel: str = "modelo", anthropic_key: str = "",
            motor: str = None, system: str = None, modelo_claude: str = None) -> str:
    """Un pedido a la IA: prueba los motores de la cadena hasta que uno responde.
    nivel: "modelo" (notas y análisis) o "rapido" (partes y resúmenes, más económico)."""
    orden = cadena(motor, anthropic_key)
    if not orden:
        raise RuntimeError("No hay ningún motor de IA configurado: cargá al menos una clave "
                           "(GEMINI_API_KEY, MISTRAL_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY o ANTHROPIC_API_KEY).")
    errores, t0 = [], time.time()
    for i, m in enumerate(orden):
        if i and time.time() - t0 > PRESUPUESTO_SEG:
            break
        modelo = (modelo_claude or _modelo(m, nivel)) if m == "claude" else _modelo(m, nivel)
        try:
            if m == "gemini":
                texto = _gemini(prompt, system, modelo, max_tokens)
            elif m == "groq":
                texto = _compatible(m, "https://api.groq.com/openai/v1/chat/completions", prompt, system, modelo, max_tokens)
            elif m == "mistral":
                texto = _compatible(m, "https://api.mistral.ai/v1/chat/completions", prompt, system, modelo, max_tokens)
            elif m == "openrouter":
                texto = _compatible(m, "https://openrouter.ai/api/v1/chat/completions", prompt, system, modelo, max_tokens,
                                    {"X-Title": "Monitor Deportivo"})
            else:
                texto = _claude(prompt, system, modelo, max_tokens, anthropic_key)
        except ErrorMotor as e:
            errores.append(e)
            continue
        ULTIMO.update(motor=m, nombre=NOMBRES[m], modelo=modelo)
        return texto
    detalle = " · ".join(str(e) for e in errores)
    raise RuntimeError(f"No respondió ningún motor de IA. {detalle}"[:700])
