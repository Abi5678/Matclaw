"""Tests for SessionStore persistence layer."""
import time

import pytest

from matclaw.memory.session_store import SessionStore


@pytest.fixture
def store(tmp_db):
    return SessionStore(db_path=tmp_db)


def _session(id="s1", title="Test Session", messages=None, ts=None):
    now = ts or int(time.time() * 1000)
    return {"id": id, "title": title, "createdAt": now, "updatedAt": now, "messages": messages or []}


def test_list_sessions_empty(store):
    assert store.list_sessions() == []


def test_upsert_and_list(store):
    store.upsert_session(_session("s1", "My Session"))
    sessions = store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == "s1"
    assert sessions[0]["title"] == "My Session"


def test_get_session_returns_none_for_missing(store):
    assert store.get_session("does-not-exist") is None


def test_get_session_with_messages(store):
    msgs = [{"role": "user", "text": "hello"}]
    store.upsert_session(_session("s2", "Chat", messages=msgs))
    session = store.get_session("s2")
    assert session is not None
    assert len(session["messages"]) == 1
    assert session["messages"][0]["text"] == "hello"


def test_multiple_sessions_ordered_by_updated_at(store):
    store.upsert_session(_session("old", "First", ts=1000))
    store.upsert_session(_session("new", "Second", ts=2000))
    sessions = store.list_sessions()
    # Most recently updated first
    assert sessions[0]["id"] == "new"
    assert sessions[1]["id"] == "old"


def test_delete_session(store):
    store.upsert_session(_session("s3", "Delete me"))
    store.delete_session("s3")
    assert store.get_session("s3") is None
    assert store.list_sessions() == []


def test_delete_returns_false_for_missing(store):
    assert store.delete_session("no-such-id") is False


def test_upsert_updates_title(store):
    store.upsert_session(_session("s4", "Old Title"))
    updated = _session("s4", "New Title")
    store.upsert_session(updated)
    session = store.get_session("s4")
    assert session["title"] == "New Title"


def test_upsert_updates_messages(store):
    store.upsert_session(_session("s5", "Chat", messages=[{"role": "user", "text": "hi"}]))
    msgs = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]
    store.upsert_session(_session("s5", "Chat", messages=msgs))
    session = store.get_session("s5")
    assert len(session["messages"]) == 2


def test_list_sessions_excludes_messages(store):
    msgs = [{"role": "user", "text": f"x{i}"} for i in range(10)]
    store.upsert_session(_session("s6", "Big", messages=msgs))
    sessions = store.list_sessions()
    # list_sessions returns empty messages list for speed
    assert sessions[0]["messages"] == []
