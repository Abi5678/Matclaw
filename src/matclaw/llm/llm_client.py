from __future__ import annotations

import os
import time
from typing import Any, AsyncGenerator

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover
    Anthropic = None  # type: ignore[misc, assignment]

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover
    genai = None  # type: ignore[assignment]

try:
    from openai import OpenAI, AsyncOpenAI
except ImportError:  # pragma: no cover
    OpenAI = None  # type: ignore[misc, assignment]
    AsyncOpenAI = None  # type: ignore[misc, assignment]

NVIDIA_API_BASE = "https://integrate.api.nvidia.com/v1"


def resolve_api_key(provider: str, api_key: str | None = None) -> str | None:
    if api_key:
        return api_key
    p = (provider or "").lower().strip()
    if p == "nvidia":
        return os.environ.get("NVIDIA_API_KEY")
    if p == "google":
        return os.environ.get("GOOGLE_API_KEY")
    if p == "openai-compatible":
        return None  # must be passed explicitly
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
    base_url: str | None = None,
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
                # For reasoning models: if content is empty, use reasoning trace
                if not content.strip() and resp.choices:
                    reasoning = getattr(resp.choices[0].message, "reasoning_content", None) or ""
                    if reasoning:
                        # First try to extract a JSON block (for structured responses)
                        json_match = _re.search(r"\{[^{}]*\"reply\"[^{}]*\}", reasoning, _re.DOTALL)
                        if json_match:
                            content = json_match.group()
                        else:
                            # Try extracting code blocks
                            code = _extract_code_from_reasoning(reasoning)
                            if code:
                                content = code
                            else:
                                # Last resort: return the last substantial paragraph
                                paragraphs = [p.strip() for p in reasoning.split("\n\n") if p.strip()]
                                content = paragraphs[-1] if paragraphs else reasoning
                return content.strip()

            if p == "openai-compatible":
                if OpenAI is None:
                    raise RuntimeError("openai package not installed.")
                if not base_url:
                    raise RuntimeError("base_url is required for openai-compatible provider.")
                client = OpenAI(base_url=base_url, api_key=key)
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


async def call_chat_completion_stream(
    *,
    provider: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> AsyncGenerator[dict[str, str], None]:
    """
    Async streaming variant — yields {"type": "thinking"|"text", "token": "..."} chunks.
    Currently supports NVIDIA (OpenAI-compatible). Falls back to non-streaming for others.
    """
    key = resolve_api_key(provider, api_key)
    if not key:
        raise RuntimeError("Missing API key for provider.")
    p = (provider or "").lower().strip()

    if p in ("nvidia", "openai-compatible"):
        if AsyncOpenAI is None:
            raise RuntimeError("openai package not installed.")
        stream_base = NVIDIA_API_BASE if p == "nvidia" else base_url
        if p == "openai-compatible" and not stream_base:
            raise RuntimeError("base_url is required for openai-compatible provider.")
        client = AsyncOpenAI(base_url=stream_base, api_key=key)
        openai_messages = [{"role": "system", "content": system}]
        openai_messages.extend(messages)
        stream = await client.chat.completions.create(
            model=model,
            messages=openai_messages,
            max_tokens=max_tokens,
            stream=True,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            reasoning_tok = getattr(delta, "reasoning_content", None)
            if reasoning_tok:
                yield {"type": "thinking", "token": reasoning_tok}
            content_tok = getattr(delta, "content", None)
            if content_tok:
                yield {"type": "text", "token": content_tok}
        return

    # For non-OpenAI providers, fall back to sync call and yield as single chunk
    result = call_chat_completion(
        provider=provider, model=model, system=system,
        messages=messages, api_key=api_key, base_url=base_url, max_tokens=max_tokens,
    )
    if result:
        yield {"type": "text", "token": result}


async def call_chat_with_tools(
    *,
    provider: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],   # OpenAI-normalized history (see note below)
    tools: list[dict[str, Any]],       # OpenAI function-calling schema
    api_key: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """
    Call LLM with tool definitions.  Returns a unified dict:
      {
        "text":       str,            # any prose the model produced (may be empty)
        "tool_calls": [               # list may be empty when model is just replying
          {"id": str, "name": str, "inputs": dict}
        ]
      }

    Internal history is kept in OpenAI wire format.
    For Anthropic we convert on the fly before the API call.

    Supported providers:  nvidia, openai-compatible, anthropic
    Google: no function-calling support yet — falls back to plain text.
    """
    key = resolve_api_key(provider, api_key)
    if not key:
        raise RuntimeError("Missing API key for provider.")
    p = (provider or "").lower().strip()

    # ── Anthropic ────────────────────────────────────────────────────────────
    if p == "anthropic":
        if Anthropic is None:
            raise RuntimeError("anthropic package not installed.")

        def _to_anthropic_tools(oai_tools: list[dict]) -> list[dict]:
            """Convert OpenAI tool schema → Anthropic tool schema."""
            out = []
            for t in oai_tools:
                fn = t.get("function", t)
                out.append({
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
                })
            return out

        def _to_anthropic_messages(msgs: list[dict]) -> list[dict]:
            """Convert OpenAI-format history → Anthropic message list."""
            result: list[dict] = []
            i = 0
            while i < len(msgs):
                m = msgs[i]
                role = m.get("role", "user")

                if role == "system":
                    i += 1
                    continue  # system handled separately

                if role == "assistant":
                    content: list[dict] | str = []
                    text_part = m.get("content") or ""
                    if text_part:
                        content.append({"type": "text", "text": text_part})  # type: ignore[union-attr]
                    for tc in m.get("tool_calls", []):
                        fn = tc.get("function", {})
                        import json as _json
                        raw_args = fn.get("arguments", "{}")
                        try:
                            inp = _json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except Exception:
                            inp = {"raw": raw_args}
                        content.append({  # type: ignore[union-attr]
                            "type": "tool_use",
                            "id": tc.get("id", f"call_{i}"),
                            "name": fn.get("name", "unknown"),
                            "input": inp,
                        })
                    result.append({"role": "assistant", "content": content or text_part})
                    i += 1

                elif role == "tool":
                    # Collect consecutive tool results into a single user message
                    tool_results = []
                    while i < len(msgs) and msgs[i].get("role") == "tool":
                        tr = msgs[i]
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tr.get("tool_call_id", ""),
                            "content": str(tr.get("content", "")),
                        })
                        i += 1
                    result.append({"role": "user", "content": tool_results})

                else:
                    result.append({"role": "user", "content": m.get("content", "")})
                    i += 1
            return result

        client = Anthropic(api_key=key)
        anthropic_tools = _to_anthropic_tools(tools)
        anthropic_msgs  = _to_anthropic_messages(messages)

        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=anthropic_msgs,
            tools=anthropic_tools,
            tool_choice={"type": "auto"},
        )

        text = ""
        tool_calls = []
        for block in resp.content:
            if hasattr(block, "text"):
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "inputs": block.input or {},
                })
        return {"text": text.strip(), "tool_calls": tool_calls}

    # ── NVIDIA / OpenAI-compatible ────────────────────────────────────────────
    if p in ("nvidia", "openai-compatible"):
        if OpenAI is None:
            raise RuntimeError("openai package not installed.")
        import json as _json
        stream_base = NVIDIA_API_BASE if p == "nvidia" else base_url
        if p == "openai-compatible" and not stream_base:
            raise RuntimeError("base_url required for openai-compatible provider.")
        client = OpenAI(base_url=stream_base, api_key=key)
        openai_messages = [{"role": "system", "content": system}, *messages]
        resp = client.chat.completions.create(
            model=model,
            messages=openai_messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=max_tokens,
        )
        msg = resp.choices[0].message if resp.choices else None
        text = (msg.content or "") if msg else ""
        tool_calls = []
        for tc in (getattr(msg, "tool_calls", None) or []):
            raw = tc.function.arguments or "{}"
            try:
                inp = _json.loads(raw)
            except Exception:
                inp = {"raw": raw}
            tool_calls.append({"id": tc.id, "name": tc.function.name, "inputs": inp})
        return {"text": text.strip(), "tool_calls": tool_calls}

    # ── Google / fallback: no tool support ───────────────────────────────────
    result = call_chat_completion(
        provider=provider, model=model, system=system,
        messages=messages, api_key=api_key, base_url=base_url, max_tokens=max_tokens,
    )
    return {"text": result, "tool_calls": []}


__all__ = ["call_chat_completion", "call_chat_completion_stream",
           "call_chat_with_tools", "resolve_api_key", "NVIDIA_API_BASE"]
