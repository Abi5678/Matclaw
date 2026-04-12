"""
MatClaw server entry point.
Run with:  matclaw-server
       or: python -m src.matclaw.api.main
       or: uvicorn src.matclaw.api.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os
import webbrowser
import threading


def main() -> None:
    import uvicorn

    host = os.getenv("MATCLAW_HOST", "0.0.0.0")
    port = int(os.getenv("MATCLAW_PORT", "8000"))
    open_browser = os.getenv("MATCLAW_OPEN_BROWSER", "true").lower() == "true"

    if open_browser:
        # Open browser after a short delay so the server has time to start
        def _open():
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(
        "matclaw.api.server:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
