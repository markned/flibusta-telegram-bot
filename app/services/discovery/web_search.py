from __future__ import annotations
import httpx
from app.services.discovery.types import WebSearchResult

class DiscoveryWebError(RuntimeError):
    pass

class WebSearchProvider:
    async def search(self, query: str, limit: int) -> list[WebSearchResult]:
        raise NotImplementedError

class DisabledWebSearchProvider(WebSearchProvider):
    async def search(self, query: str, limit: int) -> list[WebSearchResult]:
        return []

class TavilyWebSearchProvider(WebSearchProvider):
    def __init__(self, api_key: str, *, timeout_seconds: float = 15, max_snippet_chars: int = 500):
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.max_snippet_chars = max_snippet_chars

    async def search(self, query: str, limit: int) -> list[WebSearchResult]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.timeout_seconds, connect=5.0)) as client:
                response = await client.post(
                    'https://api.tavily.com/search',
                    json={'api_key': self.api_key, 'query': query, 'max_results': limit, 'search_depth': 'basic'},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise DiscoveryWebError('web discovery unavailable') from exc
        results=[]
        for item in payload.get('results', [])[:limit]:
            results.append(WebSearchResult(
                title=str(item.get('title') or ''),
                url=str(item.get('url') or ''),
                snippet=str(item.get('content') or '')[:self.max_snippet_chars],
                source='tavily',
            ))
        return results
