"""WebFetchTool + WebSearchTool — fetch URL content / search the web.

We don't ship a search provider out of the box (would need a key per
backend). The fetch tool uses httpx directly. For search, if no provider
is configured, the tool returns a helpful message rather than crashing.
"""

from __future__ import annotations

from typing import Optional

import httpx

from kairos.tools.base import BaseTool, ToolResult

class WebFetchTool(BaseTool):
    name = "webfetch"
    description = "Fetch the contents of a URL and return plain text"

    def __init__(self, allowed_root: "str | Path" = ".", timeout: float = 20.0):
        super().__init__(allowed_root=allowed_root)
        self._timeout = timeout

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "description": "HTTP or HTTPS URL to fetch"},
                    "max_chars": {"type": "integer", "default": 20000,
                                  "description": "Truncate response body to this many chars."},
                },
                "required": ["url"],
            },
        }

    async def execute(self, url: str = "", max_chars: int = 20000, **kwargs) -> ToolResult:
        if not url:
            return ToolResult(success=False, output="", error="url is required")
        if not (url.startswith("http://") or url.startswith("https://")):
            return ToolResult(success=False, output="", error="url must be http(s)")
        try:
            async with httpx.AsyncClient(timeout=self._timeout,
                                          follow_redirects=True) as client:
                resp = await client.get(url)
            text = resp.text
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n... (truncated, {len(resp.text)} total)"
            return ToolResult(
                success=resp.status_code < 400,
                output=text,
                error=None if resp.status_code < 400 else f"HTTP {resp.status_code}",
                metadata={"status": resp.status_code, "url": str(resp.url)},
            )
        except httpx.TimeoutException:
            return ToolResult(success=False, output="", error=f"fetch timed out after {self._timeout}s")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

class WebSearchTool(BaseTool):
    """Web search. We intentionally don't ship a default backend — users
    pick one (Bing/Google/Brave/SerpAPI) and configure. Without a configured
    backend, the tool returns a helpful message.

    Configuration: set environment variable KAIROS_SEARCH_API_URL to a
    SerpAPI-compatible endpoint, plus KAIROS_SEARCH_API_KEY.
    """

    name = "websearch"
    description = "Search the web (requires KAIROS_SEARCH_API_URL/KEY env vars)"

    def __init__(self, allowed_root: "str | Path" = "."):
        super().__init__(allowed_root=allowed_root)
        import os
        self._api_url = os.getenv("KAIROS_SEARCH_API_URL")
        self._api_key = os.getenv("KAIROS_SEARCH_API_KEY")

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "default": 8},
                },
                "required": ["query"],
            },
        }

    async def execute(self, query: str = "", max_results: int = 8, **kwargs) -> ToolResult:
        if not query:
            return ToolResult(success=False, output="", error="query is required")
        if not self._api_url or not self._api_key:
            return ToolResult(
                success=False, output="",
                error=("web search not configured. Set KAIROS_SEARCH_API_URL and "
                       "KAIROS_SEARCH_API_KEY in the environment (SerpAPI-compatible)."),
            )
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                resp = await client.get(
                    self._api_url,
                    params={"q": query, "api_key": self._api_key,
                            "num": max_results},
                )
            data = resp.json()
            results = data.get("results") or data.get("organic_results") or []
            lines = [f"{i+1}. {r.get('title', '')} — {r.get('link', '')}"
                     for i, r in enumerate(results[:max_results])]
            return ToolResult(success=True, output="\n".join(lines) or "(no results)",
                              metadata={"count": len(results)})
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))