"""The daily feed register and reader (spec 42).

Each feed comes from a reader's note: a source the reader named. A feed that cannot be read is kept in the register with its
status, so the gap stays visible instead of silently disappearing. Where an agency's own site blocks automated readers, its
official notices are read from the Federal Register, which publishes them.

| Status | Meaning |
|---|---|
| ACTIVE | Read every day. |
| STALE | The feed answers, but its newest item is months old. Not read. |
| BLOCKED | The site refuses automated readers, or has no feed. Not read. Reach it through a weekly search or by hand. |

Checked on 20 October 2026 with grit feeds --check.
"""

from __future__ import annotations

import calendar
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

ACTIVE, STALE, BLOCKED = "ACTIVE", "STALE", "BLOCKED"
TEXT_CHARS = 3000
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) GRIT-Scan daily feed reader"
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
FEDERAL_REGISTER = "https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bagencies%5D%5B%5D="


@dataclass(frozen=True)
class Feed:
    feed_id: str
    name: str
    url: str | None
    packs: tuple[str, ...]  # the daily runs whose rules assess its items
    basis: str  # the reader's note that names the source
    status: str = ACTIVE
    note: str = ""


GROUP, TECH = ("ste",), ("ste", "dsc")
WILSON = "wilson N1 (9 Oct 2026)"

FEEDS: tuple[Feed, ...] = (
    Feed("atlantic-council", "Atlantic Council", "https://www.atlanticcouncil.org/feed/", GROUP, WILSON),
    Feed("defense-news", "Defense News", "https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml", GROUP, WILSON),
    Feed("semianalysis", "SemiAnalysis", "https://newsletter.semianalysis.com/feed", TECH, WILSON),
    Feed("toms-hardware", "Tom's Hardware", "https://www.tomshardware.com/feeds/all", TECH, WILSON),
    Feed("epoch-ai", "Epoch AI", "https://epochai.substack.com/feed", TECH, WILSON),
    Feed("metr", "METR", "https://metr.org/feed.xml", TECH, WILSON),
    Feed("uk-aisi", "UK AI Security Institute", "https://www.gov.uk/government/organisations/ai-security-institute.atom", TECH, WILSON),
    Feed("nist", "NIST news", "https://www.nist.gov/news-events/news/rss.xml", TECH, WILSON),
    Feed("quantum-insider", "The Quantum Insider", "https://thequantuminsider.com/feed/", TECH, WILSON),
    Feed("fr-bis", "US BIS, Federal Register notices", FEDERAL_REGISTER + "industry-and-security-bureau", TECH, WILSON, note="bis.gov blocks automated readers."),
    Feed("fr-ofac", "US OFAC, Federal Register notices", FEDERAL_REGISTER + "foreign-assets-control-office", TECH, WILSON, note="OFAC's own feed stopped in February 2025."),
    Feed("cisa", "US CISA", None, TECH, WILSON, BLOCKED, "cisa.gov refuses automated readers (HTTP 403), and the Federal Register files CISA notices under all of Homeland Security."),
    Feed("trendforce", "TrendForce", "https://www.trendforce.com/news/feed/", TECH, WILSON, STALE, "The feed's newest item is from July 2026."),
    Feed("csis", "CSIS", "https://www.csis.org/rss.xml", GROUP, WILSON, STALE, "The feed's newest item is from 2016."),
    Feed("isw", "Institute for the Study of War", None, GROUP, WILSON, BLOCKED, "understandingwar.org refuses automated readers (HTTP 403)."),
    Feed("rusi", "RUSI", None, GROUP, WILSON, BLOCKED, "rusi.org returns no feed."),
    Feed("breaking-defense", "Breaking Defense", None, GROUP, WILSON, BLOCKED, "breakingdefense.com refuses automated readers (HTTP 403)."),
    Feed("janes", "Janes", None, GROUP, WILSON, BLOCKED, "Subscription; no public feed."),
    Feed("tsmc", "TSMC investor relations", None, TECH, WILSON, BLOCKED, "investor.tsmc.com refuses automated readers (HTTP 403)."),
    Feed("pif", "Saudi Public Investment Fund", None, GROUP, WILSON, BLOCKED, "pif.gov.sa refuses automated readers (HTTP 403)."),
    Feed("neom", "NEOM", None, GROUP, WILSON, BLOCKED, "No public feed."),
    Feed("uae-gov", "UAE Government (u.ae)", None, GROUP, WILSON, BLOCKED, "No public feed."),
    Feed("anthropic", "Anthropic research", None, TECH, WILSON, BLOCKED, "No public feed."),
    Feed("ibm-quantum", "IBM Quantum", None, TECH, WILSON, BLOCKED, "No public feed."),
    Feed("world-bank", "World Bank news", None, GROUP, WILSON, BLOCKED, "worldbank.org returns an empty feed."),
    Feed("imf", "IMF news", None, GROUP, WILSON, BLOCKED, "imf.org refuses automated readers (HTTP 403)."),
)


def active(packs: tuple[str, ...] | list[str] | None = None) -> list[Feed]:
    return [feed for feed in FEEDS if feed.status == ACTIVE and feed.url and (not packs or set(feed.packs) & set(packs))]


def _get(url: str) -> bytes:
    import requests

    response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8"})
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    return response.content


def _clean(text: str) -> str:
    return _SPACES.sub(" ", html.unescape(_TAGS.sub(" ", text or ""))).strip()


def _published(entry: Any) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime.fromtimestamp(calendar.timegm(parsed), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_feed(feed: Feed, *, since: datetime, getter: Callable[[str], bytes] | None = None) -> list[dict[str, Any]]:
    """Items published on or after since, newest first. An item without a date is skipped: its age cannot be checked."""
    import feedparser
    from urllib.parse import urlparse

    from grit.acquisition.brave_plan import canonical_url

    parsed = feedparser.parse((getter or _get)(feed.url))
    items = []
    for entry in parsed.entries:
        url, title, published = str(entry.get("link") or "").strip(), _clean(entry.get("title") or ""), _published(entry)
        if not url.startswith(("http://", "https://")) or not title or not published:
            continue
        if datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) < since:
            continue
        body = entry.get("summary") or ""
        if entry.get("content"):
            body = entry["content"][0].get("value") or body
        items.append({"url": canonical_url(url), "title": title, "text": _clean(body)[:TEXT_CHARS], "publisher": urlparse(url).netloc.lower().removeprefix("www."), "published_at": published})
    return sorted(items, key=lambda item: item["published_at"], reverse=True)


def _federal_register_agency(slug: str) -> bool:
    """The Federal Register ignores an unknown agency filter and returns every document, so the slug is checked first."""
    import requests

    return requests.get(f"https://www.federalregister.gov/api/v1/agencies/{slug}", timeout=20, headers={"User-Agent": USER_AGENT}).status_code == 200


def check_feeds(getter: Callable[[str], bytes] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Read every feed with a URL and report its item count and newest date, for grit feeds --check."""
    import feedparser

    now = now or datetime.now(timezone.utc)
    out = []
    for feed in FEEDS:
        if not feed.url:
            out.append({"feed": feed.feed_id, "status": feed.status, "items": 0, "newest": None, "error": feed.note})
            continue
        try:
            if feed.url.startswith(FEDERAL_REGISTER) and getter is None and not _federal_register_agency(feed.url[len(FEDERAL_REGISTER):]):
                raise RuntimeError("unknown Federal Register agency: the feed would return every agency's documents")
            entries = feedparser.parse((getter or _get)(feed.url)).entries
            dates = sorted(filter(None, (_published(entry) for entry in entries)))
            newest = dates[-1] if dates else None
            fresh = newest is not None and datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= now - timedelta(days=30)
            out.append({"feed": feed.feed_id, "status": feed.status, "items": len(entries), "newest": newest, "error": None if fresh else "No item in the last 30 days."})
        except Exception as exc:
            out.append({"feed": feed.feed_id, "status": feed.status, "items": 0, "newest": None, "error": str(exc)[:80] or type(exc).__name__})
    return out
