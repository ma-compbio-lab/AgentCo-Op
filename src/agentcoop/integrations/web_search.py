from __future__ import annotations

import html
import json
import re
import sys
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from agentcoop.ir.schema import MCPServerRef, Transport

JsonDict = Dict[str, Any]

BUILTIN_WEB_SEARCH_SERVER = "builtin-web-search"
_DEFAULT_USER_AGENT = "agentcoop-web-search/0.1"
_ANCHOR_RE = re.compile(
    r"""<a[^>]+class=["'][^"']*result__a[^"']*["'][^>]+href=["'](?P<href>[^"']+)["'][^>]*>(?P<title>.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r"""<a[^>]+class=["'][^"']*result__snippet[^"']*["'][^>]*>(?P<snippet>.*?)</a>|<div[^>]+class=["'][^"']*result__snippet[^"']*["'][^>]*>(?P<divsnippet>.*?)</div>""",
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(?P<title>.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


class WebSearchError(RuntimeError):
    """Raised when a web-search request or parse fails."""


class DuckDuckGoWebSearchClient:
    """Small no-key web-search adapter backed by the public DuckDuckGo HTML endpoint."""

    def __init__(self, timeout_s: int = 20, *, user_agent: str = _DEFAULT_USER_AGENT):
        self.timeout_s = timeout_s
        self.user_agent = user_agent

    def search(self, query: str, *, max_results: int = 5) -> JsonDict:
        normalized = query.strip()
        if not normalized:
            raise WebSearchError("query must be non-empty")
        endpoint = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(normalized)
        raw_html = self._fetch(endpoint)
        results = self._parse_search_results(raw_html, max_results=max_results)
        return {
            "query": normalized,
            "engine": "duckduckgo_html",
            "results": results,
        }

    def fetch_page(self, url: str, *, max_chars: int = 4000) -> JsonDict:
        normalized = url.strip()
        if not normalized:
            raise WebSearchError("url must be non-empty")
        raw_html = self._fetch(normalized)
        title_match = _TITLE_RE.search(raw_html)
        title = self._clean_html(title_match.group("title")) if title_match else ""
        text = self._clean_html(raw_html)
        return {
            "url": normalized,
            "title": title,
            "text_excerpt": text[:max_chars],
        }

    def _fetch(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "en-US,en;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise WebSearchError(str(exc)) from exc

    @staticmethod
    def _parse_search_results(raw_html: str, *, max_results: int) -> List[JsonDict]:
        snippets = [
            DuckDuckGoWebSearchClient._clean_html(match.group("snippet") or match.group("divsnippet") or "")
            for match in _SNIPPET_RE.finditer(raw_html)
        ]
        results: List[JsonDict] = []
        for index, match in enumerate(_ANCHOR_RE.finditer(raw_html)):
            href = DuckDuckGoWebSearchClient._normalize_result_url(match.group("href"))
            title = DuckDuckGoWebSearchClient._clean_html(match.group("title"))
            if not href or not title:
                continue
            snippet = snippets[index] if index < len(snippets) else ""
            results.append(
                {
                    "rank": len(results) + 1,
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                }
            )
            if len(results) >= max_results:
                break
        return results

    @staticmethod
    def _normalize_result_url(raw_href: str) -> str:
        href = html.unescape(raw_href)
        if href.startswith("//"):
            href = "https:" + href
        parsed = urllib.parse.urlparse(href)
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query and query["uddg"]:
            return urllib.parse.unquote(query["uddg"][0])
        return href

    @staticmethod
    def _clean_html(value: str) -> str:
        without_tags = _TAG_RE.sub(" ", value)
        normalized = " ".join(html.unescape(without_tags).split())
        return normalized.strip()


def build_builtin_web_search_server_ref() -> MCPServerRef:
    return MCPServerRef(
        name=BUILTIN_WEB_SEARCH_SERVER,
        transport=Transport.stdio,
        stdio_cmd=[sys.executable, "-m", "agentcoop.integrations.web_search_server"],
        env={
            "MCP_SERVER_NAME": BUILTIN_WEB_SEARCH_SERVER,
            "MCP_TRANSPORT": "stdio",
        },
    )


def render_search_result_json(query: str, *, max_results: int = 5) -> str:
    payload = DuckDuckGoWebSearchClient().search(query, max_results=max_results)
    return json.dumps(payload, ensure_ascii=True)
