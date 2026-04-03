"""File operations tool — read, write, list files."""
from __future__ import annotations

from pathlib import Path

from src.matclaw.tools.base import BaseTool, ToolManifest, ToolParam, ToolContext, ToolResult


class FileOpsTool(BaseTool):
    manifest = ToolManifest(
        name="file_ops",
        description="Read, write, or list files on the local filesystem.",
        runtime="python",
        inputs={
            "action": ToolParam(
                type="string",
                description="Action: 'read' | 'write' | 'list'",
            ),
            "path": ToolParam(type="string", description="File or directory path"),
            "content": ToolParam(
                type="string", description="Content to write (for 'write' action)",
                required=False, default="",
            ),
        },
        outputs={
            "result": ToolParam(type="string", description="File content or directory listing"),
        },
        tags=["filesystem", "files"],
    )

    async def run(self, inputs: dict, context: ToolContext) -> ToolResult:
        action = inputs.get("action", "read")
        path_str = inputs.get("path", "")
        if not path_str:
            return ToolResult(success=False, error="path is required")

        p = Path(path_str)
        try:
            if action == "read":
                if not p.exists():
                    return ToolResult(success=False, error=f"File not found: {path_str}")
                content = p.read_text(encoding="utf-8", errors="replace")
                return ToolResult(success=True, output=content[:8000])

            elif action == "write":
                content = inputs.get("content", "")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
                return ToolResult(success=True, output=f"Written {len(content)} bytes to {path_str}")

            elif action == "list":
                if not p.exists() or not p.is_dir():
                    return ToolResult(success=False, error=f"Directory not found: {path_str}")
                entries = [
                    f"{'[dir] ' if e.is_dir() else '      '}{e.name}"
                    for e in sorted(p.iterdir())
                ]
                return ToolResult(success=True, output="\n".join(entries))

            else:
                return ToolResult(success=False, error=f"Unknown action: {action}")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


tool = FileOpsTool()
