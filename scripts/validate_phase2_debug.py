#!/usr/bin/env python3
"""
Phase 2 integration validation:
fail -> DebugAgent fix -> .bak apply -> memory artifact check
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.matclaw.config.base_config import DebugAgentSettings, MatClawSettings
from src.matclaw.debug.debug_agent import DebugAgent
from src.matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest, MatlabCallResult
from src.matclaw.memory.memory_manager import MemoryManager


def main() -> int:
    repo = REPO_ROOT
    matlab_root = repo / "matlab"
    matlab_root.mkdir(parents=True, exist_ok=True)
    target = matlab_root / "phase2_debug_demo.m"
    target.write_text(
        "function y = phase2_debug_demo(a,b)\n"
        "y = a + b;\n"
        "end\n",
        encoding="utf-8",
    )

    settings = MatClawSettings()
    bridge = MatlabBridge(settings=settings.matlab)
    bridge.start()
    if not bridge.is_healthy():
        print("FAIL: MATLAB bridge not healthy.")
        return 2

    bridge.addpath(str(matlab_root))

    mm = MemoryManager(persist_directory=str(repo / ".matclaw_chromadb"))
    try:
        mm._ensure_client()
    except Exception:
        mm = None  # type: ignore[assignment]

    dbg = DebugAgent(
        matlab_bridge=bridge,
        settings=DebugAgentSettings(matlab_root=str(matlab_root), max_fix_attempts=2, debug_max_rounds=2),
        memory_manager=mm,
        journal_path=Path(settings.lab_journal.path) if settings.lab_journal.enabled else None,
    )

    req = MatlabCallRequest(function="phase2_debug_demo", args=[1], nargout=1, timeout_seconds=5)
    fail = MatlabCallResult(success=False, error="Not enough input arguments.")
    res = dbg.handle_failure(req, fail)

    print(f"fixed={res.fixed} applied_to_source={res.applied_to_source}")
    print(f"candidate_count={len(res.candidate_functions)} attempted_fix_file={res.attempted_fix_file is not None}")
    print(f"suggested={res.suggested_changes}")

    bak = target.with_suffix(".m.bak")
    print(f"bak_exists={bak.exists()}")

    if mm is not None:
        try:
            rows = mm.query_context("debug fix phase2_debug_demo", n_results=3)
            print(f"memory_hits={len(rows)}")
        except Exception:
            print("memory_hits=query_failed")
    else:
        print("memory_hits=memory_unavailable")

    bridge.stop()
    return 0 if res.fixed else 1


if __name__ == "__main__":
    raise SystemExit(main())
