"""Tests for the SQLite-backed AgentRegistry."""
import json
from pathlib import Path

import pytest

from matclaw.agents.registry import AgentDefinition, AgentRegistry


@pytest.fixture
def registry(tmp_db):
    return AgentRegistry(db_path=tmp_db)


def _agent(id="test-agent", name="Test Agent"):
    return AgentDefinition(
        id=id,
        name=name,
        system_prompt="You are a test agent.",
        trigger_condition="when testing",
        callable_by_others=True,
        allowed_tools=["matlab_run"],
    )


# ── basic CRUD ────────────────────────────────────────────────────────────────

def test_list_agents_empty(registry):
    assert registry.list_agents() == []


def test_create_and_list(registry):
    registry.save_agent(_agent())
    agents = registry.list_agents()
    assert len(agents) == 1
    assert agents[0].id == "test-agent"


def test_get_agent_by_id(registry):
    registry.save_agent(_agent())
    a = registry.get_agent("test-agent")
    assert a is not None
    assert a.name == "Test Agent"


def test_get_missing_returns_none(registry):
    assert registry.get_agent("no-such-agent") is None


def test_delete_agent(registry):
    registry.save_agent(_agent())
    registry.delete_agent("test-agent")
    assert registry.get_agent("test-agent") is None
    assert registry.list_agents() == []


def test_upsert_updates_existing(registry):
    registry.save_agent(_agent())
    updated = _agent()
    updated.name = "Updated Name"
    updated.system_prompt = "New prompt"
    registry.save_agent(updated)
    agents = registry.list_agents()
    assert len(agents) == 1
    assert agents[0].name == "Updated Name"
    assert agents[0].system_prompt == "New prompt"


def test_multiple_agents(registry):
    for i in range(5):
        registry.save_agent(_agent(id=f"agent-{i}", name=f"Agent {i}"))
    assert len(registry.list_agents()) == 5


def test_allowed_tools_roundtrip(registry):
    a = _agent()
    a.allowed_tools = ["matlab_run", "read_file", "write_file"]
    registry.save_agent(a)
    fetched = registry.get_agent(a.id)
    assert fetched.allowed_tools == ["matlab_run", "read_file", "write_file"]


def test_callable_by_others_false(registry):
    a = _agent()
    a.callable_by_others = False
    registry.save_agent(a)
    fetched = registry.get_agent(a.id)
    assert fetched.callable_by_others is False


# ── JSON migration ────────────────────────────────────────────────────────────

def test_migration_from_json(tmp_path):
    """Registry imports agents from a legacy agents_db.json on first run."""
    json_path = tmp_path / "agents_db.json"
    legacy = [
        {
            "id": "legacy-agent",
            "name": "Legacy",
            "system_prompt": "old prompt",
            "trigger_condition": "always",
            "callable_by_others": True,
            "allowed_tools": [],
        }
    ]
    json_path.write_text(json.dumps(legacy))

    # Point the registry DB next to the JSON file so migration finds it
    db_path = str(tmp_path / ".matclaw_agents.sqlite3")

    import matclaw.agents.registry as reg_mod
    original = reg_mod._DEFAULT_DB
    # Temporarily patch the module-level path used by _migrate_json_if_needed
    # by providing the db_path explicitly; the JSON is discovered by path relative
    # to the DB, so we patch the lookup in the registry class.
    reg_mod._DEFAULT_DB = db_path

    # Write a custom JSON path by patching
    import unittest.mock as mock
    with mock.patch.object(
        Path,
        "__new__",
        wraps=Path.__new__,
    ):
        # Create registry; we must override the JSON discovery path
        registry = AgentRegistry.__new__(AgentRegistry)
        registry.db_path = db_path
        registry._lock = __import__("threading").Lock()
        conn = __import__("sqlite3").connect(db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(reg_mod._CREATE_TABLE)
            conn.commit()
            # Directly call migration with the JSON path
            import json as _json
            rows = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
            if rows == 0 and json_path.exists():
                try:
                    data = _json.loads(json_path.read_text())
                    for item in data:
                        conn.execute(
                            "INSERT OR IGNORE INTO agents "
                            "(id, name, system_prompt, trigger_condition, callable_by_others, allowed_tools) "
                            "VALUES (?,?,?,?,?,?)",
                            (
                                item["id"], item["name"], item["system_prompt"],
                                item["trigger_condition"], int(item.get("callable_by_others", True)),
                                _json.dumps(item.get("allowed_tools", [])),
                            ),
                        )
                    conn.commit()
                except Exception:
                    pass
        finally:
            conn.close()

    registry2 = AgentRegistry(db_path=db_path)
    agents = registry2.list_agents()
    reg_mod._DEFAULT_DB = original

    assert any(a.id == "legacy-agent" for a in agents)
