"""LLM helper for matcher fit stage."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests as http_requests


OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434/v1/chat/completions")


def call_ollama(messages: list[dict[str, str]], temperature: float = 0.2, timeout: int = 600) -> str:
    payload = {"model": OLLAMA_MODEL, "messages": messages, "stream": False, "temperature": temperature}
    response = http_requests.post(OLLAMA_API_URL, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def call_claude(messages: list[dict[str, str]], system: str = "") -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package is not installed for Claude fallback") from exc

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    claude_messages: list[dict[str, str]] = []
    sys_content = system
    for msg in messages:
        if msg["role"] == "system":
            sys_content = f"{sys_content}\n{msg['content']}".strip()
        else:
            claude_messages.append({"role": msg["role"], "content": msg["content"]})

    kwargs: dict[str, Any] = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "messages": claude_messages,
    }
    if sys_content:
        kwargs["system"] = sys_content
    result = client.messages.create(**kwargs)
    return result.content[0].text


def call_llm(
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    prefer: str = "ollama",
    system: str = "",
) -> str:
    providers = ["claude", "ollama"] if prefer == "claude" else ["ollama", "claude"]
    last_error: Exception | None = None
    for provider in providers:
        try:
            if provider == "ollama":
                return call_ollama(messages=messages, temperature=temperature)
            return call_claude(messages=messages, system=system)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")


def clean_json(raw: str) -> str:
    if not raw:
        return raw
    fence_pattern = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)
    for match in fence_pattern.finditer(raw):
        candidate = match.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except Exception:  # noqa: BLE001
            continue
    raw_s = raw.strip()
    for start_ch, end_ch in [("{", "}"), ("[", "]")]:
        start = raw_s.find(start_ch)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(raw_s[start:], start):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == "\"" and not esc:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == start_ch:
                depth += 1
            elif ch == end_ch:
                depth -= 1
                if depth == 0:
                    candidate = raw_s[start : i + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except Exception:  # noqa: BLE001
                        break
    return raw_s

