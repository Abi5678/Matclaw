"""Sanity checks for scripts/reset_local_state.py (dry-run only)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_reset_script_dry_run_exits_zero() -> None:
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "reset_local_state.py"
    r = subprocess.run(
        [sys.executable, str(script), "--dry-run"],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
