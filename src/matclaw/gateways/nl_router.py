"""
Natural Language Router: uses an LLM to classify user intent and extract parameters.

Intents:
- run_skill: e.g. "Check my workspace" -> workspace_auditor
- execute_code: e.g. "Plot a sine wave" -> MATLAB code
- ask_question: e.g. "What was the last stable gain?" -> query MemoryManager

Supports conversation context for follow-ups like "Now make the line red".
Supports Anthropic (Claude), Google (Gemini), and NVIDIA (Nemotron) via provider parameter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from src.matclaw.llm.llm_client import call_chat_completion, resolve_api_key

logger = logging.getLogger(__name__)


@dataclass
class NLIntent:
    """Structured result from the NL router."""

    intent: str  # "run_skill" | "execute_code" | "ask_question" | "analyze_file"
    skill_name: str | None = None
    matlab_code: str | None = None
    query: str | None = None
    file_path: str | None = None    # extracted .m file path (for analyze_file intent)
    file_action: str | None = None  # "analyze" | "fix" | "run" | "fix_and_run"
    raw_response: str = ""


ROUTER_SYSTEM = """You are a MatClaw assistant router. Classify the user's intent and extract parameters.

Available skills: {skills}

Intents:
1. run_skill - User wants to run a MatClaw skill. Map to: workspace_auditor, pid_optimizer, or report_generator.
   Examples: "Check my workspace", "Audit memory", "Run PID optimization", "Generate a report"
2. execute_code - User wants to run MATLAB code (plot, compute, visualize).
   Examples: "Plot a sine wave", "Compute roots of x^2-5x+6", "Draw a red line"
3. ask_question - User asks about past results, parameters, or history. Query memory.
   Examples: "What was the last stable gain?", "What did we tune last?", "Show me recent results"
4. analyze_file - User wants to read, analyze, fix, or run a MATLAB .m file.
   Extract file_path from the message. Set file_action to one of: "analyze", "fix", "run", "fix_and_run".
   Examples: "fix test.m and run it", "analyze controller.m", "run pid_controller.m", "check my_script.m for errors"

Respond with ONLY a JSON object, no other text:
{{"intent": "run_skill"|"execute_code"|"ask_question"|"analyze_file", "skill_name": "..." or null, "matlab_code": "..." or null, "query": "..." or null, "file_path": "..." or null, "file_action": "..." or null}}

For execute_code, provide valid MATLAB code as a string. Use disp() for scalar output. For plots, use figure; plot(...); and optionally exportgraphics if needed.
For follow-ups like "Now make the line red", infer from context (e.g. add 'r' to plot, or set Color).
For analyze_file, extract the .m filename from the user's message and infer the action (fix, run, analyze, or fix_and_run)."""


def route_nl_message(
    message: str,
    conversation_buffer: list[dict[str, str]],
    available_skills: list[str],
    api_key: str | None = None,
    model: str = "gemini-2.0-flash",
    provider: str = "google",
    lab_context: str | None = None,
) -> NLIntent:
    """
    Use LLM to classify intent and extract parameters from the user message.

    Args:
        message: Current user message.
        conversation_buffer: Last N messages [{"role": "user"|"assistant", "content": "..."}].
        available_skills: List of skill names.
        api_key: API key (or set ANTHROPIC_API_KEY / GOOGLE_API_KEY).
        model: Model name.
        provider: "nvidia" (Nemotron), "google" (Gemini), or "anthropic" (Claude).

    Returns:
        NLIntent with intent and extracted parameters.
    """
    key = resolve_api_key(provider, api_key)
    if not key:
        logger.warning("API key not set (NVIDIA_API_KEY / GOOGLE_API_KEY / ANTHROPIC_API_KEY); NL routing disabled.")
        return NLIntent(intent="ask_question", query=message, raw_response="")

    skills_str = ", ".join(available_skills)
    system = ROUTER_SYSTEM.format(skills=skills_str)
    if lab_context:
        system += f"\n\nCurrent Lab Context:\n{lab_context}\n\nIf the context shows an error or empty workspace, the user may want you to address that. Use conversation history to infer follow-ups like 'fix that' or 'now fix'."

    messages: list[dict[str, Any]] = []
    for m in conversation_buffer[-4:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": message})

    try:
        text = call_chat_completion(
            provider=provider,
            model=model,
            system=system,
            messages=messages,
            api_key=key,
            max_tokens=512,
        )

        # Parse JSON from response
        if "```" in text:
            start = text.find("```")
            if "json" in text[start : start + 10].lower():
                start = text.find("\n", start) + 1
            end = text.find("```", start)
            if end > start:
                text = text[start:end]
        i = text.find("{")
        j = text.rfind("}")
        if i >= 0 and j > i:
            text = text[i : j + 1]

        data = json.loads(text)
        intent = data.get("intent", "ask_question")
        if intent not in ("run_skill", "execute_code", "ask_question", "analyze_file"):
            intent = "ask_question"

        return NLIntent(
            intent=intent,
            skill_name=data.get("skill_name") or None,
            matlab_code=data.get("matlab_code") or None,
            query=data.get("query") or message,
            file_path=data.get("file_path") or None,
            file_action=data.get("file_action") or None,
            raw_response=text,
        )
    except json.JSONDecodeError as e:
        logger.warning("NL router JSON parse failed: %s", e)
        return NLIntent(intent="ask_question", query=message, raw_response="")
    except Exception as exc:
        logger.exception("NL router failed: %s", exc)
        return NLIntent(intent="ask_question", query=message, raw_response="")


def resolve_skill_from_nl(skill_name: str | None, available_skills: list[str]) -> str | None:
    """Map LLM skill_name to actual skill (fuzzy match)."""
    if not skill_name:
        return None
    lower = skill_name.lower().strip()
    for s in available_skills:
        if s.lower() in lower or lower in s.lower():
            return s
    # Common aliases
    aliases = {
        "workspace_auditor": ["workspace", "audit", "memory", "variables"],
        "pid_optimizer": ["pid", "tune", "optimize", "gains"],
        "report_generator": ["report", "summary"],
    }
    for skill, words in aliases.items():
        if skill in available_skills and any(w in lower for w in words):
            return skill
    return None
