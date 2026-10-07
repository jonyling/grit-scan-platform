"""Headline collection from the Brave News Search API.

The query is built from governed ontology keywords: the active LOBs in a named
pack and their keyword lists. It is never a customer name, a placeholder, or a
site: filter. A keyword only widens what is pulled. It is not an STE link, and
every row still goes through the bind unchanged.

This module emits candidates. It does not score, and it does not read a rank.
"""

from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlparse

from grit.acquisition.adapters import wild_scan
from grit.acquisition.candidate import Candidate

NEWS_URL = "https://api.search.brave.com/res/v1/news/search"
ENV_KEY = "BRAVE_SEARCH_API_KEY"
MAX_PULLS = 6
MAX_PAGE_SIZE = 20
MAX_QUERY_CHARS = 400
MAX_QUERY_WORDS = 50
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
Getter = Callable[[str, dict[str, str]], dict[str, Any]]


class BraveFetchError(RuntimeError):
    """Brave did not return a result list. This is not an empty week."""


@dataclass(frozen=True)
class PlannedQuery:
    lob_id: str
    lob_name: str
    query: str
    days_to_scan: int


def plan_queries(lob_profiles: dict[str, Any], lob_ids: list[str] | None = None) -> list[PlannedQuery]:
    """One or more Boolean keyword queries per active LOB, from the register only.

    Inactive LOBs never match, the same gate as the LOB matcher. A keyword list
    that does not fit one query is split, never truncated.
    """
    wanted = set(lob_ids or [])
    lobs = [lob for lob in list(lob_profiles.get("lobs") or []) if lob.get("active") is True]
    unknown = wanted - {str(lob.get("lobId")) for lob in lobs}
    if unknown:
        raise ValueError(f"No active LOB in this pack with id: {', '.join(sorted(unknown))}")
    planned: list[PlannedQuery] = []
    for lob in lobs:
        if wanted and lob.get("lobId") not in wanted:
            continue
        days = lob.get("daysToScan")
        for query in _boolean_queries(list(lob.get("keywords") or [])):
            planned.append(
                PlannedQuery(
                    lob_id=str(lob["lobId"]),
                    lob_name=str(lob.get("lobName") or ""),
                    query=query,
                    days_to_scan=days if isinstance(days, int) and days > 0 else 7,
                )
            )
    return planned


def freshness_for(days: int) -> str:
    """Brave freshness code that covers the scan window."""
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    return "py"


def collect_brave(
    queries: str | list[str],
    *,
    fetched_at: str,
    max_records: int = 10,
    freshness: str = "pw",
    getter: Getter | None = None,
) -> list[Candidate]:
    """One Brave news search per query. Do not join the queries."""
    terms = _queries(queries)
    if len(terms) > MAX_PULLS:
        raise ValueError(f"Brave allows at most {MAX_PULLS} pulls per run. Name an LOB to narrow the plan.")
    if not isinstance(max_records, int) or not 1 <= max_records <= MAX_PAGE_SIZE:
        raise ValueError("max_records must be an integer from 1 to 20")
    if freshness not in {"pd", "pw", "pm", "py"}:
        raise ValueError("freshness must be pd, pw, pm, or py")
    fetch = getter or _get_json
    if getter is None:
        require_key()
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for query in terms:
        for record in articles_from_payload(fetch(NEWS_URL, _params(query, max_records, freshness))):
            if record["url"] in seen_urls:
                continue
            seen_urls.add(record["url"])
            records.append(record)
    return wild_scan(records, fetched_at=fetched_at)


def require_key() -> str:
    value = os.environ.get(ENV_KEY)
    if value is None or not value.strip():
        raise BraveFetchError(f"Missing environment variable: {ENV_KEY}")
    return value.strip()


def articles_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or "results" not in payload:
        raise BraveFetchError("Brave payload had no results list")
    results = payload["results"]
    if not isinstance(results, list):
        raise BraveFetchError("Brave results field was not a list")
    records: list[dict[str, Any]] = []
    for result in results:
        record = _record(result)
        if record is not None:
            records.append(record)
    return records


def _boolean_queries(keywords: list[Any]) -> list[str]:
    phrases: list[str] = []
    for item in keywords:
        text = _SPACES.sub(" ", str(item or "").replace('"', " ")).strip()
        if text and text.lower() not in {phrase.strip('"').lower() for phrase in phrases}:
            phrases.append(f'"{text}"')
    queries: list[str] = []
    current: list[str] = []
    for phrase in phrases:
        trial = " OR ".join([*current, phrase])
        if current and (len(trial) > MAX_QUERY_CHARS or len(trial.split()) > MAX_QUERY_WORDS):
            queries.append(" OR ".join(current))
            current = []
        current.append(phrase)
    if current:
        queries.append(" OR ".join(current))
    for query in queries:
        if len(query) > MAX_QUERY_CHARS or len(query.split()) > MAX_QUERY_WORDS:
            raise ValueError("A single LOB keyword is too long for a Brave query")
    return queries


def _queries(queries: str | list[str]) -> list[str]:
    raw = [queries] if isinstance(queries, str) else list(queries)
    terms: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text or text in terms:
            continue
        if (
            len(text) > MAX_QUERY_CHARS
            or len(text.split()) > MAX_QUERY_WORDS
            or any(ord(char) < 32 for char in text)
        ):
            raise ValueError("Brave query is empty, over 400 characters or 50 words, or not a single line")
        terms.append(text)
    if not terms:
        raise ValueError("Brave requires at least one keyword query")
    return terms


def _params(query: str, max_records: int, freshness: str) -> dict[str, str]:
    return {
        "q": query,
        "count": str(max_records),
        "search_lang": "en",
        "freshness": freshness,
    }


def _record(result: Any) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    url = str(result.get("url") or "").strip()
    title = _plain(result.get("title"))
    if not title or not url.startswith(("http://", "https://")):
        return None
    meta = result.get("meta_url") if isinstance(result.get("meta_url"), dict) else {}
    host = str(meta.get("hostname") or urlparse(url).netloc).lower().removeprefix("www.")
    if host.endswith("brave.com"):
        return None
    return {
        "title": title,
        "text": _plain(result.get("description")),
        "url": url,
        "publisher": host or None,
        "published_at": _published(result.get("page_age")),
    }


def _plain(value: Any) -> str:
    return _SPACES.sub(" ", html.unescape(_TAGS.sub("", str(value or "")))).strip()


def _published(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_json(url: str, params: dict[str, str]) -> dict[str, Any]:
    import requests

    key = require_key()
    try:
        response = requests.get(
            url,
            params=params,
            timeout=30,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": key,
                "User-Agent": "GRIT-Scan brave",
            },
        )
    except requests.RequestException as exc:
        raise BraveFetchError("Brave request failed") from exc
    if response.status_code != 200:
        retry = response.headers.get("Retry-After", "")
        extra = f" retry_after={retry}" if re.fullmatch(r"\d+", retry) else ""
        raise BraveFetchError(f"Brave returned HTTP {response.status_code}{extra}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise BraveFetchError("Brave did not return JSON") from exc
    if not isinstance(payload, dict):
        raise BraveFetchError("Brave JSON was not an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from grit.settings import CONFIG_DIR, load_local_env
    from grit.ontology.pack import PACKS, load_ontology_pack

    load_local_env()
    parser = argparse.ArgumentParser(description="Print Brave headline candidates for a pack's active LOB keywords. Nothing is stored.")
    parser.add_argument("--pack", required=True, choices=sorted(PACKS), help="Ontology pack. No default.")
    parser.add_argument("--lob", action="append", help="LOB id to scan. Repeat for more. Default: every active LOB in the pack.")
    parser.add_argument("--max-records", type=int, default=10, help="Results per query, from 1 to 20.")
    parser.add_argument("--plan-only", action="store_true", help="Print the queries and make no call.")
    args = parser.parse_args(argv)
    config = CONFIG_DIR
    plan = plan_queries(load_ontology_pack(config, args.pack).lob_profiles, args.lob)
    if not plan:
        print(f"No active LOB keywords in pack {args.pack}.")
        return 2
    for item in plan:
        print(f"[{item.lob_id}] {item.query}")
    if args.plan_only:
        return 0
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        candidates = collect_brave(
            [item.query for item in plan],
            fetched_at=fetched_at,
            max_records=args.max_records,
            freshness=freshness_for(max(item.days_to_scan for item in plan)),
        )
    except (ValueError, BraveFetchError) as exc:
        print(exc)
        return 1
    print(f"Brave candidates: {len(candidates)}")
    for candidate in candidates:
        print(f"  {candidate.publisher or 'unknown'} | {candidate.title} | {candidate.url}")
    return 0 if candidates else 2


if __name__ == "__main__":
    raise SystemExit(main())
