"""
Enterprise middleware stack:
- Request ID injection (X-Request-Id)
- Rate limiting (sliding window per API key)
- Audit logging (every request → SQLite)
- In-memory metrics collection
"""
from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

if TYPE_CHECKING:
    from src.matclaw.api.auth import APIKeyStore

logger = logging.getLogger(__name__)

# Sliding window rate-limit buckets: key_id → deque of timestamps
_rate_windows: dict[str, deque] = defaultdict(deque)

# Paths that bypass auth + rate limiting
_BYPASS_PATHS = frozenset({
    "/health", "/metrics", "/docs", "/openapi.json",
    "/redoc", "/favicon.ico",
})


class EnterpriseMiddleware(BaseHTTPMiddleware):
    """
    One middleware handles auth, rate-limiting, request IDs, and audit logging.
    Each concern is skipped gracefully if disabled.
    """

    def __init__(
        self,
        app,
        key_store: "APIKeyStore | None" = None,
        auth_enabled: bool = False,
        metrics: "MetricsStore | None" = None,
    ):
        super().__init__(app)
        self._key_store = key_store
        self._auth_enabled = auth_enabled
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next) -> Response:
        t0 = time.monotonic()
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        request.state.tenant_id = "default"
        request.state.api_key = None

        path = request.url.path

        # ── Auth + rate limit ──────────────────────────────────────────────
        if self._auth_enabled and path not in _BYPASS_PATHS:
            raw_key = (
                request.headers.get("X-API-Key")
                or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
            )
            if not raw_key:
                return JSONResponse(
                    {"error": "Missing API key. Pass X-API-Key header.", "request_id": request_id},
                    status_code=401,
                    headers={"X-Request-Id": request_id},
                )

            ak = self._key_store.validate_key(raw_key) if self._key_store else None
            if not ak:
                return JSONResponse(
                    {"error": "Invalid or revoked API key.", "request_id": request_id},
                    status_code=401,
                    headers={"X-Request-Id": request_id},
                )

            # Sliding-window rate limit (per key, per minute)
            now_mono = time.monotonic()
            window = _rate_windows[ak.id]
            cutoff = now_mono - 60.0
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= ak.rate_limit_rpm:
                return JSONResponse(
                    {"error": "Rate limit exceeded.", "retry_after": 60, "request_id": request_id},
                    status_code=429,
                    headers={"X-Request-Id": request_id, "Retry-After": "60"},
                )
            window.append(now_mono)

            request.state.tenant_id = ak.tenant_id
            request.state.api_key = ak

        # ── Execute request ────────────────────────────────────────────────
        response = await call_next(request)
        duration_ms = int((time.monotonic() - t0) * 1000)

        response.headers["X-Request-Id"] = request_id
        response.headers["X-Duration-Ms"] = str(duration_ms)

        # ── Audit log ──────────────────────────────────────────────────────
        if self._key_store and path not in _BYPASS_PATHS:
            ak = getattr(request.state, "api_key", None)
            self._key_store.log_request(
                key_id=ak.id if ak else None,
                tenant_id=getattr(request.state, "tenant_id", "default"),
                method=request.method,
                path=path,
                status_code=response.status_code,
                duration_ms=duration_ms,
                request_id=request_id,
                user_agent=request.headers.get("user-agent", ""),
                ip=request.client.host if request.client else "",
            )

        # ── Metrics ────────────────────────────────────────────────────────
        if self._metrics:
            self._metrics.record(path, request.method, response.status_code, duration_ms)

        return response


class MetricsStore:
    """In-memory metrics with Prometheus-compatible snapshot."""

    def __init__(self) -> None:
        self._req_count: dict[str, int] = defaultdict(int)
        self._err_count: dict[str, int] = defaultdict(int)
        self._durations: dict[str, list[int]] = defaultdict(list)
        self._start_time = time.time()

    def record(self, path: str, method: str, status: int, duration_ms: int) -> None:
        key = f"{method}:{path}"
        self._req_count[key] += 1
        if status >= 400:
            self._err_count[key] += 1
        bucket = self._durations[key]
        bucket.append(duration_ms)
        if len(bucket) > 1000:
            self._durations[key] = bucket[-500:]

    def snapshot(self) -> dict:
        endpoints = {}
        for key, count in self._req_count.items():
            durations = self._durations.get(key, [1])
            avg = sum(durations) / len(durations)
            s = sorted(durations)
            p95 = s[max(0, int(len(s) * 0.95) - 1)]
            endpoints[key] = {
                "requests": count,
                "errors": self._err_count.get(key, 0),
                "avg_duration_ms": round(avg, 1),
                "p95_duration_ms": p95,
            }
        return {
            "uptime_seconds": int(time.time() - self._start_time),
            "total_requests": sum(self._req_count.values()),
            "total_errors": sum(self._err_count.values()),
            "endpoints": endpoints,
        }


__all__ = ["EnterpriseMiddleware", "MetricsStore"]
