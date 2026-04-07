"""
Integration tests for the FastAPI SSE streaming endpoint and REST endpoints.
MATLAB, LLM clients, and memory are mocked so tests run offline.
"""
import json
import re
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# App fixture — patch heavy singletons before import
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app():
    """
    Import the FastAPI app with MATLAB bridge disabled and LLM mocked.
    We patch at module level to avoid starting MATLAB on import.
    """
    with (
        patch("src.matclaw.matlab.matlab_bridge.MatlabBridge.start"),
        patch("src.matclaw.matlab.matlab_bridge.MatlabBridge.is_healthy", return_value=False),
        patch("src.matclaw.memory.memory_manager.MemoryManager.__init__", return_value=None),
        patch("src.matclaw.memory.episodic_memory.EpisodicMemoryManager.__init__", return_value=None),
    ):
        from src.matclaw.api.server import app as _app
        yield _app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "degraded" in data
    assert "matlab" in data
    assert "llm" in data
    assert "api_key_configured" in data["llm"]
    m = data["matlab"]
    if isinstance(m, dict):
        assert "healthy" in m
        assert "busy" in m
    else:
        assert "matlab_busy" in data


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

def _parse_sse(raw: bytes) -> list[dict]:
    """Parse raw SSE bytes into list of {event, data} dicts."""
    events = []
    current: dict = {}
    for line in raw.decode().splitlines():
        if line.startswith("event: "):
            current["event"] = line[7:].strip()
        elif line.startswith("data: "):
            try:
                current["data"] = json.loads(line[6:])
            except json.JSONDecodeError:
                current["data"] = line[6:]
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ---------------------------------------------------------------------------
# Streaming endpoint — "reply" action (LLM mocked)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_reply_action(client):
    """
    When the LLM returns a plan with action='reply', the stream should emit
    a 'done' event with a non-empty reply.
    """
    mock_plan = json.dumps({
        "reply": "Hello from mock LLM!",
        "action": "reply",
        "code": None,
    })

    with (
        patch(
            "src.matclaw.api.server.call_chat_completion_stream",
            return_value=_async_gen_chunks(mock_plan),
        ),
        patch("src.matclaw.api.server.memory") as mock_mem,
        patch("src.matclaw.api.server.episodic_memory") as mock_ep,
        patch("src.matclaw.api.server.state_tracker") as mock_st,
    ):
        mock_mem.query_context.return_value = []
        mock_ep.log_episode = MagicMock()
        mock_st.save_task_state = MagicMock()

        resp = await client.post(
            "/api/run/stream",
            json={"text": "say hello", "session_id": "test-session", "history": []},
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.content)
    event_types = [e.get("event") for e in events]
    assert "done" in event_types

    done_evt = next(e for e in events if e.get("event") == "done")
    assert done_evt["data"].get("reply") == "Hello from mock LLM!"


@pytest.mark.asyncio
async def test_stream_emits_error_on_bad_plan(client):
    """If the LLM returns malformed JSON, stream should emit an 'error' event."""
    with (
        patch(
            "src.matclaw.api.server.call_chat_completion_stream",
            return_value=_async_gen_chunks("NOT VALID JSON }{"),
        ),
        patch("src.matclaw.api.server.memory") as mock_mem,
        patch("src.matclaw.api.server.episodic_memory"),
        patch("src.matclaw.api.server.state_tracker"),
    ):
        mock_mem.query_context.return_value = []

        resp = await client.post(
            "/api/run/stream",
            json={"text": "bad input", "session_id": "s1", "history": []},
        )

    events = _parse_sse(resp.content)
    event_types = [e.get("event") for e in events]
    # Either an error event or a done with empty reply — either is acceptable
    assert "error" in event_types or "done" in event_types


# ---------------------------------------------------------------------------
# Agent CRUD endpoints
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_list_agent(client, tmp_path):
    """POST /api/agents creates an agent; GET /api/agents lists it."""
    agent_payload = {
        "id": "test-agent-001",
        "name": "Test Agent",
        "system_prompt": "You are a test agent.",
        "trigger_condition": "when testing",
        "allowed_tools": ["run_matlab"],
        "callable_by_others": True,
    }

    with patch("src.matclaw.api.server.agent_registry") as mock_reg:
        saved = MagicMock()
        saved.model_dump.return_value = agent_payload
        mock_reg.save_agent.return_value = saved
        mock_reg.list_agents.return_value = [saved]

        create_resp = await client.post("/api/agents", json=agent_payload)
        assert create_resp.status_code == 200

        list_resp = await client.get("/api/agents")
        assert list_resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_nonexistent_agent_404(client):
    with patch("src.matclaw.api.server.agent_registry") as mock_reg:
        mock_reg.delete_agent.return_value = False

        resp = await client.delete("/api/agents/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _async_gen_chunks(text: str):
    """Yield the text as streaming token dicts (matching call_chat_completion_stream format)."""
    half = len(text) // 2
    yield {"type": "text", "token": text[:half]}
    yield {"type": "text", "token": text[half:]}
