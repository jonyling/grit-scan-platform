"""Plan-driven Brave News collection. It sends a query plan and writes a run log. No scoring, no page fetch.

grit/ontology/query_plan.py decides which queries run. This module sends them, keeps every search with
its query id and rank, and collapses the results to one candidate per page. A stop for a limit is
recorded in the run, so a short run is never read as an empty week. grit/acquisition/brave.py is the
smaller LOB keyword pull. Both emit candidates, and neither scores.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from grit.acquisition.adapters import wild_scan
from grit.acquisition.brave import MAX_PAGE_SIZE, NEWS_URL, BraveFetchError, articles_from_payload, require_key
from grit.acquisition.candidate import Candidate
from grit.settings import ROOT

ENV_MONTHLY_LIMIT = "BRAVE_MONTHLY_LIMIT"
# The key's rate-limit headers report no monthly figure, so this guard is local. Brave's free tier is $5 of credit a month at
# $5 per 1,000 searches, so about 1,000 queries; above that, Brave refuses with HTTP 402 unless credit is prepaid.
DEFAULT_MONTHLY_LIMIT = 2000
MAX_RETRIES = 2

DEFAULT_USAGE_PATH = ROOT / "output" / "brave-usage.json"
DEFAULT_OUTPUT_DIR = ROOT / "output" / "brave-plan"
_FRESHNESS = re.compile(r"^(pd|pw|pm|py|\d{4}-\d{2}-\d{2}to\d{4}-\d{2}-\d{2})$")
_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$)", re.I)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")


class SearchQuery(Protocol):
    query_id: str
    query: str
    exact: bool


@dataclass(frozen=True)
class BraveResponse:
    status: int
    payload: dict[str, Any] | None
    headers: dict[str, str]


Getter = Callable[[str, dict[str, str]], BraveResponse]


@dataclass
class PlanRun:
    candidates: list[Candidate] = field(default_factory=list)
    hits: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    searches: list[dict[str, Any]] = field(default_factory=list)
    queries_sent: int = 0
    stopped: str | None = None


def monthly_limit_from_env() -> int:
    raw = os.environ.get(ENV_MONTHLY_LIMIT, "").strip()
    if not raw:
        return DEFAULT_MONTHLY_LIMIT
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"{ENV_MONTHLY_LIMIT} must be a positive whole number")
    return int(raw)


def canonical_url(url: str) -> str:
    """Drop the fragment, tracking parameters, and a trailing slash, so one page has one URL."""
    parts = urlparse(url.strip())
    query = urlencode([(name, value) for name, value in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(name)])
    return urlunparse((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.params, query, ""))


def collect_plan(
    queries: Iterable[SearchQuery],
    *,
    fetched_at: str,
    freshness: str | None = "pw",
    count: int = MAX_PAGE_SIZE,
    country: str | None = None,
    getter: Getter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    usage_path: Path = DEFAULT_USAGE_PATH,
    monthly_limit: int | None = None,
    on_search: Callable[[dict[str, Any]], None] | None = None,
) -> PlanRun:
    """Send each query once, in order. on_search receives every search as soon as it returns."""
    if not isinstance(count, int) or not 1 <= count <= MAX_PAGE_SIZE:
        raise ValueError(f"count must be an integer from 1 to {MAX_PAGE_SIZE}")
    if freshness is not None and not _FRESHNESS.match(freshness):
        raise ValueError("freshness must be pd, pw, pm, py, or a YYYY-MM-DDtoYYYY-MM-DD range")
    fetch = getter or _get
    if getter is None:
        require_key()
    limit = monthly_limit if monthly_limit is not None else monthly_limit_from_env()
    month = fetched_at[:7]
    used = _load_usage(usage_path, month)
    run = PlanRun()
    pause = 0.0
    for spec in queries:
        used = max(used, _load_usage(usage_path, month))  # another process may have searched since the last query
        if used >= limit:
            run.stopped = "MONTHLY_LIMIT"
            break
        if run.queries_sent:
            sleep(pause)
        sent = f'"{spec.query}"' if spec.exact else spec.query
        params = {"q": sent, "count": str(count), "search_lang": "en"}
        if freshness:
            params["freshness"] = freshness
        if country:
            params["country"] = country
        response = fetch(NEWS_URL, params)
        retries = 0
        while response.status == 429 and retries < MAX_RETRIES:
            retries += 1
            sleep(max(1.0, _first_number(response.headers.get("x-ratelimit-reset")) or 1.0))
            response = fetch(NEWS_URL, params)
        if response.status == 429:
            run.stopped = "RATE_LIMITED"
            break
        if response.status == 402:
            raise BraveFetchError("Brave returned HTTP 402: the subscription refused the query (its plan limit or payment). Check the Brave dashboard.")
        if response.status != 200 or response.payload is None:
            raise BraveFetchError(f"Brave returned HTTP {response.status}")
        used = _increment_usage(usage_path, month)
        run.queries_sent += 1
        per_second = _first_number(response.headers.get("x-ratelimit-limit"))
        pause = 1.0 / per_second if per_second else 1.0
        entry = {
            "query_id": spec.query_id,
            "query": spec.query,
            "sent_query": sent,
            "status": response.status,
            "fetched_at": fetched_at,
            "rate_limit": {
                "limit": response.headers.get("x-ratelimit-limit"),
                "remaining": response.headers.get("x-ratelimit-remaining"),
            },
            "results": _results(response.payload),
        }
        run.searches.append(entry)
        if on_search is not None:
            on_search(entry)
    run.candidates, run.hits = candidates_from_searches(run.searches, fetched_at=fetched_at)
    return run


def candidates_from_searches(searches: list[dict[str, Any]], *, fetched_at: str) -> tuple[list[Candidate], dict[str, list[dict[str, Any]]]]:
    """Collapse the searches to one candidate per page. hits keeps every query that found it, with its rank."""
    records: dict[str, dict[str, Any]] = {}
    hits: dict[str, list[dict[str, Any]]] = {}
    for entry in searches:
        for result in entry["results"]:
            url = canonical_url(result["url"])
            hits.setdefault(url, []).append({"query_id": entry["query_id"], "rank": result["rank"]})
            records.setdefault(url, {**result, "url": url, "seen_at": fetched_at})
    return wild_scan(list(records.values()), fetched_at=fetched_at), hits


_NAV_SEPARATOR = re.compile(r"\s[·|»]\s")


def without_navigation(text: str) -> str:
    """Drop page furniture that a search engine returns as a snippet: a run of headline links joined by middle dots.
    One separator can be prose. Two or more in one line is a menu or a 'Latest' list of other stories."""
    kept = [line for line in text.split("\n") if len(_NAV_SEPARATOR.findall(line)) < 2]
    return "\n".join(kept)


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reuse the News adapter's mapping for each result, then add the engine's extra snippets to the text."""
    results: list[dict[str, Any]] = []
    for rank, result in enumerate(payload.get("results") or [], start=1):
        if not isinstance(result, dict):
            continue
        records = articles_from_payload({"results": [result]})
        if not records:
            continue
        record = dict(records[0])
        extras = [_SPACES.sub(" ", html.unescape(_TAGS.sub("", extra))).strip() for extra in result.get("extra_snippets") or [] if isinstance(extra, str)]
        extras = [extra for extra in extras if without_navigation(extra)]
        record["text"] = "\n".join(dict.fromkeys(snippet for snippet in [record["text"], *extras] if snippet))
        record["rank"] = rank
        results.append(record)
    return results


def _first_number(value: str | None) -> float | None:
    match = re.match(r"\s*(\d+(?:\.\d+)?)", value or "")
    return float(match.group(1)) if match and float(match.group(1)) > 0 else None


def _load_usage(path: Path, month: str) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return int(data.get("used", 0)) if isinstance(data, dict) and data.get("month") == month else 0


def _increment_usage(path: Path, month: str) -> int:
    """Add one search to the month's count. The file is re-read under a lock, so a scan and the Co-Pilot searching at the
    same time both count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(path.suffix + ".lock"), "a+") as lock:
        try:
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)
        except ImportError:  # no flock on this platform: the count can drift under concurrent searches
            pass
        used = _load_usage(path, month) + 1
        _save_usage(path, month, used)
    return used


def _save_usage(path: Path, month: str, used: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"month": month, "used": used}) + "\n", encoding="utf-8")


def _get(url: str, params: dict[str, str]) -> BraveResponse:
    import requests

    key = require_key()
    try:
        response = requests.get(
            url,
            params=params,
            timeout=30,
            headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": "GRIT-Scan brave"},
        )
    except requests.RequestException:
        raise BraveFetchError("Brave request failed") from None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return BraveResponse(
        status=response.status_code,
        payload=payload if isinstance(payload, dict) else None,
        headers={name.lower(): value for name, value in response.headers.items()},
    )


def main(
    argv: list[str] | None = None,
    *,
    getter: Getter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    usage_path: Path = DEFAULT_USAGE_PATH,
) -> int:
    from grit.settings import load_local_env
    from grit.ontology.pack import load_ontology_pack
    from grit.ontology.query_plan import DEFAULT_MAX_QUERIES, balanced_sample, build_query_plan, select_queries

    load_local_env()
    parser = argparse.ArgumentParser(description="Send this week's query plan to Brave News and store the run under output/. Use --dry-run to send nothing.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Run date, YYYY-MM-DD. It picks the week's queries.")
    parser.add_argument("--max-queries", type=int, default=DEFAULT_MAX_QUERIES)
    parser.add_argument("--limit-queries", type=int, help="Send only N queries, dealt one per lane in turn so every lane is tried. Use it for a small trial.")
    parser.add_argument("--freshness", default="pw", help="pd, pw, pm, py, or YYYY-MM-DDtoYYYY-MM-DD.")
    parser.add_argument("--count", type=int, default=MAX_PAGE_SIZE, help="Results per query, from 1 to 20.")
    parser.add_argument("--country", default="ALL", help="Brave country code. ALL searches every country. A code such as SG is rejected with HTTP 422.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true", help="Print the selection and the monthly usage. Send nothing.")
    args = parser.parse_args(argv)

    plan = build_query_plan(load_ontology_pack(ROOT / "config", "urs"), json.loads((ROOT / "config" / "registry_aliases.json").read_text(encoding="utf-8-sig")))
    selection = select_queries(plan, args.date, args.max_queries)
    chosen = balanced_sample(selection["selected"], args.limit_queries) if args.limit_queries else selection["selected"]
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        limit = monthly_limit_from_env()
    except ValueError as exc:
        print(exc)
        return 1
    used = _load_usage(usage_path, fetched_at[:7])
    print(f"Plan {selection['planSize']}, due {selection['due']}, selected {len(selection['selected'])}, this run {len(chosen)}.")
    print(f"Local monthly usage: {used} of {limit}. The Brave key reports no monthly figure.")
    if args.dry_run:
        for item in chosen[:10]:
            print(f"  {item['lane']:16} {item['query']}")
        print("Dry run. Nothing was sent.")
        return 0
    folder = args.output_dir / args.date.isoformat()
    log = folder / "searches.jsonl"
    if log.exists():
        print(f"A run for {args.date} already exists. Delete {folder} to run it again. Each run spends queries.")
        return 1
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "selection.json").write_text(json.dumps(selection, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def write_search(entry: dict[str, Any]) -> None:
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    try:
        run = collect_plan(
            [SimpleNamespace(**item) for item in chosen],
            fetched_at=fetched_at,
            freshness=args.freshness,
            count=args.count,
            country=args.country,
            getter=getter,
            sleep=sleep,
            usage_path=usage_path,
            on_search=write_search,
        )
    except (ValueError, BraveFetchError) as exc:
        print(f"{exc}. Searches already returned are in {log}.")
        return 1
    (folder / "candidates.json").write_text(
        json.dumps(
            {
                "runDate": args.date.isoformat(),
                "fetchedAt": fetched_at,
                "freshness": args.freshness,
                "queriesSelected": len(chosen),
                "queriesSent": run.queries_sent,
                "stopped": run.stopped,
                "candidates": [candidate.as_record() for candidate in run.candidates],
                "hits": run.hits,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Queries sent: {run.queries_sent} of {len(chosen)}. Candidates: {len(run.candidates)}. Stored in {folder}.")
    if run.stopped:
        print(f"The run stopped early: {run.stopped}. This is not a complete week.")
        return 1
    return 0 if run.candidates else 2


if __name__ == "__main__":
    raise SystemExit(main())
