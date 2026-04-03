"""Web fetch tool — HTTP GET/POST for any URL."""
from __future__ import annotations

import httpx

from src.matclaw.tools.base import BaseTool, ToolManifest, ToolParam, ToolContext, ToolResult


class WebFetchTool(BaseTool):
    manifest = ToolManifest(
        name="web_fetch",
        description="Fetch content from a URL via HTTP GET. Returns the response body (truncated to 4000 chars).",
        runtime="python",
        inputs={
            "url": ToolParam(type="string", description="URL to fetch"),
            "timeout": ToolParam(
                type="integer", description="Request timeout in seconds",
                required=False, default=10,
            ),
        },
        outputs={
            "content": ToolParam(type="string", description="Response body text"),
            "status_code": ToolParam(type="integer", description="HTTP status code"),
        },
        tags=["web", "http", "fetch"],
    )

    async def run(self, inputs: dict, context: ToolContext) -> ToolResult:
        url = inputs.get("url", "")
        timeout = int(inputs.get("timeout", 10))
        if not url:
            return ToolResult(success=False, error="url is required")
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(url, timeout=timeout, follow_redirects=True)
                return ToolResult(
                    success=True,
                    output={"content": r.text[:4000], "status_code": r.status_code},
                )
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


tool = WebFetchTool()
