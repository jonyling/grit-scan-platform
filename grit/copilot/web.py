"""The Co-Pilot's web search, and adding a web page to the pool (spec 41).

Web search:
- Currents answers first, to keep Brave's monthly queries for the weekly scans. Brave answers when Currents fails or
  returns nothing.
- Set COPILOT_WEB_ORDER=brave to put Brave first. Currents still goes first when Brave is within 10% of its monthly
  limit.
- Each reader has a daily budget: copilot.web_searches_per_day in their profile, else 17. A question may use at most 2.
- Results are sources of kind WEB, marked unvetted: GRIT-Scan's rules have not assessed them.

Add to pool: a web page that a reader adds goes into the pool as a CHAT-lane signal. It is queued for each pack in the
answer's scope. The next scan of that pack gives it a sighting, so the deterministic assessment keeps or drops it like any other
page. It reaches a brief only if the rules keep it and a reviewer approves the package.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from grit.pool import Pool, utc_now

DAILY_DEFAULT = 17
PER_QUESTION = 2
RESULTS = 8
NEAR_LIMIT = 0.9
UNVETTED = "Web results are unvetted: GRIT-Scan's rules have not assessed them."
DAILY_LANE = "daily-lane"  # pool_additions.added_by for pages the daily lane kept, queued for the next weekly or monthly scan


def budget(profile: Any) -> int:
    reader = getattr(profile, "reader", None)
    value = reader.section("copilot").get("web_searches_per_day") if reader is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) else DAILY_DEFAULT


def used_today(pool: Pool, profile_id: str, today: date) -> int:
    row = pool.one("SELECT COUNT(*) AS n FROM copilot_web_searches WHERE profile_id = ? AND day = ?", (profile_id, today.isoformat()))
    return int((row or {}).get("n") or 0)


def brave_near_limit(usage_path: Path | None = None, *, now: datetime | None = None) -> bool:
    from grit.acquisition.brave_plan import DEFAULT_USAGE_PATH, _load_usage, monthly_limit_from_env

    month = (now or datetime.now(timezone.utc)).strftime("%Y-%m")
    return _load_usage(usage_path or DEFAULT_USAGE_PATH, month) >= NEAR_LIMIT * monthly_limit_from_env()


def _brave(query: str, *, getter: Callable[..., Any] | None, usage_path: Path | None, fetched_at: str) -> list[dict[str, Any]]:
    from grit.acquisition.brave_plan import collect_plan

    spec = SimpleNamespace(query_id="copilot", query=query, exact=False)
    options: dict[str, Any] = {"usage_path": usage_path} if usage_path else {}
    run = collect_plan([spec], fetched_at=fetched_at, freshness="pm", count=RESULTS, getter=getter, sleep=lambda _s: None, **options)
    if run.stopped:
        raise RuntimeError(run.stopped)
    from grit.acquisition.brave_plan import canonical_url

    return [dict(item, url=canonical_url(item["url"])) for entry in run.searches for item in entry.get("results") or [] if item.get("url")][:RESULTS]


def _currents(query: str, *, getter: Callable[..., Any] | None, fetched_at: str) -> list[dict[str, Any]]:
    from grit.acquisition.currents import collect_currents

    return [
        {"url": item.url, "title": item.title, "text": item.text, "publisher": item.publisher, "published_at": item.published_at}
        for item in collect_currents(query, fetched_at=fetched_at, max_records=RESULTS, getter=getter) if item.url and item.title
    ][:RESULTS]


def search_web(
    query: str, *, brave_getter: Callable[..., Any] | None = None, currents_getter: Callable[..., Any] | None = None, usage_path: Path | None = None,
    near_limit: bool | None = None, now: datetime | None = None,
) -> dict[str, Any]:
    """One web search. Returns {engine, results, tried}. Never raises for an engine failure."""
    fetched_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    near = brave_near_limit(usage_path, now=now) if near_limit is None else near_limit
    near = near or os.environ.get("COPILOT_WEB_ORDER", "currents").strip().casefold() != "brave"
    have_brave = brave_getter is not None or bool(os.environ.get("BRAVE_SEARCH_API_KEY", "").strip())
    have_currents = currents_getter is not None or bool(os.environ.get("CURRENTS_API_KEY", "").strip())
    order = [name for name, ready in (("CURRENTS", have_currents), ("BRAVE", have_brave)) if ready] if near else \
        [name for name, ready in (("BRAVE", have_brave), ("CURRENTS", have_currents)) if ready]
    tried: list[str] = []
    for engine in order:
        try:
            if engine == "BRAVE":
                results = _brave(query, getter=brave_getter, usage_path=usage_path, fetched_at=fetched_at)
            else:
                results = _currents(query, getter=currents_getter, fetched_at=fetched_at)
        except Exception as exc:  # an engine failure moves to the next engine; the message never carries a key
            tried.append(f"{engine}: {str(exc).split('{', 1)[0][:80] or type(exc).__name__}")
            continue
        tried.append(f"{engine}: {len(results)} results")
        if results:
            return {"engine": engine, "results": results, "tried": tried}
    return {"engine": "NONE", "results": [], "tried": tried or ["No web search key is configured (BRAVE_SEARCH_API_KEY or CURRENTS_API_KEY)."]}


class WebSearch:
    """The Co-Pilot's web_search tool for one reader and one question."""

    def __init__(self, pool: Pool, profile: Any, *, today: date | None = None, search: Callable[[str], Mapping[str, Any]] | None = None) -> None:
        self.pool, self.profile = pool, profile
        self.today = today or date.today()
        self.search = search or (lambda query: search_web(query))
        self.this_question = 0

    def remaining(self) -> int:
        return max(0, budget(self.profile) - used_today(self.pool, self.profile.profile_id, self.today))

    def __call__(self, query: str) -> dict[str, Any]:
        query = " ".join(str(query or "").split())[:200]
        if not query:
            return {"error": "web_search needs a short keyword query."}
        if self.this_question >= PER_QUESTION:
            return {"error": f"At most {PER_QUESTION} web searches per question. Answer from what you have."}
        if self.remaining() <= 0:
            return {"error": f"Today's web search budget of {budget(self.profile)} is used up. Answer from GRIT-Scan's records and say so."}
        self.this_question += 1
        found = dict(self.search(query))
        results = list(found.get("results") or [])
        status = "OK" if results else ("FAILED" if found.get("engine") == "NONE" else "EMPTY")
        self.pool.insert("copilot_web_searches", {
            "profile_id": self.profile.profile_id, "day": self.today.isoformat(), "engine": found.get("engine") or "NONE", "query": query,
            "results": len(results), "status": status, "searched_at": utc_now(),
        })
        return {"engine": found.get("engine"), "results": results, "tried": found.get("tried") or [], "remaining_today": self.remaining()}


# Add to pool ---------------------------------------------------------------------------------------------------------


def add_to_pool(pool: Pool, profile: Any, page: Mapping[str, Any], packs: Sequence[str]) -> dict[str, Any]:
    """Put a web page into the pool as a CHAT-lane signal and queue it for each pack's next scan."""
    from grit.acquisition.brave_plan import canonical_url

    url = str(page.get("url") or "").strip()
    title = str(page.get("title") or "").strip()
    if not url.startswith(("http://", "https://")) or not title:
        raise ValueError("A page needs an http(s) URL and a title.")
    allowed = set(packs)
    if not allowed:
        raise ValueError("Name at least one pack.")
    text = str(page.get("text") or "")
    url = canonical_url(url)
    signal_id = pool.add_signal(url=url, title=title, text=text, publisher=page.get("publisher"), published_at=page.get("published_at"), lane="CHAT",
                                content_hash=hashlib.sha256(f"{title}\n{text}".encode("utf-8")).hexdigest())
    queued = []
    for pack in sorted(allowed):
        if pool.one("SELECT 1 FROM pool_additions WHERE signal_id = ? AND pack = ?", (signal_id, pack)):
            continue
        pool.insert("pool_additions", {"signal_id": signal_id, "pack": pack, "added_by": profile.profile_id, "added_at": utc_now(), "status": "PENDING", "run_id": None})
        queued.append(pack)
    pool.log(actor_type="FUNCTION", actor="AddToPool", status="QUEUED" if queued else "ALREADY_QUEUED", subject=signal_id, detail={"by": profile.profile_id, "packs": queued})
    return {"signal_id": signal_id, "queued": queued}


def pending_additions(pool: Pool, pack: str) -> list[dict[str, Any]]:
    return pool.rows("SELECT * FROM pool_additions WHERE pack = ? AND status = 'PENDING' ORDER BY added_at", (pack,))


def take_additions(pool: Pool, run_id: str, pack: str) -> int:
    """Give each pending page a sighting in this run, so the run's assessment decides on it. Called by the scan."""
    rows = pending_additions(pool, pack)
    for row in rows:
        daily = row["added_by"] == DAILY_LANE
        pool.add_sighting(row["signal_id"], run_id, query_id=f"{'daily' if daily else 'chat'}-{row['signal_id']}", query="Kept by the daily lane" if daily else "Added from the Co-Pilot",
                          lane="DAILY" if daily else "CHAT", target_id=None, rank=None)
        with pool.transaction() as conn:
            conn.execute("UPDATE pool_additions SET status = 'SCANNED', run_id = ? WHERE signal_id = ? AND pack = ?", (run_id, row["signal_id"], pack))
    return len(rows)
