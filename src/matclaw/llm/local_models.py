"""Discover OpenAI-compatible models already running on this machine.

Probes the usual local servers (Ollama, LM Studio, llama.cpp, vLLM, LocalAI)
and any origins listed in ``MATCLAW_LOCAL_LLM_URLS``. Only loopback addresses
are contacted unless the operator named an extra origin in that variable.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlsplit

import httpx

# (display source, origin without /v1)
_DEFAULT_PROBES: tuple[tuple[str, str], ...] = (
    ("Ollama", "http://127.0.0.1:11434"),
    ("LM Studio", "http://127.0.0.1:1234"),
    ("llama.cpp", "http://127.0.0.1:8080"),
    ("vLLM", "http://127.0.0.1:8001"),
    ("LocalAI", "http://127.0.0.1:8081"),
)

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _env_origins() -> list[str]:
    raw = os.environ.get("MATCLAW_LOCAL_LLM_URLS", "")
    return [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]


def _host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().strip("[]")
    return host


def allowed_local_hosts() -> set[str]:
    hosts = set(_LOOPBACK_HOSTS)
    for origin in _env_origins():
        host = _host_of(origin)
        if host:
            hosts.add(host)
    return hosts


def is_allowed_local_base_url(url: str | None) -> bool:
    """True when a chat base URL is loopback or an operator-configured origin."""
    if not url or not url.strip():
        return False
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    host = (parsed.hostname or "").lower().strip("[]")
    if not host:
        return False
    return host in allowed_local_hosts()


def _origin(url: str) -> str:
    url = url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url


def _openai_base(origin: str) -> str:
    return f"{_origin(origin)}/v1"


def probe_targets() -> list[tuple[str, str]]:
    """Return (source label, origin) pairs to contact."""
    seen: set[str] = set()
    targets: list[tuple[str, str]] = []
    for source, origin in _DEFAULT_PROBES:
        key = _origin(origin)
        seen.add(key)
        targets.append((source, key))
    for origin in _env_origins():
        if not is_allowed_local_base_url(origin) and not is_allowed_local_base_url(_openai_base(origin)):
            continue
        key = _origin(origin)
        if key in seen:
            continue
        seen.add(key)
        targets.append(("Local", key))
    return targets


def _models_from_ollama(payload: dict[str, Any], source: str, base_url: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for item in payload.get("models") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("model") or "").strip()
        if not name:
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        size = str(details.get("parameter_size") or "").strip()
        label = f"{name} ({size})" if size else name
        found.append({
            "model": name,
            "label": label,
            "provider": "local",
            "base_url": base_url,
            "source": source,
        })
    return found


def _models_from_openai(payload: dict[str, Any], source: str, base_url: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    data = payload.get("data")
    if data is None and isinstance(payload.get("models"), list):
        data = payload.get("models")
    for item in data or []:
        if isinstance(item, str):
            name = item.strip()
            owned_by = ""
        elif isinstance(item, dict):
            name = str(item.get("id") or item.get("name") or "").strip()
            owned_by = str(item.get("owned_by") or "").strip()
        else:
            continue
        if not name:
            continue
        label = f"{name} ({owned_by})" if owned_by and owned_by not in {"openai", "system"} else name
        found.append({
            "model": name,
            "label": label,
            "provider": "local",
            "base_url": base_url,
            "source": source,
        })
    return found


def _probe_one(source: str, origin: str, timeout: float) -> list[dict[str, str]]:
    base_url = _openai_base(origin)
    timeout_s = httpx.Timeout(timeout)
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            tags = client.get(f"{origin}/api/tags")
            if tags.status_code == 200:
                try:
                    models = _models_from_ollama(tags.json(), source, base_url)
                except ValueError:
                    models = []
                if models:
                    return models
            listing = client.get(f"{base_url}/models")
            if listing.status_code != 200:
                return []
            try:
                return _models_from_openai(listing.json(), source, base_url)
            except ValueError:
                return []
    except httpx.HTTPError:
        return []


def discover_local_models(
    *,
    timeout: float = 0.6,
    targets: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    """Return models served by local OpenAI-compatible runtimes."""
    chosen = targets if targets is not None else probe_targets()
    if not chosen:
        return []
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    workers = min(8, len(chosen))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_probe_one, source, origin, timeout) for source, origin in chosen]
        for future in as_completed(futures):
            for item in future.result():
                key = (item["base_url"], item["model"])
                if key in seen:
                    continue
                seen.add(key)
                found.append(item)
    found.sort(key=lambda item: (item["source"].lower(), item["label"].lower()))
    return found


def upsert_local_selection(
    models: list[dict[str, Any]],
    *,
    model: str,
    base_url: str,
    label: str = "",
    source: str = "Local",
    new_id: str,
) -> dict[str, Any]:
    """Mark one local model active, creating the saved entry when it is new.

    Mutates and returns the selected record. Raises ValueError when the
    address is not a local runtime this process is allowed to call.
    """
    name = (model or "").strip()
    if not name or len(name) > 200 or any(ch in name for ch in "\r\n"):
        raise ValueError("Model name is required.")
    normalized = _openai_base(base_url) if base_url and not base_url.rstrip("/").endswith("/v1") else (base_url or "").strip().rstrip("/")
    if not is_allowed_local_base_url(normalized):
        raise ValueError("That address is not a local model server.")
    display = (label or "").strip() or name
    if source and source not in display:
        display = f"{display} · {source}"

    selected: dict[str, Any] | None = None
    for entry in models:
        same = (
            entry.get("provider") == "local"
            and entry.get("model") == name
            and str(entry.get("base_url") or "").rstrip("/") == normalized
        )
        entry["active"] = same
        if same:
            entry["label"] = display
            entry["source"] = source
            selected = entry
    if selected is None:
        selected = {
            "id": new_id,
            "provider": "local",
            "model": name,
            "api_key": "",
            "base_url": normalized,
            "label": display,
            "active": True,
            "source": source,
        }
        models.append(selected)
    return selected
