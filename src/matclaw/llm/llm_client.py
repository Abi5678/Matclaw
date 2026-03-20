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
                return (resp.choices[0].message.content if resp.choices else "").strip()

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
