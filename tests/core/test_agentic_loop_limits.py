"""Wall-clock and soft cost-cap behavior in the agentic loop (mocked LLM)."""

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.matclaw.core.agentic_loop import run_agentic_loop
from src.matclaw.tools.registry import ToolRegistry


def _parse_done_sse(blob: str) -> dict:
    """Extract JSON payload from a full SSE `data: {...}` line inside blob."""
    m = re.search(r"^data:\s*(\{.*\})\s*$", blob, re.MULTILINE)
    assert m, blob
    return json.loads(m.group(1))


@pytest.mark.asyncio
async def test_wall_clock_stops_before_more_tools():
    calls = {"n": 0}

    async def fake_llm(**_kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.055)
        if calls["n"] < 4:
            return {
                "text": "",
                "tool_calls": [{
                    "id": str(calls["n"]),
                    "name": "run_shell",
                    "inputs": {"command": "true"},
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 0},
            }
        return {"text": "done", "tool_calls": [], "usage": None}

    async def dispatch(_name, _inputs):
        return "ok", []

    settings = SimpleNamespace(
        llm=SimpleNamespace(provider="nvidia", model="x"),
        agentic=SimpleNamespace(max_iterations=20, max_tokens_per_step=128),
        production=SimpleNamespace(
            max_agentic_wall_seconds=0.15,
            soft_cost_cap_usd_per_task=0.0,
            usd_per_1k_prompt_tokens=0.0,
            usd_per_1k_completion_tokens=0.0,
            store_agentic_episodes=False,
        ),
    )

    chunks: list[str] = []
    with patch("src.matclaw.core.agentic_loop.call_chat_with_tools", side_effect=fake_llm):
        async for ev in run_agentic_loop(
            user_text="hi",
            history=[],
            settings=settings,
            tool_registry=ToolRegistry(),
            runtime_dispatcher=dispatch,
            memory_preamble=None,
            session_id="t",
            memory_manager=None,
        ):
            chunks.append(ev)

    done_lines = [c for c in chunks if "event: done" in c]
    assert done_lines
    payload = _parse_done_sse(done_lines[-1])
    assert payload["budget"]["wall_abort"] is True
    assert payload["budget"]["within_wall_budget"] is False
    assert calls["n"] < 4


@pytest.mark.asyncio
async def test_soft_cost_cap_stops_loop():
    calls = {"n": 0}

    async def fake_llm(**_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "text": "",
                "tool_calls": [{
                    "id": "1",
                    "name": "run_shell",
                    "inputs": {"command": "x"},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 0},
            }
        return {
            "text": "x",
            "tool_calls": [],
            "usage": {"prompt_tokens": 600_000, "completion_tokens": 0},
        }

    async def dispatch(_name, _inputs):
        return "ok", []

    settings = SimpleNamespace(
        llm=SimpleNamespace(provider="nvidia", model="x"),
        agentic=SimpleNamespace(max_iterations=20, max_tokens_per_step=128),
        production=SimpleNamespace(
            max_agentic_wall_seconds=600.0,
            soft_cost_cap_usd_per_task=0.50,
            usd_per_1k_prompt_tokens=0.001,
            usd_per_1k_completion_tokens=0.0,
            store_agentic_episodes=False,
        ),
    )

    chunks: list[str] = []
    with patch("src.matclaw.core.agentic_loop.call_chat_with_tools", side_effect=fake_llm):
        async for ev in run_agentic_loop(
            user_text="hi",
            history=[],
            settings=settings,
            tool_registry=ToolRegistry(),
            runtime_dispatcher=dispatch,
            memory_preamble=None,
            session_id="t",
            memory_manager=None,
        ):
            chunks.append(ev)

    payload = _parse_done_sse([c for c in chunks if "event: done" in c][-1])
    assert payload["budget"]["cost_abort"] is True
    assert payload["budget"]["within_soft_cost_cap"] is False
    assert payload["budget"]["estimated_cost_usd"] > 0.5
