"""
File access guardrails for MatClaw.

Validates file paths before reading or modifying .m files and other
MATLAB workspace files.  Mirrors the pattern in guardrail.py:
Pydantic decision model + a single check function that is fail-closed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Set

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

MAX_FILE_SIZE_BYTES: int = 2 * 1024 * 1024  # 2 MB

ALLOWED_SUFFIXES: Set[str] = {".m", ".slx", ".mat", ".csv", ".mlx"}


# ---------------------------------------------------------------------------
# Decision model
# ---------------------------------------------------------------------------

class FileAccessDecision(BaseModel):
    """Result of a file access guard check."""

    allow: bool
    reason: str
    resolved_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Guard function
# ---------------------------------------------------------------------------

def _search_matlab_path(filename: str) -> Path | None:
    """Try to find a file on MATLAB's path using ``which()``."""
    try:
        from matclaw.matlab.matlab_bridge import MatlabBridge, MatlabCallRequest
        from matclaw.config.base_config import MatClawSettings
        settings = MatClawSettings()
        bridge = MatlabBridge(settings=settings.matlab)
        bridge.start()
        if not bridge.is_healthy():
            return None
        # Strip .m suffix for which() — MATLAB handles it
        stem = Path(filename).stem
        req = MatlabCallRequest(function="which", args=[stem], nargout=1)
        result = bridge.call(req)
        bridge.stop()
        if result.success and result.result and str(result.result).strip():
            found = Path(str(result.result).strip())
            if found.is_file():
                return found
    except Exception:
        pass
    return None


def _search_common_dirs(filename: str) -> Path | None:
    """Search common MATLAB directories for a file (case-insensitive)."""
    home = Path.home()
    search_dirs = [
        home / "Documents" / "MATLAB",
        home / "MATLAB",
        Path.cwd() / "matlab",
        Path.cwd() / "data_in",
    ]
    name = Path(filename).name
    name_lower = name.lower()
    for d in search_dirs:
        if not d.is_dir():
            continue
        # Direct match (case-insensitive on any OS)
        candidate = d / name
        if candidate.is_file():
            return candidate
        # Recursive search — use glob("*") + filter for case-insensitive match
        # (Python's rglob is case-sensitive even on macOS HFS+)
        suffix = Path(name).suffix.lower()
        for match in d.rglob(f"*{suffix}"):
            if match.is_file() and match.name.lower() == name_lower:
                return match
    return None


def guard_file_access(
    raw_path: str,
    workspace_roots: list[Path] | None = None,
    max_size: int = MAX_FILE_SIZE_BYTES,
    allowed_suffixes: set[str] | None = None,
    matlab_bridge: "Any | None" = None,
) -> FileAccessDecision:
    """
    Validate that *raw_path* points to a safe, readable MATLAB file.

    Checks (in order):
    1. Path resolves to an absolute location.
    2. If file doesn't exist at resolved path, search MATLAB path and
       common directories (~/Documents/MATLAB, etc.).
    3. Resolved path is under one of the allowed *workspace_roots*
       (expanded to include home MATLAB dirs).
    4. File exists and is a regular file.
    5. Suffix is in the allowed set.
    6. File size is within *max_size*.

    Returns a :class:`FileAccessDecision` that callers must inspect
    before proceeding.
    """

    suffixes = allowed_suffixes or ALLOWED_SUFFIXES

    # --- resolve -----------------------------------------------------------
    try:
        resolved = Path(raw_path).resolve()
    except (ValueError, OSError) as exc:
        return FileAccessDecision(
            allow=False,
            reason=f"Invalid path: {exc}",
        )

    # --- if file not found, search MATLAB path and common dirs -------------
    if not resolved.exists():
        filename = Path(raw_path).name
        # Add .m suffix if missing
        if not Path(filename).suffix:
            filename = filename + ".m"

        # Search common MATLAB directories first (fast, no engine needed)
        found = _search_common_dirs(filename)

        # Try MATLAB engine's which() as fallback
        if found is None:
            found = _search_matlab_path(filename)

        if found is not None:
            resolved = found.resolve()
            logger.info("File found via search: %s -> %s", raw_path, resolved)

    # --- workspace root check (path traversal guard) -----------------------
    roots = workspace_roots or [Path.cwd().resolve()]
    # Always include cwd so relative paths like "test.m" work
    cwd = Path.cwd().resolve()
    if cwd not in roots:
        roots = list(roots) + [cwd]
    # Also allow home MATLAB dirs and the file's own parent
    home = Path.home()
    for extra in [
        home / "Documents" / "MATLAB",
        home / "MATLAB",
        home / "Documents",
    ]:
        if extra.is_dir() and extra not in roots:
            roots.append(extra)
    # Allow the resolved file's parent directory (user clearly intends to access it)
    if resolved.is_file() and resolved.parent not in roots:
        roots.append(resolved.parent)

    under_root = False
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            under_root = True
            break
        except ValueError:
            continue

    if not under_root:
        root_strs = ", ".join(str(r) for r in roots)
        logger.warning(
            "File access denied (path traversal): %s is not under %s",
            resolved,
            root_strs,
        )
        return FileAccessDecision(
            allow=False,
            reason=f"Path '{resolved}' is outside allowed workspace roots: {root_strs}",
        )

    # --- existence check ---------------------------------------------------
    if not resolved.exists():
        # Provide helpful message with search info
        search_hint = (
            f" Searched: current directory, ~/Documents/MATLAB, and MATLAB path. "
            f"Try providing the full path."
        )
        return FileAccessDecision(
            allow=False,
            reason=f"File does not exist: {resolved}.{search_hint}",
        )

    if not resolved.is_file():
        return FileAccessDecision(
            allow=False,
            reason=f"Path is not a regular file: {resolved}",
        )

    # --- suffix whitelist --------------------------------------------------
    if resolved.suffix.lower() not in suffixes:
        allowed_str = ", ".join(sorted(suffixes))
        return FileAccessDecision(
            allow=False,
            reason=f"Suffix '{resolved.suffix}' not allowed. Accepted: {allowed_str}",
        )

    # --- size check --------------------------------------------------------
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return FileAccessDecision(
            allow=False,
            reason=f"Cannot stat file: {exc}",
        )

    if size > max_size:
        return FileAccessDecision(
            allow=False,
            reason=f"File too large ({size:,} bytes > {max_size:,} byte limit).",
        )

    # --- all checks passed -------------------------------------------------
    logger.debug("File access allowed: %s (%d bytes)", resolved, size)
    return FileAccessDecision(
        allow=True,
        reason="File access check passed.",
        resolved_path=resolved,
    )
