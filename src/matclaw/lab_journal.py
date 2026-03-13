"""
Shared lab journal: append RPI-style entries to LAB_JOURNAL.md.

Used by Sentry, MCP tools, and any subsystem that performs autonomous actions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def append_lab_journal(
    journal_path: Path | str,
    summary: str,
    source: str = "MCP",
    artifact_paths: list[str] | None = None,
) -> None:
    """
    Append one line to the lab journal with timestamp, summary, and optional artifact links.

    Args:
        journal_path: Path to LAB_JOURNAL.md.
        summary: Human-readable summary of the action.
        source: Origin of the action (e.g. "MCP", "Sentry", "Telegram").
        artifact_paths: Optional list of artifact file paths to link.
    """
    path = Path(journal_path)
    artifacts = artifact_paths or []
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M %p")
        line = f"- **{time_str}** — [{source}] {summary}"
        if artifacts:
            links = " ".join(f"[{Path(p).name}]({p})" for p in artifacts)
            line += f" — {links}"
        line += "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        logger.info("Appended to lab journal: %s", path)
    except Exception:
        logger.exception("Failed to append to lab journal: %s", path)
