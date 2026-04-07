#!/usr/bin/env python3
"""
Remove local MatClaw persistence: semantic memory (ChromaDB), sessions, experiments,
episodic log, pipelines, scheduler DB, async state, plot files, and optional caches.

Does not delete: .env, versioned agent definitions (src/matclaw/agents/agents_db.json),
or API key DB unless --include-auth.

Usage:
  python scripts/reset_local_state.py [--dry-run] [--kill-server] [--include-auth] [--include-projects]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _kill_listeners_on_port(port: int) -> list[str]:
    killed: list[str] = []
    try:
        r = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return killed
    if r.returncode != 0 or not (r.stdout or "").strip():
        return killed
    for pid in r.stdout.split():
        pid = pid.strip()
        if not pid.isdigit():
            continue
        subprocess.run(["kill", "-9", pid], check=False, capture_output=True)
        killed.append(pid)
    return killed


def _clear_plots_dir(plots_dir: Path, dry_run: bool) -> int:
    n = 0
    if not plots_dir.is_dir():
        return n
    for p in list(plots_dir.iterdir()):
        if p.name.startswith("."):
            continue
        n += 1
        if dry_run:
            continue
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                p.unlink()
            except OSError:
                pass
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="Reset MatClaw local state (memory, history, plots).")
    ap.add_argument("--dry-run", action="store_true", help="Print actions only.")
    ap.add_argument("--kill-server", action="store_true", help="SIGKILL any process listening on port 8000 (typical API).")
    ap.add_argument("--port", type=int, default=8000, help="Port for --kill-server (default 8000).")
    ap.add_argument(
        "--include-auth",
        action="store_true",
        help="Also remove .matclaw_auth.sqlite3 (stored API keys from Control Plane).",
    )
    ap.add_argument(
        "--include-projects",
        action="store_true",
        help="Also remove generated files under projects/ (keeps directory).",
    )
    args = ap.parse_args()

    root = _repo_root()
    os.chdir(root)

    if args.kill_server and not args.dry_run:
        killed = _kill_listeners_on_port(args.port)
        if killed:
            print(f"Killed PID(s) on port {args.port}: {', '.join(killed)}")
        else:
            print(f"No listener found on port {args.port}.")

    sqlite_files = [
        ".matclaw_experiments.sqlite3",
        ".matclaw_episodic.sqlite3",
        ".matclaw_sessions.sqlite3",
        ".matclaw_pipelines.sqlite3",
        ".matclaw_scheduler.sqlite3",
        ".matclaw_archive.sqlite3",
    ]
    if args.include_auth:
        sqlite_files.append(".matclaw_auth.sqlite3")

    json_files = [
        ".matclaw_async_state.json",
        ".matclaw_memory.json",
        ".matclaw_models.json",
    ]

    dirs = [".matclaw_chromadb"]

    plots_dir = root / "plots"
    projects_dir = root / "projects"

    planned: list[str] = []

    for name in sqlite_files:
        p = root / name
        if p.is_file():
            planned.append(str(p.relative_to(root)))

    for name in json_files:
        p = root / name
        if p.is_file():
            planned.append(str(p.relative_to(root)))

    for name in dirs:
        p = root / name
        if p.is_dir():
            planned.append(str(p.relative_to(root)) + "/")

    if plots_dir.is_dir():
        for child in plots_dir.iterdir():
            if child.name.startswith("."):
                continue
            planned.append(str(child.relative_to(root)))

    if args.include_projects and projects_dir.is_dir():
        for child in projects_dir.iterdir():
            if child.name.startswith("."):
                continue
            planned.append(str(child.relative_to(root)))

    if not planned and not plots_dir.exists():
        print("Nothing to remove (already clean).")
        return 0

    print("Planned removals:" if args.dry_run else "Removing:")
    for line in sorted(planned):
        print(f"  {line}")

    if args.dry_run:
        return 0

    for name in sqlite_files:
        p = root / name
        if p.is_file():
            p.unlink(missing_ok=True)

    for name in json_files:
        p = root / name
        if p.is_file():
            p.unlink(missing_ok=True)

    for name in dirs:
        p = root / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)

    plots_dir.mkdir(exist_ok=True)
    _clear_plots_dir(plots_dir, dry_run=False)

    if args.include_projects and projects_dir.is_dir():
        for child in list(projects_dir.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass

    print(
        "Done. Restart the API (e.g. ./start.sh or python -m src.matclaw.api.main). "
        "Reload the browser (hard refresh) so the UI drops cached sessions: empty server "
        "list now clears localStorage on load, or clear site data for localhost manually."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
