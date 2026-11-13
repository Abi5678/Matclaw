"""The settings API can list and activate a model running on this machine."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from src.matclaw.api import server


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"data": [{"id": "llama3.2:latest"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_list_and_select_local_model(monkeypatch, tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{httpd.server_address[1]}"
    monkeypatch.setattr(server, "MODELS_FILE", tmp_path / "models.json")
    import src.matclaw.llm.local_models as local_models

    monkeypatch.setattr(local_models, "probe_targets", lambda: [("Ollama", origin)])
    previous = (
        server.settings.llm.provider,
        server.settings.llm.model,
        server.settings.llm.api_key,
        server.settings.llm.base_url,
    )

    try:
        client = TestClient(server.app)
        listed = client.get("/api/settings/local-models")
        assert listed.status_code == 200
        body = listed.json()
        assert body[0]["model"] == "llama3.2:latest"
        assert body[0]["base_url"] == f"{origin}/v1"

        selected = client.post("/api/settings/models/use-local", json=body[0])
        assert selected.status_code == 200
        saved = selected.json()
        assert saved["active"] is True
        assert saved["provider"] == "local"
        assert server.settings.llm.provider == "local"
        assert server.settings.llm.model == "llama3.2:latest"
        assert server.settings.llm.base_url == f"{origin}/v1"

        rejected = client.post("/api/settings/models/use-local", json={
            "model": "remote",
            "base_url": "https://example.com/v1",
            "label": "Remote",
            "source": "Other",
        })
        assert rejected.status_code == 400
    finally:
        httpd.shutdown()
        (
            server.settings.llm.provider,
            server.settings.llm.model,
            server.settings.llm.api_key,
            server.settings.llm.base_url,
        ) = previous
