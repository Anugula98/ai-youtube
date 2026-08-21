"""Search provider interface for the Discovery agent.

This is the integration point discovery.py's docstring points to: without a
provider configured, DiscoveryAgent reasons from the LLM's own training-time
knowledge (via NullSearchProvider, which returns nothing and is the
default). Configuring a real provider lets the Discovery agent ground its
topic proposals in what's actually happening right now, the same way a real
newsroom's discovery process would work.

HONESTY NOTE (read before trusting this in production): NewsAPISearchProvider
below is a real, complete implementation against a real API (newsapi.org),
but it has NOT been exercised against a live API key or live network in this
environment -- this sandbox has no credentials for it and no route to
newsapi.org from the backend's network policy either. Treat it as
"written and reviewed, not verified" until you've run it once against a real
key yourself. NullSearchProvider (the default) IS fully tested -- it's used
implicitly by every existing Discovery test, since no provider is configured
in the test environment.
"""
from __future__ import annotations
from typing import List, Dict, Any, Protocol

from ..config import get_settings


class SearchProvider(Protocol):
    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        """Returns a list of {"title": str, "snippet": str, "url": str,
        "published_at": str | None} dicts, most-recent-first where the
        provider supports that ordering."""
        ...


class NullSearchProvider:
    """Default provider — no external data source. DiscoveryAgent falls
    back to reasoning from the LLM's own knowledge when this is active."""

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        return []


class NewsAPISearchProvider:
    """Real implementation against https://newsapi.org/ 's /v2/everything
    endpoint. Requires NEWSAPI_KEY in the environment. See the module
    docstring above for this implementation's honest verification status."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or get_settings().newsapi_key
        if not self.api_key:
            raise RuntimeError("NEWSAPI_KEY is not set")

    def search(self, query: str, max_results: int = 10) -> List[Dict[str, Any]]:
        import requests  # local import — only needed if this provider is actually used

        resp = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": query, "sortBy": "publishedAt", "pageSize": max_results, "language": "en"},
            headers={"X-Api-Key": self.api_key},
            timeout=10,
        )
        resp.raise_for_status()
        articles = resp.json().get("articles", [])
        return [
            {
                "title": a.get("title"),
                "snippet": a.get("description"),
                "url": a.get("url"),
                "published_at": a.get("publishedAt"),
            }
            for a in articles
        ]


def get_search_provider() -> SearchProvider:
    if get_settings().newsapi_key:
        return NewsAPISearchProvider()
    return NullSearchProvider()
