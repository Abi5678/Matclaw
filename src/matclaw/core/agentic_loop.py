"""
Agentic loop engine — Phase 1 of MatClaw autonomous capabilities.

Replaces the single-pass LLM→execute flow with an iterative observe-think-act
cycle where the LLM decides which tool to call next after seeing each result.

Flow:
  1. Build tool schema (pluggable tools + virtual runtime tools)
  2. Call LLM with tools → get (text, tool_calls)
  3. For each tool_call: dispatch → collect result
  4. Append result to conversation history
  5. Repeat until LLM returns no tool_calls or max_iterations reached
  6. Yield SSE events throughout for live UI updates
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Callable

from matclaw.llm.llm_client import call_chat_with_tools, resolve_api_key
from matclaw.tools.base import ToolContext
from matclaw.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# ── Agentic system prompt ─────────────────────────────────────────────────────

AGENTIC_SYSTEM = """You are MatClaw, an autonomous AI agent with full control over a MATLAB workspace.

You are running in AGENTIC MODE. Use the provided tools to complete tasks step by step.

RULES:
- Call tools one at a time — think before each call
- For MATLAB computations, plotting, or simulations: use run_matlab with complete, self-contained code
- For data processing or parsing: use run_python
- For system tasks or file listing: use run_shell
- For fetching data from a URL: use web_fetch
- For reading or writing local files: use file_ops
- After each tool result, evaluate what to do next
- When the task is fully complete, provide a clear natural-language summary
- Do NOT output JSON objects like {"reply":..., "action":..., "code":...} — just use tools and respond naturally
- If a tool fails, adapt: try a different approach or simplify the code

QUALITY STANDARDS:
- MATLAB plots must have meaningful titles, axis labels, and grids
- Simulations must use physically realistic parameters
- Never call undefined external functions — implement everything inline
"""

# ── Virtual tool schemas for the 3 execution runtimes ────────────────────────
# These don't live in ToolRegistry but the LLM should know about them.

_RUNTIME_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_matlab",
            "description": (
                "Execute MATLAB code in the live MATLAB workspace. "
                "Use for numerical computation, signal processing, control systems, "
                "simulations, and plotting. Returns stdout/stderr and any plot URLs. "
                "Always write complete, self-contained code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Complete MATLAB code to execute. Must be self-contained.",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute Python code. Use for data wrangling, CSV/JSON parsing, "
                "mathematical preprocessing, or tasks where Python is more appropriate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Complete Python code to execute.",
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": (
                "Execute a shell command. Use for file listing, environment checks, "
                "or invoking command-line tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to run.",
                    }
                },
                "required": ["command"],
            },
        },
    },
]


def _sse(event: str, data: dict) -> str:
    """Format a single SSE frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _build_tool_schema(tool_registry: ToolRegistry) -> list[dict]:
    """Combine pluggable tools + virtual runtime tools into one schema list."""
    return _RUNTIME_TOOL_SCHEMAS + tool_registry.to_llm_schema()


def _format_tool_result(name: str, result: Any, plots: list[str] | None = None) -> str:
    """Serialise a tool result to a compact string for the conversation history."""
    if isinstance(result, dict):
        content = json.dumps(result, ensure_ascii=False)[:8000]
    elif isinstance(result, str):
        content = result[:8000]
    else:
        content = str(result)[:8000]
    if plots:
        content += f"\n[Plots generated: {', '.join(plots)}]"
    return content


async def run_agentic_loop(
    *,
    user_text: str,
    history: list[dict],          # [{role, text}] from the frontend
    settings: Any,                 # MatClawSettings
    tool_registry: ToolRegistry,
    runtime_dispatcher: Callable,  # async fn(name, inputs) → (output_str, plots)
    max_iterations: int | None = None,
) -> AsyncGenerator[str, None]:
    """
    The observe-think-act loop. Yields SSE-formatted strings suitable for
    streaming directly to the frontend EventSource.

    SSE events emitted:
      agent_thought  — LLM reasoning text (before tool calls)
      agent_step     — about to call a tool  {step, tool, inputs, label}
      agent_result   — tool result received  {step, tool, success, output, plots}
      tool_start     — reuses existing frontend handler for spinner
      tool_result    — reuses existing frontend handler for plots/output
      text           — final prose answer token
      done           — loop complete
      error          — unrecoverable failure
    """

    max_iter = max_iterations or getattr(settings.agentic, "max_iterations", 10)
    max_tok  = getattr(settings.agentic, "max_tokens_per_step", 4096)
    provider = settings.llm.provider
    model    = settings.llm.model

    tools_schema = _build_tool_schema(tool_registry)

    # Build OpenAI-format conversation history from frontend [{role, text}]
    messages: list[dict] = []
    for m in (history or []):
        role = m.get("role", "user")
        text = m.get("text") or m.get("content") or ""
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})

    # Append the current user turn
    messages.append({"role": "user", "content": user_text})

    step = 0
    all_plots: list[str] = []
    final_text = ""

    for iteration in range(1, max_iter + 1):
        step += 1

        # ── LLM call ─────────────────────────────────────────────────────────
        response = await call_chat_with_tools(
            provider=provider,
            model=model,
            system=AGENTIC_SYSTEM,
            messages=messages,
            tools=tools_schema,
            api_key=None,
            max_tokens=max_tok,
        )

        llm_text: str       = response.get("text", "")
        tool_calls: list    = response.get("tool_calls", [])

        # ── Emit LLM prose if any ─────────────────────────────────────────────
        if llm_text:
            yield _sse("agent_thought", {"step": step, "text": llm_text})
            final_text = llm_text  # keep last prose as candidate final answer

        # ── No tool calls → LLM is done ───────────────────────────────────────
        if not tool_calls:
            break

        # ── Dispatch tool calls ───────────────────────────────────────────────
        # Build assistant message with tool_calls for history
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": llm_text or None}
        oai_tool_calls = []
        for tc in tool_calls:
            oai_tool_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["inputs"]),
                },
            })
        assistant_msg["tool_calls"] = oai_tool_calls
        messages.append(assistant_msg)

        for tc in tool_calls:
            tool_name  = tc["name"]
            tool_id    = tc["id"]
            tool_inputs = tc["inputs"]

            label = f"Step {step}: {tool_name}({', '.join(f'{k}=…' for k in list(tool_inputs.keys())[:2])})"
            yield _sse("agent_step", {
                "step": step, "tool": tool_name,
                "inputs": tool_inputs, "label": label,
            })
            # Also emit tool_start so the existing StreamingMessage spinner fires
            yield _sse("tool_start", {"action": tool_name, "label": label})

            tc_output  = ""
            tc_plots: list[str] = []
            tc_success = True

            try:
                tc_output, tc_plots = await runtime_dispatcher(tool_name, tool_inputs)
                all_plots.extend(tc_plots)
            except Exception as exc:
                tc_output  = f"Tool error: {exc}"
                tc_success = False
                logger.warning("Agentic tool dispatch failed [%s]: %s", tool_name, exc)

            # Emit result events (reuse tool_result for plots/output rendering)
            yield _sse("agent_result", {
                "step": step, "tool": tool_name,
                "success": tc_success, "output": tc_output[:2000],
                "plots": tc_plots,
            })
            yield _sse("tool_result", {
                "output": tc_output,
                "plots": tc_plots,
                "files": [],
                # include code if it was a run_matlab call
                **({"code": tool_inputs.get("code", "")} if tool_name == "run_matlab" else {}),
            })

            # Append tool result to conversation history (OpenAI format)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "content": _format_tool_result(tool_name, tc_output, tc_plots),
            })

        step += 1

    else:
        # max_iterations exhausted
        yield _sse("agent_thought", {
            "step": step,
            "text": f"Reached maximum {max_iter} iterations. Summarising progress…",
        })

    # ── If no prose reply yet, ask for a final summary ───────────────────────
    if not final_text.strip():
        try:
            messages.append({
                "role": "user",
                "content": "All steps complete. Provide a concise markdown summary of what was accomplished and any key results or insights.",
            })
            summary_resp = await call_chat_with_tools(
                provider=provider,
                model=model,
                system=AGENTIC_SYSTEM,
                messages=messages,
                tools=[],          # no tools — just prose
                api_key=None,
                max_tokens=1024,
            )
            final_text = summary_resp.get("text", "").strip() or "Task complete."
        except Exception as _e:
            logger.debug("Summary call failed: %s", _e)
            final_text = "Task complete."

    # ── Final done event ──────────────────────────────────────────────────────
    yield _sse("done", {
        "skill": "agentic",
        "elapsed_ms": 0,
        "reply": final_text,
        "plots": all_plots,
        "files": [],
    })


__all__ = ["run_agentic_loop", "AGENTIC_SYSTEM"]
