"""
MatClaw skills: pluggable capabilities (Workspace Auditor, Live Documenter, PID Optimizer, etc.).

Each skill lives in a subdirectory:
  skills/[skill_name]/
    SKILL.md    - Instruction manual for the agent (when to use, I/O, constraints).
    logic.py    - Python bridge: research(), plan(), execute(); uses MatlabBridge + Pydantic.
    templates/  - Pre-defined .m / .mlx scripts (boilerplate).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent


def list_skills() -> list[str]:
    """Return names of installed skills (subdirs containing SKILL.md)."""
    names = []
    for child in _SKILLS_DIR.iterdir():
        if child.is_dir() and not child.name.startswith("_"):
            if (child / "SKILL.md").is_file():
                names.append(child.name)
    return sorted(names)


def get_skill_instructions(skill_name: str) -> str | None:
    """Return the contents of SKILL.md for the given skill, or None if not found."""
    path = _SKILLS_DIR / skill_name / "SKILL.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def get_skill_path(skill_name: str) -> Path | None:
    """Return the root path of the skill directory, or None."""
    path = _SKILLS_DIR / skill_name
    return path if path.is_dir() and (path / "SKILL.md").is_file() else None


def load_skill_logic(skill_name: str) -> Any | None:
    """
    Import and return the skill's logic module (logic.py) run() or the module itself.
    Returns None if the skill has no logic.py or import fails.
    """
    path = _SKILLS_DIR / skill_name / "logic.py"
    if not path.is_file():
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(f"matclaw.skills.{skill_name}.logic", path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        logger.exception("Failed to load skill logic: %s", skill_name)
        return None


__all__ = ["list_skills", "get_skill_instructions", "get_skill_path", "load_skill_logic"]
