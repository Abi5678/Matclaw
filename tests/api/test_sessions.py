"""
Tests for the SQLite-backed SessionStore and the /api/sessions REST endpoints.
"""
import pytest
import pytest_asyncio
from unittest.mock import patch
from httpx import AsyncClient, ASGITransport

from src.matclaw.memory.session_store import SessionStore


# ---------------------------------------------------------------------------
# SessionStore unit tests
# ---------------------------------------------------------------------------

def test_empty_store(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    assert store.list_sessions() == []


def test_upsert_and_list(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    store.upsert_session({
        "id": "s1",
        "title": "Test Session",
        "createdAt": 1000,
        "updatedAt": 2000,
        "messages": [{"role": "user", "text": "hello", "id": "m1", "ts": 1000}],
    })
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["title"] == "Test Session"
    # list_sessions omits messages for perf
    assert sessions[0]["messages"] == []


def test_get_session_includes_messages(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    messages = [{"role": "user", "text": "hi", "id": "m1", "ts": 1000}]
    store.upsert_session({
        "id": "s1",
        "title": "With Messages",
        "createdAt": 1000,
        "updatedAt": 1000,
        "messages": messages,
    })
    s = store.get_session("s1")
    assert s is not None
    assert len(s["messages"]) == 1
    assert s["messages"][0]["text"] == "hi"


def test_upsert_updates_existing(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    store.upsert_session({"id": "s1", "title": "Old", "createdAt": 1, "updatedAt": 1, "messages": []})
    store.upsert_session({"id": "s1", "title": "New", "createdAt": 1, "updatedAt": 2, "messages": []})
    s = store.get_session("s1")
    assert s["title"] == "New"
    assert s["updatedAt"] == 2


def test_delete_session(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    store.upsert_session({"id": "s1", "title": "To Delete", "createdAt": 1, "updatedAt": 1, "messages": []})
    assert store.delete_session("s1") is True
    assert store.get_session("s1") is None


def test_delete_nonexistent_returns_false(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    assert store.delete_session("does-not-exist") is False


def test_list_ordered_by_updated_at_desc(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.sqlite3"))
    store.upsert_session({"id": "old", "title": "Old", "createdAt": 1, "updatedAt": 1000, "messages": []})
    store.upsert_session({"id": "new", "title": "New", "createdAt": 2, "updatedAt": 9000, "messages": []})
    sessions = store.list_sessions()
    assert sessions[0]["id"] == "new"
    assert sessions[1]["id"] == "old"


# ---------------------------------------------------------------------------
# REST endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def app(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "sessions.sqlite3"
    with (
        patch("src.matclaw.matlab.matlab_bridge.MatlabBridge.start"),
        patch("src.matclaw.matlab.matlab_bridge.MatlabBridge.is_healthy", return_value=False),
        patch("src.matclaw.memory.memory_manager.MemoryManager.__init__", return_value=None),
        patch("src.matclaw.memory.episodic_memory.EpisodicMemoryManager.__init__", return_value=None),
        patch("src.matclaw.memory.session_store.SessionStore.__init__", return_value=None),
    ):
        from src.matclaw.api.server import app as _app, session_store as _store
        # Inject a real store backed by tmp db
        import threading
        real_store = SessionStore.__new__(SessionStore)
        real_store.db_path = str(db)
        real_store._lock = threading.Lock()
        import sqlite3, src.matclaw.memory.session_store as _mod
        conn = sqlite3.connect(str(db))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_mod._CREATE_TABLE)
        conn.commit()
        conn.close()
        import src.matclaw.api.server as srv
        srv.session_store = real_store
        yield _app


@pytest_asyncio.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_list_empty(client):
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_upsert_and_list_via_api(client):
    session = {
        "id": "api-s1",
        "title": "API Test",
        "createdAt": 1000,
        "updatedAt": 2000,
        "messages": [],
    }
    resp = await client.post("/api/sessions", json=session)
    assert resp.status_code == 200

    list_resp = await client.get("/api/sessions")
    ids = [s["id"] for s in list_resp.json()]
    assert "api-s1" in ids


@pytest.mark.asyncio
async def test_get_session_404(client):
    resp = await client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_session_via_api(client):
    session = {
        "id": "api-delete",
        "title": "Delete Me",
        "createdAt": 1,
        "updatedAt": 1,
        "messages": [],
    }
    await client.post("/api/sessions", json=session)
    del_resp = await client.delete("/api/sessions/api-delete")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"
