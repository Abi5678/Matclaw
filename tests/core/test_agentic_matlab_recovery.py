"""Agentic loop must auto-run fenced MATLAB when the LLM skips tool_calls."""

import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.matclaw.core.agentic_loop import run_agentic_loop
from src.matclaw.tools.registry import ToolRegistry


def _parse_events(chunks: list[str]) -> list[tuple[str, dict]]:
    out = []
    event = None
    for c in chunks:
        for line in c.splitlines():
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: ") and event:
                out.append((event, json.loads(line[6:])))
                event = None
    return out


@pytest.mark.asyncio
async def test_fenced_matlab_without_tool_calls_is_executed():
    prose = (
        "Sure — here is a surface plot.\n\n"
        "```matlab\n"
        "[x,y]=meshgrid(linspace(-pi,pi,40));\n"
        "z=sin(x).*cos(y);\n"
        "surf(x,y,z);\n"
        "```\n"
    )
    calls = {"n": 0}

    async def fake_llm(**kwargs):
        calls["n"] += 1
        # First response: prose + fence, no tool_calls. Later: summary.
        if calls["n"] == 1:
            return {"text": prose, "tool_calls": [], "usage": None}
        return {"text": "Rendered the surface.", "tool_calls": [], "usage": None}

    async def dispatch(name, inputs):
        assert name == "run_matlab"
        assert "surf" in inputs.get("code", "")
        return "ok", ["/plots/recovery.png"]

    settings = SimpleNamespace(
        llm=SimpleNamespace(provider="openai-compatible", model="x", api_key="k", base_url="http://x"),
        agentic=SimpleNamespace(max_iterations=5, max_tokens_per_step=256),
        production=SimpleNamespace(
            max_agentic_wall_seconds=30.0,
            soft_cost_cap_usd_per_task=0.0,
            usd_per_1k_prompt_tokens=0.0,
            usd_per_1k_completion_tokens=0.0,
            store_agentic_episodes=False,
        ),
    )

    chunks: list[str] = []
    with patch("src.matclaw.core.agentic_loop.call_chat_with_tools", side_effect=fake_llm):
        async for ev in run_agentic_loop(
            user_text="Plot a 3D surface of sin(x)*cos(y)",
            history=[],
            settings=settings,
            tool_registry=ToolRegistry(),
            runtime_dispatcher=dispatch,
            memory_preamble=None,
            session_id="t",
            memory_manager=None,
        ):
            chunks.append(ev)

    events = _parse_events(chunks)
    kinds = [e for e, _ in events]
    assert "tool_start" in kinds
    assert "tool_result" in kinds
    done = [p for e, p in events if e == "done"][-1]
    assert "/plots/recovery.png" in (done.get("plots") or [])
