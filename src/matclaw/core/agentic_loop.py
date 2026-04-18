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

from matclaw.core.spec_compliance import analyze_matlab_run
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
- For interactive charts (hover/zoom) from fetched data: use run_python with Plotly and fig.write_html under the plots/ folder (e.g. plots/heatmap.html) — MatClaw surfaces /plots/... URLs in the UI
- For system tasks or file listing: use run_shell
- For fetching data from a URL: use web_fetch
- For reading or writing local files: use file_ops
- After each tool result, evaluate what to do next
- When the task is fully complete, provide a clear natural-language summary
- Use ONLY the provided tool calling interface. Do not output raw JSON or Markdown-fenced JSON in the text message body.

CRITICAL — ERROR HANDLING:
- If a tool call returns an error, you MUST fix the code and call the tool again with corrected code.
- NEVER give up after one failure. Read the error message carefully, fix the exact issue, and retry.
- Common MATLAB fixes: use .* instead of * for elementwise ops, pre-allocate arrays, check matrix dimensions, use correct indexing.
- You have up to 10 iterations — use them. Only stop when the task succeeds or you've tried at least 3 different approaches.
- After a successful run that produces a plot, you may summarize and finish.

QUALITY STANDARDS:
- MATLAB plots must have meaningful titles, axis labels, and grids
- Simulations must use physically realistic parameters
- Never call undefined external functions — implement everything inline
- Always use .* ./ .^ for elementwise operations on vectors/matrices
- If the user asks for "base MATLAB" or "no toolboxes", do not use knnsearch, fitlm, pdist2, or other toolbox-only functions — implement intersections and search in plain MATLAB

FORMAT HINT:
Always invoke tools via the internal function-calling API. If you must output JSON, ensure it is within the 'tool_calls' field and not the conversational text.
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
    memory_preamble: str | None = None,
    session_id: str = "default",
    memory_manager: Any | None = None,
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

    import time as _time
    _mono0 = _time.monotonic()
    prod = getattr(settings, "production", None)
    wall_budget_s = float(getattr(prod, "max_agentic_wall_seconds", 600.0) or 600.0) if prod else 600.0
    # Tests may pass a fractional budget via a plain namespace bypassing pydantic ge=30.
    _wall_deadline = _mono0 + wall_budget_s
    cost_cap = float(getattr(prod, "soft_cost_cap_usd_per_task", 0.0) or 0.0) if prod else 0.0
    usd_1k_in = float(getattr(prod, "usd_per_1k_prompt_tokens", 0.0) or 0.0) if prod else 0.0
    usd_1k_out = float(getattr(prod, "usd_per_1k_completion_tokens", 0.0) or 0.0) if prod else 0.0
    cost_cap_enforced = cost_cap > 0.0 and (usd_1k_in > 0.0 or usd_1k_out > 0.0)

    def _wall_hit() -> bool:
        return _time.monotonic() >= _wall_deadline

    def _mono_elapsed_ms() -> int:
        return int((_time.monotonic() - _mono0) * 1000)

    usage_totals: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
    wall_abort = False
    cost_abort = False

    def _add_usage(u: dict[str, Any] | None) -> None:
        if not u:
            return
        usage_totals["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
        usage_totals["completion_tokens"] += int(u.get("completion_tokens") or 0)

    def _estimated_cost_usd() -> float:
        return (
            (usage_totals["prompt_tokens"] / 1000.0) * usd_1k_in
            + (usage_totals["completion_tokens"] / 1000.0) * usd_1k_out
        )

    max_iter = max_iterations or getattr(settings.agentic, "max_iterations", 10)
    max_tok  = getattr(settings.agentic, "max_tokens_per_step", 4096)
    provider = settings.llm.provider
    model    = settings.llm.model
    api_key  = settings.llm.api_key
    base_url = getattr(settings.llm, "base_url", None)

    tools_schema = _build_tool_schema(tool_registry)

    MAX_HISTORY_MESSAGES = 40

    messages: list[dict] = []
    for m in (history or []):
        role = m.get("role", "user")
        text = m.get("text") or m.get("content") or ""
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})

    messages.append({"role": "user", "content": user_text})
    if memory_preamble and memory_preamble.strip():
        u = messages[-1]["content"]
        messages[-1]["content"] = (
            f"{memory_preamble.strip()}\n\n---\n\n[Current user request]\n{u}"
        )

    step = 0
    all_plots: list[str] = []
    final_text = ""
    last_tool_failed = False
    retry_nudge_count = 0
    _MAX_NUDGES = 3
    last_matlab_spec: dict[str, Any] | None = None

    for iteration in range(1, max_iter + 1):
        step += 1

        if _wall_hit():
            wall_abort = True
            yield _sse("agent_thought", {
                "step": step,
                "text": "Wall-clock budget exceeded; stopping the agent loop.",
            })
            break

        # ── LLM call ─────────────────────────────────────────────────────────
        response = await call_chat_with_tools(
            provider=provider,
            model=model,
            system=AGENTIC_SYSTEM,
            messages=messages,
            tools=tools_schema,
            api_key=api_key,
            base_url=base_url,
            max_tokens=max_tok,
        )

        llm_text: str       = response.get("text", "")
        tool_calls: list    = response.get("tool_calls", [])
        _add_usage(response.get("usage"))

        if cost_cap_enforced and _estimated_cost_usd() > cost_cap:
            cost_abort = True
            yield _sse("agent_thought", {
                "step": step,
                "text": (
                    "Estimated LLM cost for this task exceeded the configured soft cap; "
                    "stopping further tool calls."
                ),
            })
            break

        if _wall_hit():
            wall_abort = True
            yield _sse("agent_thought", {
                "step": step,
                "text": "Wall-clock budget exceeded after the LLM step; not running tools.",
            })
            break

        # ── Emit LLM prose if any ─────────────────────────────────────────────
        if llm_text:
            yield _sse("agent_thought", {"step": step, "text": llm_text})
            final_text = llm_text  # keep last prose as candidate final answer

        # ── No tool calls → check if we should nudge or finish ───────────────
        if not tool_calls:
            if last_tool_failed and retry_nudge_count < _MAX_NUDGES:
                retry_nudge_count += 1
                messages.append({"role": "assistant", "content": llm_text or ""})
                messages.append({
                    "role": "user",
                    "content": (
                        "The previous tool call failed. You MUST fix the error and "
                        "call the tool again with corrected code. Do not give up — "
                        "read the error, fix the issue, and retry using the tool."
                    ),
                })
                logger.info("Agentic nudge %d/%d: re-prompting LLM to retry after failure", retry_nudge_count, _MAX_NUDGES)
                continue
            break

        # ── Dispatch tool calls ───────────────────────────────────────────────
        last_tool_failed = False

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

        tool_round_stopped = False
        for tc in tool_calls:
            if _wall_hit():
                wall_abort = True
                yield _sse("agent_thought", {
                    "step": step,
                    "text": "Wall-clock budget exceeded before running the next tool; stopping.",
                })
                tool_round_stopped = True
                break

            tool_name  = tc["name"]
            tool_id    = tc["id"]
            tool_inputs = tc["inputs"]
            _remain = _wall_deadline - _time.monotonic()
            _tool_timeout = max(0.05, min(120.0, _remain))

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
                tc_output, tc_plots = await asyncio.wait_for(
                    runtime_dispatcher(tool_name, tool_inputs),
                    timeout=_tool_timeout,
                )
                all_plots.extend(tc_plots)
                if tool_name in ("run_matlab", "run_python", "run_shell"):
                    if tc_output and any(
                        marker in tc_output
                        for marker in ("MATLAB error:", "execution failed", "Tool error:", "Traceback (most recent", "Error:", "Execution error:")
                    ):
                        tc_success = False
            except asyncio.TimeoutError:
                tc_output  = f"Tool '{tool_name}' timed out after 120s."
                tc_success = False
                logger.warning("Agentic tool dispatch timed out [%s]", tool_name)
            except Exception as exc:
                tc_output  = f"Tool error: {exc}"
                tc_success = False
                logger.warning("Agentic tool dispatch failed [%s]: %s", tool_name, exc)

            quality_payload: dict[str, Any] | None = None
            if tool_name == "run_matlab":
                mcode = tool_inputs.get("code") or ""
                spec = analyze_matlab_run(
                    code=mcode,
                    user_text=user_text,
                    tool_output_success=tc_success,
                    plots=tc_plots,
                )
                last_matlab_spec = spec.to_dict()
                quality_payload = last_matlab_spec

            # Emit result events (reuse tool_result for plots/output rendering)
            ar_data: dict[str, Any] = {
                "step": step, "tool": tool_name,
                "success": tc_success, "output": tc_output[:2000],
                "plots": tc_plots,
            }
            if quality_payload is not None:
                ar_data["quality"] = quality_payload
            yield _sse("agent_result", ar_data)
            yield _sse("tool_result", {
                "output": tc_output,
                "plots": tc_plots,
                "files": [],
                # include code if it was a run_matlab call
                **({"code": tool_inputs.get("code", "")} if tool_name == "run_matlab" else {}),
            })

            if not tc_success:
                last_tool_failed = True

            # Append tool result to conversation history (OpenAI format)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "content": _format_tool_result(tool_name, tc_output, tc_plots),
            })

        if tool_round_stopped:
            break

        if not last_tool_failed:
            retry_nudge_count = 0

        if len(messages) > MAX_HISTORY_MESSAGES:
            keep_first = messages[:1]
            messages = keep_first + messages[-(MAX_HISTORY_MESSAGES - 1):]

    else:
        # max_iterations exhausted
        yield _sse("agent_thought", {
            "step": step,
            "text": f"Reached maximum {max_iter} iterations. Summarising progress…",
        })

    # ── If no prose reply yet, ask for a final summary ───────────────────────
    if not final_text.strip() and not wall_abort and not cost_abort and not _wall_hit():
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
                api_key=api_key,
                base_url=base_url,
                max_tokens=1024,
            )
            _add_usage(summary_resp.get("usage"))
            final_text = summary_resp.get("text", "").strip() or "Task complete."
        except Exception as _e:
            logger.debug("Summary call failed: %s", _e)
            final_text = "Task complete."
    elif cost_abort and not final_text.strip():
        final_text = "Stopped: estimated LLM cost exceeded the soft cap."
    elif wall_abort and not final_text.strip():
        final_text = "Stopped: wall-clock budget exceeded."

    elapsed_ms = _mono_elapsed_ms()
    budget_ms = int(wall_budget_s * 1000)

    execution_summary: dict[str, Any]
    if last_matlab_spec is not None:
        execution_summary = last_matlab_spec
    else:
        execution_summary = {
            "run_success": not last_tool_failed,
            "spec_satisfied": not last_tool_failed,
            "user_asked_base_only": False,
            "violations": [] if not last_tool_failed else ["A tool failed; no successful MATLAB run to evaluate spec."],
            "notes": ["No run_matlab step completed in this trace."],
        }

    if memory_manager and prod and getattr(prod, "store_agentic_episodes", True):
        try:
            _key = f"agentic_{session_id}_{int(_time.time())}"
            memory_manager.store_artifact(
                _key,
                {
                    "summary": (user_text or "")[:2000],
                    "reply_excerpt": (final_text or "")[:2000],
                    "kind": "agentic_episode",
                    "session_id": session_id,
                    "spec_satisfied": str(execution_summary.get("spec_satisfied", "")),
                    "violations": ",".join(execution_summary.get("violations") or [])[:1500],
                },
            )
        except Exception as _mem_exc:
            logger.debug("Agentic episode store skipped: %s", _mem_exc)

    _tok_total = usage_totals["prompt_tokens"] + usage_totals["completion_tokens"]
    _est_usd = _estimated_cost_usd()

    yield _sse("done", {
        "skill": "agentic",
        "elapsed_ms": elapsed_ms,
        "reply": final_text,
        "plots": all_plots,
        "files": [],
        "execution": execution_summary,
        "budget": {
            "wall_clock_ms": elapsed_ms,
            "wall_clock_budget_ms": budget_ms,
            "within_wall_budget": (not wall_abort) and elapsed_ms <= budget_ms,
            "wall_abort": wall_abort,
            "soft_cost_cap_usd": cost_cap,
            "cost_abort": cost_abort,
            "prompt_tokens": usage_totals["prompt_tokens"],
            "completion_tokens": usage_totals["completion_tokens"],
            "total_tokens": _tok_total,
            "estimated_cost_usd": round(_est_usd, 6),
            "within_soft_cost_cap": (not cost_cap_enforced) or (not cost_abort),
        },
    })


__all__ = ["run_agentic_loop", "AGENTIC_SYSTEM"]
