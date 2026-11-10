"""The daily feed register and reader (spec 42).

Each feed comes from a reader's note: a source the reader named. A feed that cannot be read is kept in the register with its
status, so the gap stays visible instead of silently disappearing. Where an agency's own site blocks automated readers, its
official notices are read from the Federal Register, which publishes them.

| Status | Meaning |
|---|---|
| ACTIVE | Read every day. |
| STALE | The feed answers, but its newest item is months old. Not read. |
| BLOCKED | The site refuses automated readers, or has no feed. Not read. |

A stale or blocked source with a sweep query is reached once a week by the source sweep instead: one Brave News query
restricted to the site (spec 42, section 2).

Besides RSS and Atom, the register reads five official sources in their own formats (the Feed kind):

| Kind | Source | Item |
|---|---|---|
| kev | CISA Known Exploited Vulnerabilities, JSON | One per vulnerability added. |
| usgs | USGS earthquakes, GeoJSON | One per earthquake of magnitude USGS_MIN_MAG (5.0) or more in the Taiwan region. |
| ncdr | Taiwan NCDR official alerts, Atom (public domain) | One per typhoon, tsunami, rain, flood, debris-flow, closure, outage or air-raid alert. |
| mnd | Taiwan Ministry of National Defense, PLA activities | One per daily report, with its aircraft and ship counts. |
| firms | NASA FIRMS fire detections, CSV; needs FIRMS_MAP_KEY | One per day with FIRMS_MIN_DETECTIONS (5) or more high-confidence detections in Taiwan. |

Checked on 20 October 2026 with grit feeds --check.
"""

from __future__ import annotations

import calendar
import csv
import html
import io
import os
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
    sweep: str | None = None  # the weekly Brave query for a stale or blocked source, such as "site:rusi.org"
    kind: str = "rss"  # rss (RSS or Atom), kev, usgs, ncdr, mnd or firms: see the module notes
    currents: str | None = None  # a daily Currents query for a source with no feed: news that reports the source's notices


GROUP, TECH, CYBER, URS_TW, WSH = ("ste",), ("ste", "dsc"), ("ste", "dsc", "urs"), ("urs-tw",), ("wsh",)
WILSON = "wilson N1 (9 Oct 2026)"
REGISTERS = "DS&C and URS source registers; Jony Ling, 23 Oct 2026"
TW_HAZARDS = "URS-TW official hazard and cross-strait sources; Jony Ling, improvement point 14, 26 Oct 2026"
WSH_BASIS = "WSH functional pack for James Lek; spec 47"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"
NCDR_URL = "https://alerts.ncdr.nat.gov.tw/RssAtomFeeds.ashx"
MND_URL = "https://www.mnd.gov.tw/en/news/plaactlist"
FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/VIIRS_SNPP_NRT/119.3,21.8,122.1,25.4/{days}"
TAIWAN_REGION = (118.0, 21.0, 123.5, 26.5)  # west, south, east, north: Taiwan, its outlying islands and the Strait
USGS_MIN_MAG = float(os.environ.get("USGS_MIN_MAG", "5.0") or 5.0)
FIRMS_MIN_DETECTIONS = int(os.environ.get("FIRMS_MIN_DETECTIONS", "5") or 5)
# NCDR alert categories GRIT-Scan reads, with their English names. Earthquakes come from USGS, which gives magnitudes.
NCDR_CATEGORIES = {
    "颱風": "Typhoon", "海嘯": "Tsunami", "豪雨": "Heavy rain", "大雨": "Heavy rain", "淹水": "Flooding", "土石流": "Debris flow",
    "停班停課": "Work and school closure", "停電": "Power outage", "停水": "Water supply cut", "防空": "Air-raid alert", "空襲": "Air-raid alert",
}

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
    Feed("cisa-kev", "US CISA Known Exploited Vulnerabilities", KEV_URL, CYBER, REGISTERS, kind="kev",
         note="CISA's advisory pages refuse automated readers; its catalogue of exploited vulnerabilities is open."),
    Feed("csa", "Cyber Security Agency of Singapore", None, CYBER, REGISTERS, BLOCKED, "csa.gov.sg publishes no feed; its advisories are read through the news that reports them.",
         "site:csa.gov.sg", currents="Cyber Security Agency of Singapore"),
    Feed("cisa", "US CISA advisories", None, CYBER, WILSON, BLOCKED, "cisa.gov refuses automated readers (HTTP 403), and the Federal Register files CISA notices under all of Homeland Security.",
         "site:cisa.gov", currents="CISA advisory"),
    Feed("usgs-taiwan", "USGS earthquakes, Taiwan region", USGS_URL, URS_TW, TW_HAZARDS, kind="usgs"),
    Feed("ncdr-taiwan", "Taiwan NCDR official disaster alerts", NCDR_URL, URS_TW, TW_HAZARDS, kind="ncdr",
         note="Carries the Central Weather Administration's typhoon, rain and tsunami warnings."),
    Feed("tw-mnd", "Taiwan Ministry of National Defense, PLA activities around Taiwan", MND_URL, URS_TW, TW_HAZARDS, kind="mnd"),
    Feed("nasa-firms", "NASA FIRMS fire detections, Taiwan", FIRMS_URL, URS_TW, TW_HAZARDS, kind="firms",
         note="Needs a free FIRMS_MAP_KEY from firms.modaps.eosdis.nasa.gov/api/map_key."),
    Feed("cwa", "Taiwan Central Weather Administration", None, URS_TW, TW_HAZARDS, BLOCKED,
         "CWA's RSS terms forbid parsing it into derived products, and its open-data API needs a key. Its warnings arrive through the NCDR feed."),
    Feed("mom-wsh", "Ministry of Manpower WSH", None, WSH, WSH_BASIS, BLOCKED,
         "mom.gov.sg publishes no stable public WSH RSS for automated readers; notices are read through Currents and the monthly scan.",
         "site:mom.gov.sg workplace safety", currents="Ministry of Manpower Singapore workplace safety fatality"),
    Feed("wshc", "Workplace Safety and Health Council", None, WSH, WSH_BASIS, BLOCKED,
         "wshc.sg publishes no stable public RSS for automated readers; circulars are read through Currents and the monthly scan.",
         "site:wshc.sg", currents="WSH Council Singapore circular"),
    Feed("trendforce", "TrendForce", "https://www.trendforce.com/news/feed/", TECH, WILSON, STALE, "The feed's newest item is from July 2026.", "site:trendforce.com"),
    Feed("csis", "CSIS", "https://www.csis.org/rss.xml", GROUP, WILSON, STALE, "The feed's newest item is from 2016.", "site:csis.org"),
    Feed("isw", "Institute for the Study of War", None, GROUP, WILSON, BLOCKED, "understandingwar.org refuses automated readers (HTTP 403).", "site:understandingwar.org"),
    Feed("rusi", "RUSI", None, GROUP, WILSON, BLOCKED, "rusi.org returns no feed.", "site:rusi.org"),
    Feed("breaking-defense", "Breaking Defense", None, GROUP, WILSON, BLOCKED, "breakingdefense.com refuses automated readers (HTTP 403).", "site:breakingdefense.com"),
    Feed("janes", "Janes", None, GROUP, WILSON, BLOCKED, "Subscription; no public feed.", "site:janes.com"),
    Feed("tsmc", "TSMC", None, TECH, WILSON, BLOCKED, "investor.tsmc.com refuses automated readers (HTTP 403).", "site:tsmc.com"),
    Feed("pif", "Saudi Public Investment Fund", None, GROUP, WILSON, BLOCKED, "pif.gov.sa refuses automated readers (HTTP 403).", "site:pif.gov.sa"),
    Feed("neom", "NEOM", None, GROUP, WILSON, BLOCKED, "No public feed.", "site:neom.com"),
    Feed("uae-gov", "UAE Government (u.ae)", None, GROUP, WILSON, BLOCKED, "No public feed.", "site:u.ae"),
    Feed("anthropic", "Anthropic research", None, TECH, WILSON, BLOCKED, "No public feed.", "site:anthropic.com"),
    Feed("ibm-quantum", "IBM Quantum", None, TECH, WILSON, BLOCKED, "No public feed.", "site:ibm.com quantum"),
    Feed("world-bank", "World Bank news", None, GROUP, WILSON, BLOCKED, "worldbank.org returns an empty feed.", "site:worldbank.org"),
    Feed("imf", "IMF news", None, GROUP, WILSON, BLOCKED, "imf.org refuses automated readers (HTTP 403).", "site:imf.org"),
)


def swept(packs: tuple[str, ...] | list[str] | None = None) -> list[Feed]:
    """Stale and blocked sources reached by the weekly source sweep."""
    return [feed for feed in FEEDS if feed.status != ACTIVE and feed.sweep and (not packs or set(feed.packs) & set(packs))]


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


def read_kev(raw: bytes, since: datetime) -> list[dict[str, Any]]:
    """One item per vulnerability CISA added to the catalog on or after since. Each links to its NVD record."""
    import json

    items = []
    for row in json.loads(raw).get("vulnerabilities") or []:
        added = str(row.get("dateAdded") or "")
        try:
            when = datetime.strptime(added, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when < since or not row.get("cveID"):
            continue
        ransomware = " CISA lists known use in ransomware campaigns." if str(row.get("knownRansomwareCampaignUse") or "").casefold() == "known" else ""
        items.append({
            "url": f"https://nvd.nist.gov/vuln/detail/{row['cveID']}",
            "title": f"CISA adds {row['cveID']} ({row.get('vendorProject')} {row.get('product')}) to Known Exploited Vulnerabilities: {row.get('vulnerabilityName')}",
            "text": _clean(f"{row.get('shortDescription') or ''} Required action: {row.get('requiredAction') or 'not stated'} Due {row.get('dueDate') or 'not stated'}.{ransomware}")[:TEXT_CHARS],
            "publisher": "cisa.gov", "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return sorted(items, key=lambda item: item["published_at"], reverse=True)


def _in_taiwan_region(lon: Any, lat: Any) -> bool:
    west, south, east, north = TAIWAN_REGION
    try:
        return west <= float(lon) <= east and south <= float(lat) <= north
    except (TypeError, ValueError):
        return False


def read_usgs(raw: bytes, since: datetime, min_mag: float = USGS_MIN_MAG) -> list[dict[str, Any]]:
    """One item per earthquake of min_mag or more in the Taiwan region since since, linked to its USGS event page."""
    import json

    items = []
    for feature in json.loads(raw).get("features") or []:
        props, coords = feature.get("properties") or {}, (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or not _in_taiwan_region(coords[0], coords[1]) or props.get("mag") is None or float(props["mag"]) < min_mag:
            continue
        when = datetime.fromtimestamp(int(props.get("time") or 0) / 1000, tz=timezone.utc)
        if when < since or not props.get("url"):
            continue
        depth = f"{float(coords[2]):.0f} km deep" if len(coords) > 2 and coords[2] is not None else "depth not stated"
        pager = f" USGS PAGER alert level {props['alert']}." if props.get("alert") else ""
        tsunami = " USGS flags a possible tsunami." if props.get("tsunami") else ""
        items.append({
            "url": props["url"], "title": f"USGS: magnitude {float(props['mag']):.1f} earthquake, {props.get('place') or 'Taiwan region'}",
            "text": f"Magnitude {float(props['mag']):.1f} earthquake {props.get('place') or 'in the Taiwan region'}, {depth}, at {when:%Y-%m-%d %H:%M} UTC.{pager}{tsunami}",
            "publisher": "usgs.gov", "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return sorted(items, key=lambda item: item["published_at"], reverse=True)


def read_ncdr(raw: bytes, since: datetime) -> list[dict[str, Any]]:
    """One item per NCDR alert in NCDR_CATEGORIES. The title gives the English category. The Chinese text is kept as issued."""
    import feedparser

    items = []
    for entry in feedparser.parse(raw).entries:
        terms = " ".join([str(entry.get("title") or "")] + [str(tag.get("term") or "") for tag in entry.get("tags") or []])
        english = next((name for chinese, name in NCDR_CATEGORIES.items() if chinese in terms), None)
        url, published = str(entry.get("link") or "").strip(), _published(entry)
        if english is None or not url.startswith(("http://", "https://")) or not published:
            continue
        if datetime.strptime(published, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) < since:
            continue
        summary, issuer = _clean(entry.get("summary") or ""), _clean(entry.get("author") or "") or "NCDR"
        items.append({
            "url": url, "title": f"Taiwan official alert: {english}, issued by {issuer}",
            "text": f"{english} alert for Taiwan. {summary}"[:TEXT_CHARS], "publisher": "alerts.ncdr.nat.gov.tw", "published_at": published,
        })
    return sorted(items, key=lambda item: item["published_at"], reverse=True)


_MND_ROW = re.compile(r'<a href="(/en/News/PLAAct/\d+)"[^>]*>\s*<div class="date[^"]*">\s*(\d{4})\.(\d{2})\.(\d{2})\s*</div>', re.I)
_MND_COUNTS = (
    (re.compile(r"(\d+) sorties? of PLA aircraft", re.I), "PLA aircraft sortie"),
    (re.compile(r"(\d+) PLAN (?:ships?|vessels?)", re.I), "PLAN ship"),
    (re.compile(r"(\d+) official ships?", re.I), "official ship"),
    (re.compile(r"(\d+) balloons?", re.I), "balloon"),
)
_MND_CROSSED = re.compile(r"(\d+) out of \d+ sorties? crossed the median line", re.I)


def mnd_report(text: str) -> tuple[str, str]:
    """The title and text of one MND daily report, from the page's text."""
    start, end = text.find("1.Date"), text.find("Keywords")
    body = text[start:end if end > start else None].strip() if start >= 0 else text[:TEXT_CHARS]
    counts = [f"{match.group(1)} {label}{'' if match.group(1) == '1' else 's'}" for pattern, label in _MND_COUNTS if (match := pattern.search(body))]
    crossed = _MND_CROSSED.search(body)
    title = "Taiwan MND: " + (", ".join(counts) if counts else "PLA activities") + " around Taiwan"
    if crossed:
        title += f"; {crossed.group(1)} crossed the Strait median line"
    return title, body[:TEXT_CHARS]


def read_mnd(feed: Feed, since: datetime, getter: Callable[[str], bytes]) -> list[dict[str, Any]]:
    """One item per MND daily report of PLA activity around Taiwan, read from the list page and each report's page."""
    from urllib.parse import urljoin

    listing = getter(feed.url).decode("utf-8", "replace")
    items, seen = [], set()
    for path, year, month, day in _MND_ROW.findall(listing):
        # A report is dated the day it is issued, at 06:00 Taiwan time (UTC+8).
        when = datetime(int(year), int(month), int(day), tzinfo=timezone.utc) - timedelta(hours=2)
        if when < since or path in seen:
            continue
        seen.add(path)
        url = urljoin(feed.url, path)
        page = getter(url).decode("utf-8", "replace")
        page = page[page.find('aria-current="page"'):] if 'aria-current="page"' in page else page
        title, text = mnd_report(_clean(re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S | re.I)))
        items.append({"url": url, "title": title, "text": text, "publisher": "mnd.gov.tw", "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ")})
    return sorted(items, key=lambda item: item["published_at"], reverse=True)


def read_firms(raw: bytes, since: datetime, min_detections: int = FIRMS_MIN_DETECTIONS) -> list[dict[str, Any]]:
    """One item per day with min_detections or more high-confidence fire detections in Taiwan, linked to that day's map."""
    by_day: dict[str, list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))):
        if str(row.get("confidence") or "").strip().lower() in {"h", "high"} and row.get("acq_date"):
            by_day.setdefault(row["acq_date"], []).append(row)
    items = []
    for day, rows in by_day.items():
        when = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if when < since or len(rows) < min_detections:
            continue
        top = max(rows, key=lambda row: float(row.get("frp") or 0))
        lat, lon = float(top["latitude"]), float(top["longitude"])
        items.append({
            "url": f"https://firms.modaps.eosdis.nasa.gov/map/?d={day}&lat={lat:.3f}&lon={lon:.3f}",
            "title": f"NASA FIRMS: {len(rows)} high-confidence fire detections in Taiwan on {day}",
            "text": f"{len(rows)} high-confidence VIIRS fire detections in Taiwan on {day}. The strongest, {float(top.get('frp') or 0):.0f} MW fire radiative power, is at latitude {lat:.3f}, longitude {lon:.3f}.",
            "publisher": "nasa.gov", "published_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return sorted(items, key=lambda item: item["published_at"], reverse=True)


def _firms(feed: Feed, since: datetime, getter: Callable[[str], bytes] | None) -> list[dict[str, Any]]:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    if not key and getter is None:
        raise RuntimeError("FIRMS_MAP_KEY is not set (free key: firms.modaps.eosdis.nasa.gov/api/map_key)")
    days = min(5, max(1, (datetime.now(timezone.utc) - since).days + 1))
    try:
        raw = (getter or _get)(feed.url.format(key=key or "KEY", days=days))
    except Exception as exc:  # the URL carries the key, so it is never shown
        raise RuntimeError(f"FIRMS request failed ({type(exc).__name__})") from None
    if raw.lstrip()[:1] not in (b"l", b"L"):  # FIRMS answers an invalid key or a bad request with plain text, not CSV
        raise RuntimeError(f"FIRMS refused the request: {raw[:80].decode('utf-8', 'replace').strip()}")
    return read_firms(raw, since)


def read_feed(feed: Feed, *, since: datetime, getter: Callable[[str], bytes] | None = None) -> list[dict[str, Any]]:
    """Items published on or after since, newest first. An item without a date is skipped: its age cannot be checked."""
    import feedparser
    from urllib.parse import urlparse

    from grit.acquisition.brave_plan import canonical_url

    if feed.kind == "kev":
        return read_kev((getter or _get)(feed.url), since)
    if feed.kind == "usgs":
        return read_usgs((getter or _get)(feed.url), since)
    if feed.kind == "ncdr":
        return read_ncdr((getter or _get)(feed.url), since)
    if feed.kind == "mnd":
        return read_mnd(feed, since, getter or _get)
    if feed.kind == "firms":
        return _firms(feed, since, getter)
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
            if feed.kind == "kev":
                raw = (getter or _get)(feed.url)
                rows = read_kev(raw, datetime(1970, 1, 1, tzinfo=timezone.utc))
                entries, dates = rows, sorted(row["published_at"] for row in rows)
            elif feed.kind != "rss":
                rows = read_feed(feed, since=now - timedelta(days=30), getter=getter)
                entries, dates = rows, sorted(row["published_at"] for row in rows)
            else:
                entries = feedparser.parse((getter or _get)(feed.url)).entries
                dates = sorted(filter(None, (_published(entry) for entry in entries)))
            newest = dates[-1] if dates else None
            fresh = newest is not None and datetime.strptime(newest, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) >= now - timedelta(days=30)
            out.append({"feed": feed.feed_id, "status": feed.status, "items": len(entries), "newest": newest, "error": None if fresh else "No item in the last 30 days."})
        except Exception as exc:
            out.append({"feed": feed.feed_id, "status": feed.status, "items": 0, "newest": None, "error": str(exc)[:80] or type(exc).__name__})
    return out
