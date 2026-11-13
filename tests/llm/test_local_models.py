"""Discovery and selection of models already running on this machine."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.matclaw.llm.local_models import (
    discover_local_models,
    is_allowed_local_base_url,
    upsert_local_selection,
)
from src.matclaw.llm.llm_client import resolve_api_key


class _QuietHandler(BaseHTTPRequestHandler):
    routes: dict[str, dict] = {}

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        payload = self.routes.get(self.path)
        if payload is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _serve(routes: dict[str, dict]) -> tuple[ThreadingHTTPServer, str]:
    handler = type("Handler", (_QuietHandler,), {"routes": routes})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


def test_ollama_tags_are_preferred_over_openai_listing():
    server, origin = _serve({
        "/api/tags": {
            "models": [{
                "name": "llama3.2:latest",
                "details": {"parameter_size": "3.2B", "family": "llama"},
            }],
        },
        "/v1/models": {"data": [{"id": "ignored-openai-id"}]},
    })
    try:
        found = discover_local_models(targets=[("Ollama", origin)], timeout=1.0)
    finally:
        server.shutdown()
    assert len(found) == 1
    assert found[0]["model"] == "llama3.2:latest"
    assert found[0]["label"] == "llama3.2:latest (3.2B)"
    assert found[0]["provider"] == "local"
    assert found[0]["base_url"] == f"{origin}/v1"
    assert found[0]["source"] == "Ollama"


def test_openai_compatible_server_is_listed():
    server, origin = _serve({
        "/v1/models": {"data": [{"id": "qwen2.5-7b-instruct", "owned_by": "lmstudio"}]},
    })
    try:
        found = discover_local_models(targets=[("LM Studio", origin)], timeout=1.0)
    finally:
        server.shutdown()
    assert found == [{
        "model": "qwen2.5-7b-instruct",
        "label": "qwen2.5-7b-instruct (lmstudio)",
        "provider": "local",
        "base_url": f"{origin}/v1",
        "source": "LM Studio",
    }]


def test_unreachable_server_is_skipped():
    found = discover_local_models(
        targets=[("Ollama", "http://127.0.0.1:1")],
        timeout=0.2,
    )
    assert found == []


@pytest.mark.parametrize("url, allowed", [
    ("http://127.0.0.1:11434/v1", True),
    ("http://localhost:1234/v1", True),
    ("http://[::1]:8080/v1", True),
    ("http://example.com/v1", False),
    ("http://169.254.169.254/latest", False),
    ("http://user:pass@127.0.0.1:11434/v1", False),
    ("file:///etc/passwd", False),
    ("", False),
])
def test_only_loopback_base_urls_are_selectable(url, allowed):
    assert is_allowed_local_base_url(url) is allowed


def test_selecting_a_local_model_activates_it_and_reuses_the_entry():
    models: list[dict] = [{
        "id": "cloud",
        "provider": "nvidia",
        "model": "nemotron",
        "api_key": "secret",
        "label": "Cloud",
        "active": True,
    }]
    selected = upsert_local_selection(
        models,
        model="llama3.2:latest",
        base_url="http://127.0.0.1:11434",
        label="llama3.2:latest (3.2B)",
        source="Ollama",
        new_id="local-1",
    )
    assert selected["id"] == "local-1"
    assert selected["active"] is True
    assert selected["base_url"] == "http://127.0.0.1:11434/v1"
    assert selected["api_key"] == ""
    assert models[0]["active"] is False

    again = upsert_local_selection(
        models,
        model="llama3.2:latest",
        base_url="http://127.0.0.1:11434/v1",
        label="llama3.2:latest (3.2B)",
        source="Ollama",
        new_id="local-2",
    )
    assert again["id"] == "local-1"
    assert len(models) == 2


def test_remote_address_cannot_be_selected_as_local():
    with pytest.raises(ValueError):
        upsert_local_selection(
            [],
            model="llama3.2",
            base_url="http://example.com/v1",
            new_id="nope",
        )


def test_local_provider_does_not_need_an_api_key():
    assert resolve_api_key("local", None) == "local"
    assert resolve_api_key("local", "") == "local"
    assert resolve_api_key("openai-compatible", None, base_url="http://127.0.0.1:11434/v1") == "local"
    assert resolve_api_key("openai-compatible", None, base_url="https://api.example.com/v1") is None
