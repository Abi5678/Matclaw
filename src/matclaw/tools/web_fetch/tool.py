from typing import Any
import httpx
from bs4 import BeautifulSoup
from matclaw.tools.base import BaseTool, ToolManifest, ToolParam, ToolContext, ToolResult

class WebFetchTool(BaseTool):
    manifest = ToolManifest(
        name="web_fetch",
        description="Fetch content from a URL via HTTP GET and parse it to raw text.",
        version="0.1.0",
        runtime="python",
        inputs={
            "url": ToolParam(type="string", description="The full URL to fetch."),
        },
    )

    async def run(self, inputs: dict[str, Any], context: ToolContext) -> ToolResult:
        url = inputs.get("url")
        if not url:
            return ToolResult(success=False, error="URL is required")
            
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                
            text = resp.text
            # Basic HTML stripping if content type is html
            if "text/html" in resp.headers.get("content-type", "").lower():
                try:
                    soup = BeautifulSoup(text, "html.parser")
                    text = soup.get_text(separator="\n", strip=True)
                except ImportError:
                    pass
                
            return ToolResult(success=True, output=text[:10000])  # limit output length
        except Exception as e:
            return ToolResult(success=False, error=str(e))

tool = WebFetchTool()
