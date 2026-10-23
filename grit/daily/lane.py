"""The daily lane (spec 42): a rolling daily run per pack, fed by the feed register and one Currents query per standing
condition and active management request. No model, apart from one embedding per new page.

For each pack and day:
1. Start the run DATE-PACK-daily (kind DAILY). Weekly and monthly code ignores DAILY runs.
2. Read each active feed for the pack, add the week's source-sweep results for its stale and blocked sources, and send the
   pack's daily Currents queries. Every item becomes a signal and a sighting in the run, as a scanned page does.
3. Embed new pages, then run the same deterministic assessment as a weekly scan: link ladder, keep rule, tier, conditions,
   harm and RED.
4. Queue every kept page for the pack's next weekly or monthly scan, whose rules assess it again for the brief.
5. Draft RED alerts for a named person to send, as the weekly report does.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from grit.daily.feeds import FEEDS, Feed, active, read_feed, swept
from grit.pool import Pool, utc_now

LOOKBACK_DAYS = 2  # a missed day is caught up the next day
CURRENTS_DAILY_MAX = 20
SWEEP_DAYS = int(os.environ.get("SWEEP_DAYS", "28") or 28)  # each stale or blocked source is swept once in this many days
SWEEP_RESULTS = 20


def daily_run_id(day: date, pack: str) -> str:
    return f"{day.isoformat()}-{pack}-daily"


def daily_packs(packs: Sequence[str] | None = None) -> list[str]:
    """The packs with a daily run: those an active feed serves, plus the Group pack's Currents queries."""
    found = sorted({pack for feed in active() for pack in feed.packs} | {"ste"})
    return [pack for pack in found if not packs or pack in packs]


def due_sweeps(pool: Pool, day: date, feeds: Sequence[Feed] | None = None) -> list[Feed]:
    """Stale and blocked sources not swept in the last SWEEP_DAYS. A failed sweep is tried again the next day."""
    due = []
    for feed in feeds if feeds is not None else swept():
        row = pool.one("SELECT MAX(day) AS last FROM source_sweeps WHERE feed_id = ? AND status != 'FAILED'", (feed.feed_id,))
        last = (row or {}).get("last")
        if last is None or (day - date.fromisoformat(last)).days >= SWEEP_DAYS:
            due.append(feed)
    return due


def sweep_sources(pool: Pool, day: date, *, feeds: Sequence[Feed] | None = None, getter: Callable[..., Any] | None = None, usage_path: Any = None,
                  log: Callable[[str], None] = print) -> dict[str, list[dict[str, Any]]]:
    """The weekly source sweep: one Brave News query restricted to each due source's site, for the last week. Returns
    feed id -> pages from that site. Skipped without a Brave key, and when Brave is within 10% of its monthly limit."""
    from types import SimpleNamespace
    from urllib.parse import urlparse

    from grit.acquisition.brave import BraveFetchError
    from grit.acquisition.brave_plan import canonical_url, collect_plan
    from grit.copilot.web import brave_near_limit

    due = due_sweeps(pool, day, feeds)
    if not due:
        return {}
    if getter is None and not os.environ.get("BRAVE_SEARCH_API_KEY", "").strip():
        log("  Source sweep skipped: BRAVE_SEARCH_API_KEY is not set.")
        return {}
    if getter is None and brave_near_limit(usage_path):
        log("  Source sweep skipped: Brave is within 10% of its monthly limit.")
        return {}
    answered: dict[str, list[dict[str, Any]]] = {}
    specs = [SimpleNamespace(query_id=f"sweep-{feed.feed_id}", query=feed.sweep, exact=False) for feed in due]
    try:
        collect_plan(specs, fetched_at=utc_now(), freshness="pw", count=SWEEP_RESULTS, getter=getter,
                     on_search=lambda entry: answered.__setitem__(entry["query_id"], entry["results"]), **({"usage_path": usage_path} if usage_path else {}))
    except BraveFetchError as exc:
        log(f"  Source sweep stopped: {exc}")
    out: dict[str, list[dict[str, Any]]] = {}
    for feed in due:
        results = answered.get(f"sweep-{feed.feed_id}")
        site = feed.sweep.split()[0].removeprefix("site:")
        pages = [
            {"url": canonical_url(item["url"]), "title": item["title"], "text": item.get("text") or "", "publisher": item.get("publisher"), "published_at": item.get("published_at")}
            for item in results or [] if item.get("url") and item.get("title") and (urlparse(item["url"]).netloc.lower() == site or urlparse(item["url"]).netloc.lower().endswith(f".{site}"))
        ]
        status = "FAILED" if results is None else "OK" if pages else "EMPTY"
        pool.upsert("source_sweeps", {"feed_id": feed.feed_id, "day": day.isoformat(), "query": feed.sweep, "results": len(pages), "status": status}, ["feed_id", "day"])
        if pages:
            out[feed.feed_id] = pages
        log(f"  sweep {feed.feed_id:18} {len(pages):>3} pages" + ("  (not answered; tried again tomorrow)" if status == "FAILED" else ""))
    return out


def currents_specs(pool: Pool, run: Any) -> list[Any]:
    """One query per standing condition and per active management request, from the pack's own scan plan."""
    from grit.pipeline.reader_scan import plan, target_of

    chosen, seen = [], set()
    for spec in plan(pool, run):
        if spec.lane not in {"CONDITION", "REQUEST"}:
            continue
        target = target_of(spec) or spec.query_id
        if (spec.lane, target) in seen:
            continue
        seen.add((spec.lane, target))
        chosen.append(spec)
    return chosen


@dataclass
class DailyResult:
    pack: str
    run_id: str
    feeds: dict[str, Any] = field(default_factory=dict)
    currents: dict[str, Any] = field(default_factory=dict)
    signals: int = 0
    new_signals: int = 0
    kept: int = 0
    queued: int = 0
    red_drafts: int = 0

    def as_record(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _add(pool: Pool, run_id: str, item: dict[str, Any], *, lane: str, query_id: str, query: str, sighting_lane: str, target_id: str | None, rank: int | None, result: DailyResult) -> None:
    before = pool.one("SELECT 1 FROM signals WHERE url = ?", (item["url"],))
    content = hashlib.sha256(f"{item['title']}\n{item.get('text') or ''}".encode("utf-8")).hexdigest()
    signal_id = pool.add_signal(url=item["url"], title=item["title"], text=item.get("text") or "", publisher=item.get("publisher"), published_at=item.get("published_at"), lane=lane, content_hash=content)
    pool.add_sighting(signal_id, run_id, query_id=query_id, query=query, lane=sighting_lane, target_id=target_id, rank=rank)
    result.signals += 1
    result.new_signals += 0 if before else 1


def run_daily(
    pool: Pool, pack: str, day: date, *, feeds: Sequence[Feed] | None = None, feed_getter: Callable[[str], bytes] | None = None,
    currents_getter: Callable[..., Any] | None = None, use_currents: bool = True, lookback_days: int = LOOKBACK_DAYS, embedder: Any | None = None,
    score_harm_fn: Callable[..., dict[str, Any]] | None = None, profiles: Sequence[Any] | None = None, log: Callable[[str], None] = print,
    swept_pages: Mapping[str, Sequence[dict[str, Any]]] | None = None,
) -> DailyResult:
    """swept_pages: the source sweep's results, feed id -> pages, shared by every pack the source serves."""
    from grit.pipeline import reader_scan
    from grit.pipeline.reader_scan import ReaderRun, target_of

    run = ReaderRun(daily_run_id(day, pack), day, pack)
    if pool.run(run.run_id) is None:
        pool.start_run(run.run_id, run_date=day.isoformat(), pack=pack, kind="DAILY", config={"lane": "daily"})
    run.folder.mkdir(parents=True, exist_ok=True)
    result = DailyResult(pack, run.run_id)
    since = datetime.combine(day - timedelta(days=lookback_days), time.min, tzinfo=timezone.utc)
    for feed in feeds if feeds is not None else active([pack]):
        if pack not in feed.packs:
            continue
        try:
            items = read_feed(feed, since=since, getter=feed_getter)
        except Exception as exc:  # a feed that fails today is reported, and the others still run
            result.feeds[feed.feed_id] = f"failed: {str(exc)[:80] or type(exc).__name__}"
            log(f"  {pack:7} feed {feed.feed_id:18} failed: {exc}")
            continue
        for rank, item in enumerate(items, start=1):
            _add(pool, run.run_id, item, lane="FEED", query_id=f"feed-{feed.feed_id}", query=feed.name, sighting_lane="FEED", target_id=None, rank=rank, result=result)
        result.feeds[feed.feed_id] = len(items)
        log(f"  {pack:7} feed {feed.feed_id:18} {len(items):>3} items")
    have_currents = currents_getter is not None or bool(os.environ.get("CURRENTS_API_KEY", "").strip())
    if use_currents and have_currents:
        from grit.acquisition.currents import CurrentsFetchError, collect_currents

        answered = {row["query_id"] for row in pool.rows("SELECT DISTINCT query_id FROM sightings WHERE run_id = ?", (run.run_id,))}
        for feed in [item for item in FEEDS if item.currents and pack in item.packs and f"source-{item.feed_id}" not in answered]:
            try:
                candidates = collect_currents(feed.currents, fetched_at=utc_now(), max_records=20, getter=currents_getter)
            except (CurrentsFetchError, ValueError) as exc:
                log(f"  {pack:7} source {feed.feed_id:16} Currents failed: {exc}")
                continue
            for rank, candidate in enumerate(candidates, start=1):
                if candidate.url and candidate.title:
                    item = {"url": candidate.url, "title": candidate.title, "text": candidate.text, "publisher": candidate.publisher, "published_at": candidate.published_at}
                    _add(pool, run.run_id, item, lane="CURRENTS", query_id=f"source-{feed.feed_id}", query=feed.currents, sighting_lane="SOURCE", target_id=None, rank=rank, result=result)
            result.feeds[f"currents:{feed.feed_id}"] = len(candidates)
            log(f"  {pack:7} source {feed.feed_id:16} {len(candidates):>3} news items (Currents)")
    by_id = {feed.feed_id: feed for feed in FEEDS}
    for feed_id, pages in (swept_pages or {}).items():
        feed = by_id.get(feed_id)
        if feed is None or pack not in feed.packs:
            continue
        for rank, item in enumerate(pages, start=1):
            _add(pool, run.run_id, dict(item), lane="BRAVE", query_id=f"sweep-{feed_id}", query=feed.sweep or feed.name, sighting_lane="SWEEP", target_id=None, rank=rank, result=result)
        result.feeds[f"sweep:{feed_id}"] = len(pages)
    have_currents = currents_getter is not None or bool(os.environ.get("CURRENTS_API_KEY", "").strip())
    if use_currents and have_currents:
        from grit.acquisition.currents import CurrentsFetchError, collect_currents

        cap = int(os.environ.get("CURRENTS_DAILY_MAX", CURRENTS_DAILY_MAX) or CURRENTS_DAILY_MAX)
        answered = {row["query_id"] for row in pool.rows("SELECT DISTINCT query_id FROM sightings WHERE run_id = ?", (run.run_id,))}
        specs = [spec for spec in currents_specs(pool, run) if spec.query_id not in answered][:cap]
        sent = 0
        for spec in specs:
            try:
                candidates = collect_currents(spec.query, fetched_at=utc_now(), max_records=20, getter=currents_getter)
            except (CurrentsFetchError, ValueError) as exc:
                log(f"  {pack:7} Currents stopped: {exc}")
                result.currents["stopped"] = str(exc)[:80]
                break
            sent += 1
            for rank, candidate in enumerate(candidates, start=1):
                if candidate.url and candidate.title:
                    item = {"url": candidate.url, "title": candidate.title, "text": candidate.text, "publisher": candidate.publisher, "published_at": candidate.published_at}
                    _add(pool, run.run_id, item, lane="CURRENTS", query_id=spec.query_id, query=spec.query, sighting_lane=spec.lane, target_id=target_of(spec), rank=rank, result=result)
        result.currents.update({"sent": sent, "queries": len(specs)})
        log(f"  {pack:7} Currents {sent} queries")
    elif use_currents:
        result.currents["skipped"] = "CURRENTS_API_KEY is not set."
    reader_scan.embed(pool, run, embedder=embedder)
    options = {"score_harm_fn": score_harm_fn} if score_harm_fn is not None else {}
    assessed = reader_scan.assess(pool, run, **options)
    pool.finish_run(run.run_id)
    result.kept = int(assessed.get("kept") or 0)
    result.queued = queue_kept(pool, run.run_id, pack)
    if profiles is not None:
        from grit.report.red import draft_red_alerts

        result.red_drafts = len(draft_red_alerts(pool, run.run_id, pack, list(profiles), run.folder / "alerts"))
    pool.log(actor_type="FUNCTION", actor="DailyLane", status="SUCCEEDED", run_id=run.run_id, detail=result.as_record())
    return result


def queue_kept(pool: Pool, run_id: str, pack: str) -> int:
    """Queue the run's kept pages for the pack's next weekly or monthly scan. A page already queued is not queued twice."""
    from grit.copilot.web import DAILY_LANE

    queued = 0
    for row in pool.assessments(run_id, kept_only=True):
        if pool.one("SELECT 1 FROM pool_additions WHERE signal_id = ? AND pack = ?", (row["signal_id"], pack)):
            continue
        pool.insert("pool_additions", {"signal_id": row["signal_id"], "pack": pack, "added_by": DAILY_LANE, "added_at": utc_now(), "status": "PENDING", "run_id": None})
        queued += 1
    return queued
