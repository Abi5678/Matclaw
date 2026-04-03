"""
Lightweight tool interface — simpler than Skills, no RPI loop required.
Any tool = BaseTool subclass with a manifest + async run() method.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


class ToolParam(BaseModel):
    type: str                   # "string" | "integer" | "boolean" | "object" | "array"
    description: str
    required: bool = True
    default: Any = None


class ToolManifest(BaseModel):
    name: str
    description: str
    version: str = "0.1.0"
    runtime: str = "python"     # "python" | "matlab" | "shell" | "http"
    inputs: dict[str, ToolParam] = {}
    outputs: dict[str, ToolParam] = {}
    tags: list[str] = []


@dataclass
class ToolContext:
    tenant_id: str = "default"
    request_id: str = ""
    user_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    success: bool
    output: Any = None
    error: str = ""
    artifacts: list[str] = field(default_factory=list)


class BaseTool(ABC):
    manifest: ToolManifest

    @abstractmethod
    async def run(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult:
        ...


__all__ = ["ToolParam", "ToolManifest", "ToolContext", "ToolResult", "BaseTool"]
