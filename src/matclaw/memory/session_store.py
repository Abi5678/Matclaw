"""
SQLite-backed session store.

Sessions are persisted server-side so chat history, files, and plots survive
browser refreshes, page reloads, and server restarts.
"""
import json
import sqlite3
import threading
from typing import Any

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    messages    TEXT NOT NULL DEFAULT '[]'
)
"""


class SessionStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_TABLE)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── read ─────────────────────────────────────────────────────────────────

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return all sessions ordered by updated_at desc, WITHOUT messages (for speed)."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [
                {"id": r[0], "title": r[1], "createdAt": r[2], "updatedAt": r[3], "messages": []}
                for r in rows
            ]
        finally:
            conn.close()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a single session with full messages."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id, title, created_at, updated_at, messages FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            return {
                "id": row[0],
                "title": row[1],
                "createdAt": row[2],
                "updatedAt": row[3],
                "messages": json.loads(row[4]),
            }
        finally:
            conn.close()

    # ── write ────────────────────────────────────────────────────────────────

    def upsert_session(self, session: dict[str, Any]) -> None:
        """Insert or replace a session (client is source of truth for content)."""
        with self._lock:
            conn = self._conn()
            try:
                conn.execute(
                    """
                    INSERT INTO sessions (id, title, created_at, updated_at, messages)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title      = excluded.title,
                        updated_at = excluded.updated_at,
                        messages   = excluded.messages
                    """,
                    (
                        session["id"],
                        session.get("title", "Session"),
                        session.get("createdAt", 0),
                        session.get("updatedAt", 0),
                        json.dumps(session.get("messages", [])),
                    ),
                )
                conn.commit()
            finally:
                conn.close()

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()
