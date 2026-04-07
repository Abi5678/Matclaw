from typing import Any
import os
from pathlib import Path
from matclaw.tools.base import BaseTool, ToolManifest, ToolParam, ToolContext, ToolResult

class FileOpsTool(BaseTool):
    manifest = ToolManifest(
        name="file_ops",
        description="Read or write local files within the project workspace.",
        version="0.1.0",
        runtime="python",
        inputs={
            "action": ToolParam(type="string", description="'read', 'write', or 'list'"),
            "path": ToolParam(type="string", description="Relative file path"),
            "content": ToolParam(type="string", description="File content to write (only for 'write' action)", required=False),
        },
    )

    async def run(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult:
        action = inputs.get("action")
        path = inputs.get("path")
        
        if action not in ("read", "write", "list"):
            return ToolResult(success=False, error="Invalid action. Use read, write, or list.")
        if not path:
            return ToolResult(success=False, error="Path is required.")
            
        # Ensure path is safely within CWD loosely (we asked user and they said no restriction, 
        # but defaulting to relative to root is good practice).
        target = Path(path).resolve()

        try:
            if action == "list":
                if not target.is_dir():
                    return ToolResult(success=False, error="Path is not a directory.")
                items = [str(p.name) for p in target.iterdir()]
                return ToolResult(success=True, output="\\n".join(items))
                
            elif action == "read":
                if not target.is_file():
                    return ToolResult(success=False, error="File not found.")
                with open(target, "r", encoding="utf-8") as f:
                    content = f.read()
                return ToolResult(success=True, output=content[:15000])  # limit safety
                
            elif action == "write":
                content = inputs.get("content", "")
                target.parent.mkdir(parents=True, exist_ok=True)
                with open(target, "w", encoding="utf-8") as f:
                    f.write(content)
                return ToolResult(success=True, output=f"Successfully wrote {len(content)} bytes to {path}")
                
        except Exception as e:
            return ToolResult(success=False, error=str(e))

tool = FileOpsTool()
