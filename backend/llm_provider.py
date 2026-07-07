"""Shared LLM provider layer — the single seam for all LLM calls (CLAUDE.md rule 9).

Try the preferred provider first (local Ollama by default), auto-fallback to the
other (Claude API), raise if both fail. Consumers: backend/main.py, matcher, teach.
Importable both as `llm_provider` (cwd=backend/) and `backend.llm_provider`
(cwd=repo root, e.g. `python -m backend.matcher.run`).
"""

from __future__ import annotations

import json
import os
import re
import time as _time

try:
    from logger import get_logger, log_event
except ImportError:  # invoked as backend.* from repo root
    from backend.logger import get_logger, log_event

log = get_logger("llm")

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")


def _probe_ollama_base(timeout: float = 1.0) -> str | None:
    """Return first reachable Ollama base URL (tries default port then common alt)."""
    import requests as http_requests
    candidates = []
    if os.getenv("OLLAMA_BASE_URL"):
        candidates.append(os.getenv("OLLAMA_BASE_URL").rstrip("/"))
    for port in (11434, 11435, 11436):
        candidates.append(f"http://127.0.0.1:{port}")
    seen = set()
    for base in candidates:
        if not base or base in seen:
            continue
        seen.add(base)
        try:
            r = http_requests.get(f"{base}/api/tags", timeout=timeout)
            if r.status_code == 200:
                return base
        except Exception:
            continue
    return None


_detected = _probe_ollama_base()
_default_base = (_detected or "http://127.0.0.1:11434").rstrip("/")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", f"{_default_base}/v1/chat/completions")
OLLAMA_HEALTH_URL = os.getenv("OLLAMA_HEALTH_URL", f"{_default_base}/api/tags")
if _detected and _detected != "http://127.0.0.1:11434":
    log.info(f"Ollama detected at {_detected} (default port 11434 unreachable)")
OLLAMA_CONNECT_TIMEOUT = int(os.getenv("OLLAMA_CONNECT_TIMEOUT", "8"))

# Preferred display order when auto-picking an installed model
_OLLAMA_MODEL_ORDER = [
    "qwen2.5-coder:7b",
    "deepseek-r1:7b",
    "qwen3:32b",
    "qwen3-coder-next:latest",
    "gemma4:e4b",
    "qwen2.5:3b",
    "llama3.2:3b",
]
_EXCLUDE_MODEL_PATTERNS = ["embed", "ocr", "clip", "vision-only", "whisper"]


def _fetch_ollama_model_names(timeout: float = 1.5) -> list[str]:
    try:
        import requests as http_requests
        r = http_requests.get(OLLAMA_HEALTH_URL, timeout=timeout)
        if r.status_code != 200:
            return []
        raw = r.json().get("models", [])
        return [
            m.get("name", "")
            for m in raw
            if m.get("name")
            and not any(p in m.get("name", "").lower() for p in _EXCLUDE_MODEL_PATTERNS)
        ]
    except Exception:
        return []


def resolve_ollama_model(requested: str | None = None) -> str:
    """Pick an installed Ollama model; fall back if configured default is missing."""
    want = (requested or OLLAMA_MODEL).strip()
    installed = _fetch_ollama_model_names()
    if not installed:
        return want
    if want in installed:
        return want
    for name in _OLLAMA_MODEL_ORDER:
        if name in installed:
            if name != want:
                log.warning(
                    f"Ollama model '{want}' not installed — using '{name}' instead. "
                    f"Installed: {', '.join(installed[:6])}"
                )
            return name
    fallback = installed[0]
    log.warning(f"Ollama model '{want}' not installed — using '{fallback}'")
    return fallback


def ollama_reachable(timeout: float = 1.5) -> bool:
    """Quick health probe — used by /models and /llm-status."""
    try:
        import requests as http_requests
        r = http_requests.get(OLLAMA_HEALTH_URL, timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def normalize_llm_prefer(prefer: str) -> tuple[str, str | None]:
    """Return (provider_key, ollama_model_or_none). Accepts legacy 'ollama' bare id."""
    if not prefer:
        return "ollama", None
    if prefer.startswith("ollama/"):
        model = prefer[len("ollama/"):].strip()
        return "ollama", model or None
    if prefer.startswith("claude/"):
        return "claude", None
    if prefer == "ollama":
        return "ollama", None
    if prefer == "claude":
        return "claude", None
    # Unknown — treat bare string as Ollama model name for backward compat
    if "/" not in prefer:
        return "ollama", prefer
    return prefer.split("/", 1)[0], None


def get_anthropic_key():
    # Read from env at call time so POST /set-claude-key hot-reload works
    return os.environ.get("ANTHROPIC_API_KEY", "")


def call_ollama(messages: list, temperature: float = 0.3, timeout: int = 600,
                model: str = None, connect_timeout: int = None) -> str:
    import requests as http_requests  # lazy — clean_json-only consumers skip the dep
    active_model = resolve_ollama_model(model)
    conn_to = connect_timeout if connect_timeout is not None else OLLAMA_CONNECT_TIMEOUT
    t0 = _time.time()
    log.debug(f"Calling Ollama — model={active_model} messages={len(messages)} temp={temperature}")
    data = {"model": active_model, "messages": messages, "stream": False, "temperature": temperature}
    try:
        response = http_requests.post(
            OLLAMA_API_URL, json=data, timeout=(conn_to, timeout),
        )
    except http_requests.exceptions.ConnectTimeout as e:
        raise RuntimeError(
            f"Ollama not responding at {OLLAMA_API_URL} (connection timed out after {conn_to}s). "
            "Open the Ollama app or run `ollama serve`, then confirm with `ollama list`."
        ) from e
    except http_requests.exceptions.ConnectionError as e:
        raise RuntimeError(
            f"Cannot reach Ollama at {OLLAMA_API_URL}. Start Ollama and ensure model "
            f"'{active_model}' is pulled (`ollama pull {active_model}`)."
        ) from e
    if response.status_code == 404:
        raise RuntimeError(
            f"Ollama model '{active_model}' not found. Run `ollama pull {active_model}`."
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(str(payload["error"]))
    try:
        result = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Ollama response shape: {payload!r}") from e
    log_event(log, "INFO", "llm_call", provider="ollama", model=active_model,
              latency_ms=int((_time.time()-t0)*1000), response_chars=len(result))
    return result


def call_claude(messages: list, temperature: float = 0.3, system: str = "") -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "Claude support is not installed. Run `python3 -m pip install -r backend/requirements-optional.txt`."
        ) from exc
    t0 = _time.time()
    api_key = get_anthropic_key()
    if not api_key:
        log.error("call_claude: ANTHROPIC_API_KEY is not set")
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    log.debug(f"Calling Claude — messages={len(messages)} temp={temperature}")
    client = anthropic.Anthropic(api_key=api_key)
    # Convert OpenAI-format messages; extract system if present
    claude_messages = []
    sys_content = system
    for m in messages:
        if m["role"] == "system":
            sys_content = (sys_content + "\n" + m["content"]).strip()
        else:
            claude_messages.append({"role": m["role"], "content": m["content"]})
    kwargs = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "messages": claude_messages,
    }
    if sys_content:
        kwargs["system"] = sys_content
    message = client.messages.create(**kwargs)
    result = message.content[0].text
    log_event(log, "INFO", "llm_call", provider="claude", model="claude-sonnet-4-6",
              latency_ms=int((_time.time()-t0)*1000), response_chars=len(result))
    return result


def call_llm(messages: list, temperature: float = 0.3, system: str = "",
             prefer: str = "ollama", timeout: int = 600, model: str = None) -> str:
    """Try preferred provider first, auto-fallback to the other.

    prefer: "ollama" | "claude" | "ollama/<model-name>" (e.g. "ollama/llama3:8b")
    model: explicit Ollama model name override (overrides OLLAMA_MODEL default)
    """
    # Parse provider and optional model from prefer string like "ollama/llama3:8b"
    provider_key, parsed_model = normalize_llm_prefer(prefer or "ollama")
    ollama_model_override = model or parsed_model

    providers = ["claude", "ollama"] if provider_key == "claude" else ["ollama", "claude"]
    last_err = None
    for provider in providers:
        try:
            if provider == "ollama":
                return call_ollama(messages, temperature, timeout, model=ollama_model_override)
            else:
                return call_claude(messages, temperature, system)
        except Exception as e:
            last_err = e
            log.warning(f"LLM provider '{provider}' failed — {e}. Trying next...")
    log.error(f"All LLM providers failed. Last error: {last_err}")
    raise RuntimeError(f"All LLM providers failed. Last error: {last_err}")


def clean_json(raw: str) -> str:
    """Robustly extract the first valid JSON object or array from LLM output.
    Handles: fenced blocks (```json / ```JSON / ```), preamble text, postamble text,
    multiple code blocks, and truncated JSON gracefully.
    """
    if not raw:
        return raw
    # 1. Try fenced code blocks first (handles ```json, ```JSON, ```)
    fence_pattern = re.compile(r'```(?:json|JSON)?\s*\n?(.*?)```', re.DOTALL)
    for match in fence_pattern.finditer(raw):
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            continue
    # 2. Depth-track to find the first balanced { or [ — handles preamble/postamble.
    # Try whichever opener appears FIRST in the text, so a root array like [{"x":1}]
    # returns the whole array instead of the inner object.
    raw_s = raw.strip()
    obj_at = raw_s.find('{')
    arr_at = raw_s.find('[')
    if arr_at != -1 and (obj_at == -1 or arr_at < obj_at):
        bracket_order = [('[', ']'), ('{', '}')]
    else:
        bracket_order = [('{', '}'), ('[', ']')]
    for start_ch, end_ch in bracket_order:
        start = raw_s.find(start_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(raw_s[start:], start):
            if esc:
                esc = False; continue
            if ch == '\\' and in_str:
                esc = True; continue
            if ch == '"' and not esc:
                in_str = not in_str; continue
            if in_str:
                continue
            if ch == start_ch: depth += 1
            elif ch == end_ch:
                depth -= 1
                if depth == 0:
                    candidate = raw_s[start:i+1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except (json.JSONDecodeError, ValueError):
                        break
    return raw_s  # last resort — let caller handle json.loads error
