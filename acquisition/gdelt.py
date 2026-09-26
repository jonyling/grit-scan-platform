"""Wild-scan collection from the public GDELT DOC artlist.

This module emits candidates. It does not score, and it does not read tone.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlparse

from acquisition.adapters import wild_scan
from acquisition.candidate import Candidate

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_TIMESPAN = re.compile(r"^\d+(?:min|h|d|w|m)$")
_SEEN = re.compile(r"^\d{8}T\d{6}Z$")
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class GdeltFetchError(RuntimeError):
    """GDELT did not return an article list. This is not an empty week."""


def collect_wild_scan(
    queries: str | list[str],
    *,
    fetched_at: str,
    timespan: str = "7d",
    max_records: int = 25,
    getter: Getter | None = None,
    pause_seconds: float = 0,
) -> list[Candidate]:
    """Run one GDELT query per condition. Do not join the queries into one search."""
    terms = _queries(queries)
    if pause_seconds < 0:
        raise ValueError("pause_seconds must be zero or greater")
    fetch = getter or _get_json
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, query in enumerate(terms):
        if index and pause_seconds:
            time.sleep(pause_seconds)
        for record in articles_from_payload(fetch(DOC_URL, _params(query, timespan, max_records))):
            url = record["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            records.append(record)
    return wild_scan(records, fetched_at=fetched_at)


def articles_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or "articles" not in payload:
        raise GdeltFetchError("GDELT payload had no articles list")
    articles = payload["articles"]
    if not isinstance(articles, list):
        raise GdeltFetchError("GDELT articles field was not a list")
    records: list[dict[str, Any]] = []
    for article in articles:
        record = _record(article)
        if record is not None:
            records.append(record)
    return records


def _queries(queries: str | list[str]) -> list[str]:
    raw = [queries] if isinstance(queries, str) else list(queries)
    terms: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text or text in terms:
            continue
        if len(text) > 500 or any(ord(char) < 32 for char in text):
            raise ValueError("GDELT query is empty, too long, or not a single line")
        terms.append(text)
    if not terms:
        raise ValueError("Wild scan requires one query per condition")
    return terms


def _params(query: str, timespan: str, max_records: int) -> dict[str, str]:
    if not _TIMESPAN.fullmatch(timespan):
        raise ValueError("timespan must look like 7d, 24h, or 15min")
    if not isinstance(max_records, int) or not 1 <= max_records <= 250:
        raise ValueError("max_records must be an integer from 1 to 250")
    return {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "timespan": timespan,
        "maxrecords": str(max_records),
        "sort": "datedesc",
    }


def _record(article: Any) -> dict[str, Any] | None:
    if not isinstance(article, dict):
        return None
    url = str(article.get("url") or "").strip()
    title = str(article.get("title") or "").strip()
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if not title or not url.startswith(("http://", "https://")):
        return None
    if host.endswith("gdeltproject.org"):
        return None
    domain = str(article.get("domain") or "").strip() or host
    return {
        "title": title,
        "text": "",
        "url": url,
        "publisher": domain,
        "published_at": None,
        "seen_at": _seen_at(article.get("seendate")),
    }


def _seen_at(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not _SEEN.fullmatch(text):
        return text
    try:
        parsed = datetime.strptime(text, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return text
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    import requests

    try:
        response = requests.get(
            url,
            params=params,
            timeout=30,
            headers={"User-Agent": "GRIT-Scan wild-scan", "Accept": "application/json"},
        )
    except requests.RequestException as exc:
        raise GdeltFetchError("GDELT request failed") from exc
    if response.status_code != 200:
        raise GdeltFetchError(f"GDELT returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise GdeltFetchError("GDELT did not return JSON") from exc
    if not isinstance(payload, dict):
        raise GdeltFetchError("GDELT JSON was not an object")
    return payload
