"""
Enterprise API key authentication with RBAC and audit logging.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
import time
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

Role = Literal["admin", "developer", "viewer"]

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin":     {"read", "write", "execute", "manage"},
    "developer": {"read", "write", "execute"},
    "viewer":    {"read"},
}


class APIKey(BaseModel):
    id: str
    label: str
    key_prefix: str          # first 12 chars shown in UI
    key_hash: str            # SHA-256 — never returned to client
    role: Role
    tenant_id: str
    created_at: int
    last_used_at: int | None = None
    enabled: bool = True
    rate_limit_rpm: int = 60

    def has_permission(self, perm: str) -> bool:
        return perm in ROLE_PERMISSIONS.get(self.role, set())

    def safe_dict(self) -> dict:
        """Return a version safe to expose via API (no key_hash)."""
        d = self.model_dump()
        d.pop("key_hash", None)
        return d


class APIKeyStore:
    """SQLite-backed API key store with audit log."""

    def __init__(self, db_path: str) -> None:
        self._db = db_path
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    key_prefix TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL DEFAULT 'developer',
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    rate_limit_rpm INTEGER NOT NULL DEFAULT 60
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    key_id TEXT,
                    tenant_id TEXT NOT NULL DEFAULT 'default',
                    method TEXT,
                    path TEXT,
                    status_code INTEGER,
                    duration_ms INTEGER,
                    request_id TEXT,
                    user_agent TEXT,
                    ip TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_tenant ON audit_log(tenant_id)")

    # ── Key management ─────────────────────────────────────────────────────

    def create_key(
        self,
        label: str,
        role: Role = "developer",
        tenant_id: str = "default",
        rate_limit_rpm: int = 60,
    ) -> tuple[str, APIKey]:
        """Create a new API key. Returns (raw_key, APIKey). raw_key shown once."""
        raw_key = f"mc_{secrets.token_urlsafe(32)}"
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        key_id = secrets.token_hex(8)
        now = int(time.time())
        ak = APIKey(
            id=key_id, label=label,
            key_prefix=raw_key[:12],
            key_hash=key_hash, role=role,
            tenant_id=tenant_id,
            created_at=now,
            rate_limit_rpm=rate_limit_rpm,
        )
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO api_keys VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ak.id, ak.label, ak.key_prefix, ak.key_hash,
                 ak.role, ak.tenant_id, ak.created_at,
                 ak.last_used_at, int(ak.enabled), ak.rate_limit_rpm),
            )
        logger.info("API key created: %s (%s, tenant=%s)", label, role, tenant_id)
        return raw_key, ak

    def validate_key(self, raw_key: str) -> APIKey | None:
        """Validate a raw API key string. Updates last_used_at on success."""
        if not raw_key:
            return None
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_keys WHERE key_hash=? AND enabled=1",
                (key_hash,),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE api_keys SET last_used_at=? WHERE id=?",
                (int(time.time()), row["id"]),
            )
        return APIKey(**dict(row))

    def list_keys(self) -> list[APIKey]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys ORDER BY created_at DESC"
            ).fetchall()
        return [APIKey(**dict(r)) for r in rows]

    def revoke_key(self, key_id: str) -> bool:
        with self._conn() as conn:
            r = conn.execute("UPDATE api_keys SET enabled=0 WHERE id=?", (key_id,))
        return r.rowcount > 0

    # ── Audit log ──────────────────────────────────────────────────────────

    def log_request(
        self, *, key_id: str | None, tenant_id: str,
        method: str, path: str, status_code: int,
        duration_ms: int, request_id: str,
        user_agent: str, ip: str,
    ) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO audit_log "
                    "(ts,key_id,tenant_id,method,path,status_code,"
                    "duration_ms,request_id,user_agent,ip) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (int(time.time()), key_id, tenant_id, method, path,
                     status_code, duration_ms, request_id, user_agent, ip),
                )
        except Exception:
            pass  # audit log must never crash the request

    def get_audit_log(self, limit: int = 100, tenant_id: str | None = None) -> list[dict]:
        with self._conn() as conn:
            if tenant_id:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE tenant_id=? ORDER BY ts DESC LIMIT ?",
                    (tenant_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
                ).fetchall()
        return [dict(r) for r in rows]


__all__ = ["APIKey", "APIKeyStore", "Role", "ROLE_PERMISSIONS"]
