"""Headline collection from Currents. This module does not call GDELT.

It emits candidates. It does not score, and it does not read a tone or a rank.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from acquisition.adapters import wild_scan
from acquisition.candidate import Candidate

SEARCH_URL = "https://api.currentsapi.services/v1/search"
ENV_KEY = "CURRENTS_API_KEY"
MAX_PULLS = 2
MAX_PAGE_SIZE = 20
_PUBLISHED = (
    "%Y-%m-%d %H:%M:%S %z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
)
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class CurrentsFetchError(RuntimeError):
    """Currents did not return an article list. This is not an empty week."""


def collect_currents(
    queries: str | list[str],
    *,
    fetched_at: str,
    max_records: int = 10,
    getter: Getter | None = None,
) -> list[Candidate]:
    """One Currents search per condition, at most two. Do not join the queries."""
    terms = _queries(queries)
    if len(terms) > MAX_PULLS:
        raise ValueError("Currents allows one or two pulls per run, not a firehose")
    if not isinstance(max_records, int) or not 1 <= max_records <= MAX_PAGE_SIZE:
        raise ValueError("max_records must be an integer from 1 to 20")
    fetch = getter or _get_json
    if getter is None:
        require_key()
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for query in terms:
        for record in articles_from_payload(fetch(SEARCH_URL, _params(query, max_records))):
            if record["url"] in seen_urls:
                continue
            seen_urls.add(record["url"])
            records.append(record)
    return wild_scan(records, fetched_at=fetched_at)


def require_key() -> str:
    value = os.environ.get(ENV_KEY)
    if value is None or not value.strip():
        raise CurrentsFetchError("Missing environment variable: CURRENTS_API_KEY")
    return value.strip()


def articles_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("status") != "ok" or "news" not in payload:
        raise CurrentsFetchError("Currents payload had no news list")
    articles = payload["news"]
    if not isinstance(articles, list):
        raise CurrentsFetchError("Currents news field was not a list")
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
            raise ValueError("Currents query is empty, too long, or not a single line")
        terms.append(text)
    if not terms:
        raise ValueError("Currents requires one query per condition")
    return terms


def _params(query: str, max_records: int) -> dict[str, str]:
    return {
        "keywords": query,
        "language": "en",
        "page_number": "1",
        "page_size": str(max_records),
        "limit": str(max_records),
    }


def _record(article: Any) -> dict[str, Any] | None:
    if not isinstance(article, dict):
        return None
    url = str(article.get("url") or "").strip()
    title = str(article.get("title") or "").strip()
    host = urlparse(url).netloc.lower().removeprefix("www.")
    if not title or not url.startswith(("http://", "https://")):
        return None
    if host.endswith("currentsapi.services"):
        return None
    description = str(article.get("description") or "").strip()
    if description.lower() == "none":
        description = ""
    return {
        "title": title,
        "text": description,
        "url": url,
        "publisher": host,
        "published_at": _published(article.get("published")),
    }


def _published(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return None
    for fmt in _PUBLISHED:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    import requests

    key = require_key()
    try:
        response = requests.get(
            url,
            params=params,
            timeout=30,
            headers={
                "Authorization": key,
                "Accept": "application/json",
                "User-Agent": "GRIT-Scan currents",
            },
        )
    except requests.RequestException as exc:
        raise CurrentsFetchError("Currents request failed") from exc
    if response.status_code != 200:
        retry = response.headers.get("Retry-After", "")
        extra = f" retry_after={retry}" if re.fullmatch(r"\d+", retry) else ""
        raise CurrentsFetchError(f"Currents returned HTTP {response.status_code}{extra}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise CurrentsFetchError("Currents did not return JSON") from exc
    if not isinstance(payload, dict):
        raise CurrentsFetchError("Currents JSON was not an object")
    return payload
