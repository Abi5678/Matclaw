"""
Base contract for MatClaw skills (RPI loop + resilience).

Every skill logic module should expose research(), plan(), and execute()
and use Pydantic for data passed to/from MATLAB.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class SkillResult(BaseModel):
    """Standard result shape for skill execution."""
    success: bool = False
    message: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None


class BaseSkill(ABC):
    """
    Optional base for skill logic. Skills can also be standalone modules
    that expose research(), plan(), execute() and accept (matlab_bridge, **kwargs).
    """

    @abstractmethod
    def research(self, matlab_bridge: Any, **kwargs: Any) -> dict[str, Any]:
        """Gather context from MATLAB (e.g. whos, license, model state)."""
        ...

    @abstractmethod
    def plan(self, research_data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        """Decide what to do (e.g. suggest clear, suggest license check)."""
        ...

    @abstractmethod
    def execute(self, matlab_bridge: Any, plan_data: dict[str, Any], **kwargs: Any) -> SkillResult:
        """Perform the planned action via the bridge."""
        ...
