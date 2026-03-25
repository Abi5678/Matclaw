from __future__ import annotations

import os
import time
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore[misc, assignment]

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover
    genai = None  # type: ignore[assignment]

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[misc, assignment]

NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"


def resolve_api_key(provider: str, api_key: str | None = None) -> str | None:
    if api_key:
        return api_key
    p = (provider or "").lower().strip()
    if p == "nvidia":
        return os.environ.get("NVIDIA_API_KEY")
    if p == "google":
        return os.environ.get("GOOGLE_API_KEY")
    return os.environ.get("ANTHROPIC_API_KEY")


import re as _re


def _extract_code_from_reasoning(reasoning: str) -> str:
    """
    For reasoning models that spend all tokens thinking and produce no content,
    extract the last coherent code block from the reasoning trace.
    Looks for multi-line sequences that look like code (assignments, loops, function calls).
    """
    if not reasoning:
        return ""
    # Find all fenced code blocks first
    fenced = _re.findall(r"```(?:matlab)?\s*\n(.*?)```", reasoning, _re.DOTALL | _re.IGNORECASE)
    if fenced:
        return fenced[-1].strip()
    # Fall back: find the last run of lines that look like MATLAB code
    # (contains assignment = , function calls, for/while/if, or semicolons)
    lines = reasoning.split("\n")
    code_lines: list[str] = []
    best_block: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (stripped and
            not stripped.startswith("#") and
            not stripped.startswith("//") and
            (_re.search(r"[\w\.]+\s*=\s*", stripped) or
             _re.search(r"\b(for|while|if|end|figure|plot|surf|mesh|subplot|xlabel|ylabel|title|drawnow|hold)\b", stripped) or
             stripped.endswith(";"))):
            code_lines.append(line)
        else:
            if len(code_lines) > len(best_block):
                best_block = code_lines[:]
            code_lines = []
    if len(code_lines) > len(best_block):
        best_block = code_lines
    return "\n".join(best_block).strip()


def call_chat_completion(
    *,
    provider: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    max_tokens: int = 1024,
    retries: int = 3,
    backoff_seconds: float = 0.6,
) -> str:
    """
    Unified chat completion across NVIDIA(OpenAI-compatible), Google, and Anthropic.
    Returns plain text response.
    """
    key = resolve_api_key(provider, api_key)
    if not key:
        raise RuntimeError("Missing API key for provider.")
    p = (provider or "").lower().strip()

    attempts = max(1, int(retries))
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if p == "nvidia":
                if OpenAI is None:
                    raise RuntimeError("openai package not installed.")
                client = OpenAI(base_url=NVIDIA_API_BASE, api_key=key)
                openai_messages = [{"role": "system", "content": system}]
                openai_messages.extend(messages)
                resp = client.chat.completions.create(
                    model=model,
                    messages=openai_messages,
                    max_tokens=max_tokens,
                )
                content = (resp.choices[0].message.content if resp.choices else "") or ""
                # For reasoning models: if content is empty, extract code from reasoning trace
                if not content.strip() and resp.choices:
                    reasoning = getattr(resp.choices[0].message, "reasoning_content", None) or ""
                    content = _extract_code_from_reasoning(reasoning)
                return content.strip()

            if p == "google":
                if genai is None:
                    raise RuntimeError("google-generativeai package not installed.")
                genai.configure(api_key=key)
                m = genai.GenerativeModel(model)
                parts = [system, "\n\n"]
                for msg in messages:
                    role = msg.get("role", "user")
                    content = msg.get("content", "")
                    parts.append(f"{role.upper()}: {content}\n")
                prompt = "".join(parts)
                r = m.generate_content(prompt)
                return (r.text or "").strip()

            if Anthropic is None:
                raise RuntimeError("anthropic package not installed.")
            client = Anthropic(api_key=key)
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return (resp.content[0].text if resp.content else "").strip()
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            sleep_for = backoff_seconds * (2 ** (attempt - 1))
            time.sleep(max(0.0, sleep_for))
    assert last_exc is not None
    raise last_exc


__all__ = ["call_chat_completion", "resolve_api_key", "NVIDIA_API_BASE"]
