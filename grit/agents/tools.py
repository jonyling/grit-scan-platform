"""Tools an agent may call. Results come from supplied registers, never from the model."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class AgentTools:
    def __init__(
        self,
        *,
        known_entity_ids: frozenset[str] | set[str],
        search_results: list[dict[str, str]] | None = None,
        url_checks: dict[str, bool] | None = None,
    ) -> None:
        self.known_entity_ids = set(known_entity_ids)
        self.search_results = list(search_results or [])
        self.url_checks = dict(url_checks or {})
        self.calls: list[str] = []

    def search(self, query: str) -> dict[str, Any]:
        self.calls.append("search")
        if not self.search_results:
            return {"check": "search", "status": "NOT_VERIFIABLE", "query": query, "results": []}
        return {"check": "search", "status": "RESULTS", "query": query, "results": self.search_results}

    def check_entity(self, entity_id: str) -> dict[str, Any]:
        self.calls.append("check_entity")
        if entity_id in self.known_entity_ids:
            return {"check": "entity", "status": "FOUND", "entity_id": entity_id}
        return {"check": "entity", "status": "NOT_VERIFIABLE", "entity_id": entity_id, "reason": "NOT_IN_REGISTER"}

    def check_url(self, url: str) -> dict[str, Any]:
        self.calls.append("check_url")
        supplied = self.url_checks.get(url)
        if supplied is not True:
            return {"check": "url", "status": "NOT_VERIFIABLE", "url": url, "reason": "NO_PASSED_URL_CHECK"}
        return {"check": "url", "status": "CHECKED", "url": url}

    def check_corroboration(self, references: list[str]) -> dict[str, Any]:
        self.calls.append("check_corroboration")
        outlets = sorted({_outlet(url) for url in references if _outlet(url)})
        if len(outlets) < 2:
            return {"check": "corroboration", "status": "NOT_VERIFIABLE", "outlet_count": len(outlets), "outlets": outlets}
        return {"check": "corroboration", "status": "CORROBORATED", "outlet_count": len(outlets), "outlets": outlets}


def _outlet(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) < 2:
        return ""
    return ".".join(parts[-2:])
