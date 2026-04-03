"""
Auto-discovering tool registry.
Tools are auto-loaded from src/matclaw/tools/{tool_name}/tool.py
Each tool.py must expose a module-level `tool` instance.
"""
from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.matclaw.tools.base import BaseTool, ToolManifest

logger = logging.getLogger(__name__)

_TOOLS_DIR = Path(__file__).parent


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, "BaseTool"] = {}

    def register(self, tool: "BaseTool") -> None:
        self._tools[tool.manifest.name] = tool
        logger.info("Tool registered: %s (%s)", tool.manifest.name, tool.manifest.runtime)

    def get(self, name: str) -> "BaseTool | None":
        return self._tools.get(name)

    def list_tools(self) -> list["ToolManifest"]:
        return [t.manifest for t in self._tools.values()]

    def to_llm_schema(self) -> list[dict]:
        """Export all tools as OpenAI-compatible function-calling schema."""
        result = []
        for tool in self._tools.values():
            m = tool.manifest
            props = {
                name: {"type": p.type, "description": p.description}
                for name, p in m.inputs.items()
            }
            required = [n for n, p in m.inputs.items() if p.required]
            result.append({
                "type": "function",
                "function": {
                    "name": m.name,
                    "description": m.description,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            })
        return result

    def discover(self) -> None:
        """
        Auto-scan tools/ subdirectories.
        Each subdirectory must have a tool.py exposing a `tool` attribute.
        """
        for tool_dir in _TOOLS_DIR.iterdir():
            if not tool_dir.is_dir() or tool_dir.name.startswith("_"):
                continue
            tool_file = tool_dir / "tool.py"
            if not tool_file.exists():
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"matclaw_tool_{tool_dir.name}", tool_file
                )
                if spec is None or spec.loader is None:
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)  # type: ignore[union-attr]
                tool_instance = getattr(module, "tool", None)
                if tool_instance is not None:
                    self.register(tool_instance)
                else:
                    logger.warning("No `tool` export in %s/tool.py", tool_dir.name)
            except Exception as exc:
                logger.warning("Failed to load tool %s: %s", tool_dir.name, exc)


# Singleton registry — populated at server startup
tool_registry = ToolRegistry()

__all__ = ["ToolRegistry", "tool_registry"]
