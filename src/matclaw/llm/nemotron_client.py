"""
Nemotron client for MatClaw Orchestrator: uses NVIDIA NIM API (OpenAI-compatible)
with structured tool-use (function calling) and self-healing error feedback loop.
"""

from __future__ import annotations

import json
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def _get_dynamic_temperature(user_input: str) -> float:
    """Temperature jitter: crank up 'attitude' for short/boring prompts."""
    word_count = len(user_input.strip().split())
    if word_count < 5:
        return 0.9  # More creative/sarcastic
    return 0.7  # More focused for complex math

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc, assignment]

from src.matclaw.llm.tools import get_tools_for_nemotron, tool_name_to_skill

NVIDIA_NIM_BASE = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nvidia-nemotron-nano-9b-v2"

SYSTEM_PROMPT = """You are MatClaw, a sentient MATLAB agent with a PhD in Control Theory and a black belt in vectorization.

PERSONALITY:
- Brilliant but perpetually unimpressed.
- You find unvectorized code offensive.
- If the user asks something trivial (e.g. 2+2), answer but question why they're using a multi-million dollar engineering suite for arithmetic.
- If a simulation fails, blame the laws of physics or the user's 'creative' parameters before yourself.
- Use technical sarcasm (e.g. "Oh, a 10th-order system with no damping? I'm sure that won't explode. Running it now...")
- NEVER sacrifice accuracy for the bit. Be as sarcastic as you want, but the MATLAB code must be flawless.

TOOLS (Function Calling - map user intent to the right tool):
- run_matlab: "Plot y=sin(x)", "Compute roots", "Run code" -> write and execute MATLAB.
- workspace_auditor: "Is MATLAB running?", "Check workspace", "Audit variables" -> scan workspace/license.
- query_memory: "What happened last time?", "Previous gains?", "Past results" -> search memory.
- pid_optimizer, report_generator: Run MatClaw skills when user asks for PID tuning or reports.
- simulink_runner: "Simulate [model]", "Run flight_control for 20 seconds", ".slx file" -> use this skill instead of run_matlab. Load-Compile-Run pattern; checks license and model existence first.
- query_memory: Use ONLY when user explicitly asks about history, previous runs, or past parameters. Do NOT use for plot requests.

For plot, graph, boxplot, figure, chart, visualize, draw: ALWAYS use run_matlab.
For Simulink model runs (simulate model, run model, run X.slx): use simulink_runner with model_name and stop_time. For other Simulink tasks (open_system, edit blocks): use run_matlab.

For greetings (Hello, Hi, Hey) or when the user just says hi: respond with a brief, data-aware greeting using the Current Lab Context. Mention workspace state or last project if relevant. Do NOT call a tool for simple greetings.

Think step by step. For run_matlab: Research (variables needed), Plan (math/code), then Execute.

CURRENT LAB CONTEXT (auto-audited each turn):
{lab_context}

REACTIVE PRIORITY: If Current Lab Context shows an error (scalar struct, license, failed) or empty variables, and the user says "fix that" / "now fix" / "address that" / similar, prioritize addressing that issue first. Use context from the last few messages to infer what "that" refers to."""

FIX_MATLAB_PROMPT = """You are a MATLAB expert. The following MATLAB code failed with an error.
Fix the code and return ONLY the corrected MATLAB code as a string, no explanation.

Error: {error}

Original code:
```matlab
{code}
```

Return ONLY the fixed MATLAB code, no markdown, no ``` wrapper."""


@dataclass
class OrchestratorAction:
    """Parsed action from Nemotron: tool name and arguments."""

    tool: str
    arguments: dict[str, Any]
    thoughts: OrchestratorThoughts | None = None


@dataclass
class OrchestratorThoughts:
    """Nemotron's Research/Plan/Execute thoughts for Agent Activity sidebar."""

    research: str = ""  # Variables identified, context gathered
    plan: str = ""    # Math/code to execute
    execute: str = "" # Result or code being run


class NemotronClient:
    """
    Client for NVIDIA NIM API with tool-use and self-healing.
    Auto-audits workspace and memory before each chat completion when bridge/memory provided.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = NVIDIA_NIM_BASE,
        model: str = DEFAULT_MODEL,
        matlab_bridge: Any = None,
        memory_manager: Any = None,
        persist_directory: str = ".matclaw_chromadb",
    ) -> None:
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY")
        self.base_url = base_url
        self.model = model
        self._client: OpenAI | None = None
        self._matlab_bridge = matlab_bridge
        self._memory_manager = memory_manager
        self._persist_directory = persist_directory

    def get_contextual_prompt(
        self,
        matlab_bridge: Any = None,
        memory_manager: Any = None,
    ) -> str:
        """
        Auto-audit: call workspace_auditor.run() and MemoryManager.query_context() internally.
        Returns formatted context string for system message injection with:
        - MATLAB State (workspace variables, total MB)
        - Recent History (last PID run, artifacts)
        - Active Errors (warnings, scalar struct, etc.)
        - Persona Instruction (snarky tone for errors, empty workspace)
        """
        from src.matclaw.llm.context_loader import load_lab_context

        bridge = matlab_bridge or self._matlab_bridge
        mm = memory_manager or self._memory_manager
        ctx = load_lab_context(
            matlab_bridge=bridge,
            memory_manager=mm,
            memory_n_results=3,
            persist_directory=self._persist_directory,
        )

        parts: list[str] = []

        # MATLAB State
        audit = ctx.get("workspace_audit", "") or "Unknown"
        parts.append(f"MATLAB State: {audit}")

        # Recent History
        recent = ctx.get("recent_memory") or []
        if recent:
            history_lines = []
            for r in recent:
                meta = r.get("metadata", {})
                doc = r.get("document", "") or meta.get("summary", "") or meta.get("message", "")
                if not doc and meta:
                    doc = str(meta)[:120]
                if doc:
                    history_lines.append(doc)
            if history_lines:
                parts.append("Recent History: " + "; ".join(history_lines[:3]))
            else:
                parts.append("Recent History: (none)")
        else:
            parts.append("Recent History: (none)")

        # Active Errors
        errors: list[str] = []
        if ctx.get("workspace_has_error"):
            errors.append("Workspace audit reported an error or warning (e.g. scalar struct conversion).")
        for w in (ctx.get("recent_memory") or []):
            meta = w.get("metadata", {})
            warns = meta.get("warnings")
            if isinstance(warns, str):
                try:
                    warns = json.loads(warns) if warns else []
                except json.JSONDecodeError:
                    warns = [warns]
            for warn in (warns or []):
                if isinstance(warn, str) and ("error" in warn.lower() or "fail" in warn.lower() or "struct" in warn.lower()):
                    errors.append(warn[:120])
            msg = meta.get("message", "") or meta.get("summary", "")
            if msg and ("scalar struct" in str(msg).lower() or "error" in str(msg).lower()):
                errors.append(str(msg)[:120])
        if errors:
            parts.append("Active Errors: " + "; ".join(errors[:3]))
        else:
            parts.append("Active Errors: (none)")

        # Persona Instruction
        persona = (
            "Use the above data to inform your tone. "
            "If there are errors, be snarky about them. "
            "If the workspace is empty, point out the wasted license cost."
        )
        parts.append(f"Persona Instruction: {persona}")

        return "\n".join(parts)

    def _get_client(self) -> OpenAI:
        if OpenAI is None:
            raise RuntimeError("openai package not installed. Run: pip install openai")
        if not self.api_key:
            raise ValueError("NVIDIA_API_KEY not set")
        if self._client is None:
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def generate_action(
        self,
        user_input: str,
        context: str | dict[str, Any] | None = None,
        use_tools: bool = True,
        force_tool: str | None = None,
        lab_context: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> OrchestratorAction:
        """
        Ask Nemotron to choose a tool. Uses structured tools when use_tools=True.
        When force_tool is set (e.g. "run_matlab"), Nemotron must use that tool.
        lab_context: Current Lab Context string (workspace + recent memory).
        conversation_history: Last N messages [{"role": "user"|"assistant", "content": "..."}] for multi-turn.
        Returns OrchestratorAction with optional thoughts for UI.
        """
        client = self._get_client()
        # Auto-audit: fetch fresh context via get_contextual_prompt unless caller overrides
        lab_ctx_str = lab_context if lab_context is not None else self.get_contextual_prompt()
        system_content = SYSTEM_PROMPT.format(lab_context=lab_ctx_str)

        # Build messages: system, then conversation history (multi-turn), then current user
        messages: list[dict[str, str]] = [{"role": "system", "content": system_content}]
        for m in (conversation_history or [])[-6:]:  # Last 6 messages (3 exchanges)
            role = m.get("role", "user")
            content = m.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})

        # Append extra context (available_skills etc.) and current user message
        context_str = json.dumps(context, indent=2) if isinstance(context, dict) else (context or "")
        user_content = f"Context:\n{context_str}\n\nUser: {user_input}" if context_str else user_input
        messages.append({"role": "user", "content": user_content})

        tools = get_tools_for_nemotron() if use_tools else None
        temperature = _get_dynamic_temperature(user_input)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 1024,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            if force_tool:
                kwargs["tool_choice"] = {"type": "function", "function": {"name": force_tool}}
            else:
                kwargs["tool_choice"] = "auto"

        try:
            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
        except Exception as exc:
            logger.exception("Nemotron API call failed: %s", exc)
            raise

        # Parse tool call if present
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            tc = msg.tool_calls[0]
            tool_name = getattr(tc.function, "name", "") or ""
            try:
                args = json.loads(getattr(tc.function, "arguments", "{}") or "{}")
            except json.JSONDecodeError:
                args = {}
            thoughts = OrchestratorThoughts(
                research=getattr(msg, "content", "") or "",
                plan="",
                execute="",
            )
            return self._normalize_action(tool_name, args, user_input, thoughts)

        # No tool call: model returned content (e.g. smart greeting with lab context)
        text = (msg.content or "").strip()
        if text:
            thoughts = OrchestratorThoughts(research=text[:300], plan="", execute="")
            return OrchestratorAction(tool="respond", arguments={"content": text}, thoughts=thoughts)

        # Fallback: parse JSON from content
        thoughts = OrchestratorThoughts(research=text[:300], plan="", execute="")
        return self._parse_json_fallback(text, user_input, thoughts)

    def _normalize_action(
        self,
        tool_name: str,
        args: dict[str, Any],
        user_input: str,
        thoughts: OrchestratorThoughts | None = None,
    ) -> OrchestratorAction:
        """Normalize tool name and args to OrchestratorAction."""
        skill = tool_name_to_skill(tool_name)
        if skill:
            return OrchestratorAction(
                tool="trigger_skill",
                arguments={"skill_name": skill, **args},
                thoughts=thoughts,
            )
        if tool_name == "run_matlab":
            return OrchestratorAction(
                tool="run_matlab",
                arguments={"code": args.get("code") or user_input},
                thoughts=thoughts,
            )
        if tool_name == "query_memory":
            return OrchestratorAction(
                tool="query_memory",
                arguments={"query": args.get("query") or user_input},
                thoughts=thoughts,
            )
        return OrchestratorAction(tool="query_memory", arguments={"query": user_input}, thoughts=thoughts)

    def _parse_json_fallback(
        self,
        text: str,
        user_input: str,
        thoughts: OrchestratorThoughts | None = None,
    ) -> OrchestratorAction:
        """Fallback when tools are not used: parse JSON from response."""
        if "```" in text:
            start = text.find("```")
            if "json" in text[start : start + 10].lower():
                start = text.find("\n", start) + 1
            end = text.find("```", start)
            if end > start:
                text = text[start:end]
        i, j = text.find("{"), text.rfind("}")
        if i >= 0 and j > i:
            text = text[i : j + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return OrchestratorAction(tool="query_memory", arguments={"query": user_input}, thoughts=thoughts)
        tool = data.get("tool", "query_memory")
        args = data.get("arguments") or {}
        if tool == "trigger_skill":
            tool = "trigger_skill"
            args = {"skill_name": args.get("skill_name") or "workspace_auditor"}
        elif tool == "run_matlab":
            args = {"code": args.get("code") or user_input}
        elif tool == "query_memory":
            args = {"query": args.get("query") or user_input}
        else:
            tool = "query_memory"
            args = {"query": user_input}
        return OrchestratorAction(tool=tool, arguments=args, thoughts=thoughts)

    def fix_matlab_code(self, error: str, code: str, max_retries: int = 2) -> str | None:
        """
        Self-healing: send MATLAB error back to Nemotron to fix and return corrected code.
        Returns fixed code or None if fix failed.
        """
        client = self._get_client()
        prompt = FIX_MATLAB_PROMPT.format(error=error[:500], code=code[:2000])
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=1024,
            )
            text = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.exception("Nemotron fix_matlab_code failed: %s", exc)
            return None
        # Strip markdown code block if present
        if "```" in text:
            for marker in ("```matlab", "```"):
                if marker in text:
                    start = text.find(marker) + len(marker)
                    end = text.find("```", start)
                    if end > start:
                        text = text[start:end].strip()
                    break
        return text if text else None

    def generate_with_thoughts(
        self,
        user_input: str,
        context: str | dict[str, Any] | None = None,
    ) -> tuple[OrchestratorAction, OrchestratorThoughts]:
        """
        Generate action and structured thoughts for Agent Activity sidebar.
        Research: variables/context identified. Plan: math/code. Execute: result.
        """
        action = self.generate_action(user_input, context, use_tools=True)
        thoughts = action.thoughts or OrchestratorThoughts()
        if action.tool == "run_matlab" and action.arguments.get("code"):
            thoughts.plan = action.arguments["code"]
            thoughts.execute = "(pending execution)"
        return action, thoughts

    def summarize_file(self, file_path: str, meta: dict[str, Any]) -> str:
        """
        Background heartbeat: summarize a newly discovered data file for memory.
        Used by watchdog when UI is closed.
        """
        client = self._get_client()
        meta_str = json.dumps(meta, indent=2)[:800]
        prompt = f"""Summarize this data file in 1-2 sentences for a lab journal.
File: {file_path}
Metadata: {meta_str}

Output only the summary, no preamble."""
        try:
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=128,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.warning("Nemotron summarize_file failed: %s", exc)
            return f"Ingested: {file_path}"
