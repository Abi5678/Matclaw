from __future__ import annotations

import asyncio
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


def _usage_to_dict(u: Any) -> dict[str, int] | None:
    """Normalize provider usage objects to prompt/completion/total token counts."""
    if u is None:
        return None
    if isinstance(u, dict):
        pt = int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
        ct = int(u.get("completion_tokens") or u.get("output_tokens") or 0)
        ext = u.get("total_tokens")
    else:
        pt = int(getattr(u, "prompt_tokens", None) or getattr(u, "input_tokens", None) or 0)
        ct = int(getattr(u, "completion_tokens", None) or getattr(u, "output_tokens", None) or 0)
        ext = getattr(u, "total_tokens", None)
    total = int(ext) if ext is not None else pt + ct
    if pt == 0 and ct == 0 and total == 0:
        return None
    return {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": total}


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
    if p == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    import logging as _log
    _log.getLogger(__name__).warning("Unknown LLM provider %r in resolve_api_key; returning None", provider)
    return None


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


def _parse_embedded_tool_calls(text: str) -> list[dict[str, Any]]:
    """
    Fallback parser for models that 'hallucinate' tool calls into the message
    content instead of using the structured tool_calls field.
    Handles:
      1. OpenAI-style JSON array: `[{"name": "...", "arguments": {...}}]`
      2. XML-like tags: `<tool_call name="...">...</tool_call>`
    """
    import json as _json
    import re as _re
    import logging as _logging
    _log = _logging.getLogger(__name__)
    tool_calls: list[dict[str, Any]] = []

    # Format A: JSON Array — bracket-aware extraction.
    # Find the outermost `[{...}]` by tracking bracket depth,
    # which handles nested brackets inside MATLAB code strings.
    def _extract_json_array(s: str) -> str | None:
        start = s.find('[{')
        if start == -1:
            return None
        depth = 0
        for idx in range(start, len(s)):
            ch = s[idx]
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return s[start:idx + 1]
        return None

    json_blob = _extract_json_array(text)
    if json_blob:
        try:
            raw_list = _json.loads(json_blob)
            if isinstance(raw_list, list):
                for i, item in enumerate(raw_list):
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name")
                    args = item.get("arguments") or item.get("inputs") or {}
                    if name:
                        if isinstance(args, str):
                            try:
                                args = _json.loads(args)
                            except Exception:
                                args = {"raw": args}
                        tool_calls.append({
                            "id": f"fallback_{int(time.time())}_{i}",
                            "name": name,
                            "inputs": args if isinstance(args, dict) else {},
                        })
        except (ValueError, TypeError, _json.JSONDecodeError) as exc:
            _log.debug("Embedded JSON tool-call parse failed: %s", exc)

    if tool_calls:
        _log.debug("Extracted %d embedded tool call(s) from text (JSON format)", len(tool_calls))
        return tool_calls

    # Format B: XML-like tags (common in some Nemotron/Llama variants)
    # <tool_call name="run_matlab">{"code": "..."}</tool_call>
    xml_matches = _re.finditer(r"<tool_call\s+name=\"(.*?)\">(.*?)</tool_call>", text, _re.DOTALL)
    for i, match in enumerate(xml_matches):
        name = match.group(1)
        raw_args = match.group(2).strip()
        try:
            args = _json.loads(raw_args)
        except Exception:
            args = {"raw": raw_args}
        tool_calls.append({
            "id": f"xml_{int(time.time())}_{i}",
            "name": name,
            "inputs": args,
        })

    if tool_calls:
        _log.debug("Extracted %d embedded tool call(s) from text (XML format)", len(tool_calls))

    return tool_calls


def _strip_embedded_json_array(text: str) -> str:
    """Remove the outermost `[{...}]` JSON blob from text using bracket depth tracking."""
    start = text.find('[{')
    if start == -1:
        return text
    depth = 0
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                return (text[:start] + text[idx + 1:]).strip()
    return text


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
                                paragraphs = [para.strip() for para in reasoning.split("\n\n") if para.strip()]
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
                try:
                    return (r.text or "").strip()
                except ValueError:
                    if r.candidates:
                        parts = r.candidates[0].content.parts
                        return (parts[0].text if parts else "").strip()
                    return ""

            if Anthropic is None:
                raise RuntimeError("anthropic package not installed.")
            client = Anthropic(api_key=key)
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return (getattr(resp.content[0], "text", "") if resp.content else "").strip()
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
        try:
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
        finally:
            await stream.close()
        return

    # For non-OpenAI providers, fall back to sync call in a thread to avoid blocking the event loop
    result = await asyncio.to_thread(
        call_chat_completion,
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
    retries: int = 3,
    backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    """
    Call LLM with tool definitions.      Returns a unified dict:
      {
        "text":       str,            # any prose the model produced (may be empty)
        "tool_calls": [               # list may be empty when model is just replying
          {"id": str, "name": str, "inputs": dict}
        ],
        "usage":      optional {prompt_tokens, completion_tokens, total_tokens}
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

    attempts = max(1, int(retries))
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
      try:
        return await _call_chat_with_tools_once(
            provider=p, model=model, system=system, messages=messages,
            tools=tools, api_key=key, base_url=base_url, max_tokens=max_tokens,
        )
      except Exception as exc:
        last_exc = exc
        if attempt >= attempts:
            break
        sleep_for = backoff_seconds * (2 ** (attempt - 1))
        await asyncio.sleep(sleep_for)

    assert last_exc is not None
    raise last_exc


async def _call_chat_with_tools_once(
    *,
    provider: str,
    model: str,
    system: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Single-attempt implementation of call_chat_with_tools."""
    p = provider
    key = api_key

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

        anthropic_tools = _to_anthropic_tools(tools)
        anthropic_msgs = _to_anthropic_messages(messages)

        def _anthropic_call():
            client = Anthropic(api_key=key)
            return client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=anthropic_msgs,
                tools=anthropic_tools,
                tool_choice={"type": "auto"},
            )

        resp = await asyncio.to_thread(_anthropic_call)

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
        usage = _usage_to_dict(getattr(resp, "usage", None))
        return {"text": text.strip(), "tool_calls": tool_calls, "usage": usage}

    # ── NVIDIA / OpenAI-compatible ────────────────────────────────────────────
    if p in ("nvidia", "openai-compatible"):
        if OpenAI is None:
            raise RuntimeError("openai package not installed.")
        import json as _json
        stream_base = NVIDIA_API_BASE if p == "nvidia" else base_url
        if p == "openai-compatible" and not stream_base:
            raise RuntimeError("base_url required for openai-compatible provider.")
        openai_messages = [{"role": "system", "content": system}, *messages]

        def _openai_tools_call():
            client = OpenAI(base_url=stream_base, api_key=key)
            return client.chat.completions.create(
                model=model,
                messages=openai_messages,
                tools=tools,
                tool_choice="auto",
                max_tokens=max_tokens,
            )

        resp = await asyncio.to_thread(_openai_tools_call)
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
        
        # Fallback: if no structured tool_calls found, but there's content text
        # some NVIDIA models (Nemotron) hallucinate tool calls into text.
        if not tool_calls and text:
            tool_calls = _parse_embedded_tool_calls(text)
            if tool_calls:
                cleaned = _strip_embedded_json_array(text)
                cleaned = _re.sub(r"<tool_call\s+name=\".*?\">(.*?)</tool_call>", "", cleaned, flags=_re.DOTALL)
                text = cleaned.strip()

        usage = _usage_to_dict(getattr(resp, "usage", None))
        return {"text": text.strip(), "tool_calls": tool_calls, "usage": usage}

    # ── Google / fallback: no tool support ───────────────────────────────────
    result = await asyncio.to_thread(
        call_chat_completion,
        provider=provider,
        model=model,
        system=system,
        messages=messages,
        api_key=api_key,
        base_url=base_url,
        max_tokens=max_tokens,
    )
    return {"text": result, "tool_calls": [], "usage": None}


__all__ = ["call_chat_completion", "call_chat_completion_stream",
           "call_chat_with_tools", "resolve_api_key", "NVIDIA_API_BASE"]
