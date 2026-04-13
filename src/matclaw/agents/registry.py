"""
SQLite-backed agent registry.

Thread-safe agent storage replacing the old JSON file approach.
Mirrors the pattern used by SessionStore, Scheduler, and PipelineStore.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

# Default location: project root (same level as other .matclaw_*.sqlite3 files)
_DEFAULT_DB = str(Path(__file__).resolve().parent.parent.parent.parent / ".matclaw_agents.sqlite3")

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS agents (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    system_prompt     TEXT NOT NULL,
    trigger_condition TEXT NOT NULL,
    callable_by_others INTEGER NOT NULL DEFAULT 1,
    allowed_tools     TEXT NOT NULL DEFAULT '[]'
)
"""


class AgentDefinition(BaseModel):
    id: str = Field(description="Unique English identifier, e.g. 'simulink-wrangler'")
    name: str = Field(description="Display name, e.g. 'Simulink Modeler'")
    system_prompt: str = Field(description="The primary persona, workflow rules, and formatting preferences.")
    trigger_condition: str = Field(description="When to call this agent in a long-horizon task.")
    callable_by_others: bool = Field(default=True)
    allowed_tools: List[str] = Field(default_factory=list, description="List of MCP tools the agent is allowed to use.")


class AgentRegistry:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or _DEFAULT_DB
        self._lock = threading.Lock()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_TABLE)
            conn.commit()
            self._migrate_json_if_needed(conn)
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _migrate_json_if_needed(self, conn: sqlite3.Connection) -> None:
        """One-time migration: import agents from the old agents_db.json if it exists."""
        json_path = Path(__file__).parent / "agents_db.json"
        if not json_path.exists():
            return
        # Only migrate if table is empty
        count = conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
        if count > 0:
            return
        try:
            with open(json_path, "r") as f:
                data = json.load(f)
            for item in data:
                agent = AgentDefinition(**item)
                conn.execute(
                    "INSERT OR IGNORE INTO agents (id, name, system_prompt, trigger_condition, callable_by_others, allowed_tools) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (agent.id, agent.name, agent.system_prompt, agent.trigger_condition,
                     int(agent.callable_by_others), json.dumps(agent.allowed_tools)),
                )
            conn.commit()
            # Rename old file so migration doesn't re-run
            json_path.rename(json_path.with_suffix(".json.migrated"))
        except Exception:
            pass  # non-critical — skip if corrupt

    # ── read ─────────────────────────────────────────────────────────────────

    def list_agents(self) -> List[AgentDefinition]:
        with self._lock:
            conn = self._conn()
            try:
                rows = conn.execute("SELECT * FROM agents ORDER BY name").fetchall()
                return [self._row_to_agent(r) for r in rows]
            finally:
                conn.close()

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
                return self._row_to_agent(row) if row else None
            finally:
                conn.close()

    # ── write ────────────────────────────────────────────────────────────────

    def save_agent(self, agent: AgentDefinition) -> AgentDefinition:
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO agents (id, name, system_prompt, trigger_condition, callable_by_others, allowed_tools)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name              = excluded.name,
                        system_prompt     = excluded.system_prompt,
                        trigger_condition = excluded.trigger_condition,
                        callable_by_others = excluded.callable_by_others,
                        allowed_tools     = excluded.allowed_tools
                    """,
                    (agent.id, agent.name, agent.system_prompt, agent.trigger_condition,
                     int(agent.callable_by_others), json.dumps(agent.allowed_tools)),
                )
                conn.commit()
            finally:
                conn.close()
        return agent

    def delete_agent(self, agent_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _row_to_agent(row: sqlite3.Row) -> AgentDefinition:
        return AgentDefinition(
            id=row["id"],
            name=row["name"],
            system_prompt=row["system_prompt"],
            trigger_condition=row["trigger_condition"],
            callable_by_others=bool(row["callable_by_others"]),
            allowed_tools=json.loads(row["allowed_tools"]),
        )
