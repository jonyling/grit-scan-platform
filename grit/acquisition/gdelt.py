"""Wild-scan collection from GDELT.

The scan loop reads the public 15-minute events export. It does not poll the
DOC API. This module emits candidates or URL index hits. It does not score,
and it does not read tone. Currents is a separate module.
"""

from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import urlparse

from grit.acquisition.adapters import wild_scan
from grit.acquisition.candidate import Candidate

DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
LASTUPDATE_URL = "http://data.gdeltproject.org/gdeltv2/lastupdate.txt"
MAX_EXPORT_SLICES = 4
_ACTOR1_NAME = 6
_ACTOR2_NAME = 16
_ACTOR1_GEO = 36
_ACTOR2_GEO = 44
_ACTION_GEO = 52
_DATE_ADDED = 59
_SOURCE_URL = 60
_EXPORT_COLUMNS = 61
_TIMESPAN = re.compile(r"^\d+(?:min|h|d|w|m)$")
_SEEN = re.compile(r"^\d{8}T\d{6}Z$")
_ADDED = re.compile(r"^\d{14}$")
_STAMP = re.compile(r"(\d{14})\.export\.CSV\.zip$")
_MD5 = re.compile(r"^[0-9a-f]{32}$")
Getter = Callable[[str, dict[str, str]], dict[str, Any]]
ByteGetter = Callable[[str], bytes]


class GdeltFetchError(RuntimeError):
    """GDELT did not return the requested file or article list. This is not an empty week."""


@dataclass(frozen=True)
class ExportHit:
    """A URL from a 15-minute events file. It is not a headline."""

    url: str
    publisher: str | None
    seen_at: str | None
    matched_queries: tuple[str, ...]


def collect_export_scan(
    queries: str | list[str],
    *,
    max_records: int = 25,
    slices: int = 1,
    getter: ByteGetter | None = None,
) -> list[ExportHit]:
    """Download the latest events export and filter each condition locally.

    This does not call the DOC API. Mentions, GKG, tone, and Goldstein scores
    are not read. A failed download is an error, not an empty slice.
    """
    terms = _queries(queries)
    if not isinstance(slices, int) or not 1 <= slices <= MAX_EXPORT_SLICES:
        raise ValueError("slices must be an integer from 1 to 4")
    if not isinstance(max_records, int) or not 1 <= max_records <= 250:
        raise ValueError("max_records must be an integer from 1 to 250")
    fetch = getter or _get_bytes
    url, digest = export_url_from_lastupdate(_text(fetch(LASTUPDATE_URL)))
    stamp = _stamp_from_url(url)
    rows: list[dict[str, str]] = []
    for offset in range(slices):
        file_url = _export_url(_shift_stamp(stamp, -15 * offset))
        payload = fetch(file_url)
        if offset == 0 and hashlib.md5(payload).hexdigest() != digest:
            raise GdeltFetchError("GDELT export checksum did not match")
        rows.extend(rows_from_export_zip(payload))
    return match_export_rows(rows, terms, max_records)


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


def export_url_from_lastupdate(text: str) -> tuple[str, str]:
    """Return the events-export URL and checksum. Ignore mentions and GKG."""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[2].endswith(".export.CSV.zip"):
            continue
        digest, url = parts[1].lower(), parts[2]
        if not _MD5.fullmatch(digest):
            raise GdeltFetchError("GDELT export listing had no checksum")
        host = urlparse(url).netloc.lower()
        if host != "data.gdeltproject.org" or not urlparse(url).path.startswith("/gdeltv2/"):
            raise GdeltFetchError("GDELT export URL was not on the GDELT data host")
        return url, digest
    raise GdeltFetchError("GDELT listing had no 15-minute export file")


def rows_from_export_zip(payload: bytes) -> list[dict[str, str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise GdeltFetchError("GDELT export zip did not contain one file")
            text = archive.read(names[0]).decode("utf-8", "replace")
    except GdeltFetchError:
        raise
    except (zipfile.BadZipFile, OSError) as exc:
        raise GdeltFetchError("GDELT export zip could not be read") from exc
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < _EXPORT_COLUMNS:
            continue
        url = fields[_SOURCE_URL].strip()
        host = urlparse(url).netloc.lower().removeprefix("www.")
        if not url.startswith(("http://", "https://")) or host.endswith("gdeltproject.org"):
            continue
        rows.append(
            {
                "url": url,
                "publisher": host,
                "seen_at": _added_at(fields[_DATE_ADDED]),
                "search_text": " ".join(
                    fields[index] for index in (_ACTOR1_NAME, _ACTOR2_NAME, _ACTOR1_GEO, _ACTOR2_GEO, _ACTION_GEO, _SOURCE_URL)
                ).lower(),
            }
        )
    return rows


def match_export_rows(rows: list[dict[str, str]], queries: list[str], max_records: int) -> list[ExportHit]:
    """Apply each condition on its own. Do not join the queries into one search."""
    grouped: dict[str, tuple[str, ...]] = {}
    order: list[str] = []
    details: dict[str, dict[str, str]] = {}
    for query in queries:
        tokens = _match_tokens(query)
        kept = 0
        for row in rows:
            if kept >= max_records:
                break
            if not all(token in row["search_text"] for token in tokens):
                continue
            kept += 1
            url = row["url"]
            if url not in grouped:
                grouped[url] = ()
                order.append(url)
                details[url] = row
            if query not in grouped[url]:
                grouped[url] = grouped[url] + (query,)
    return [
        ExportHit(
            url=url,
            publisher=details[url]["publisher"] or None,
            seen_at=details[url]["seen_at"] or None,
            matched_queries=grouped[url],
        )
        for url in order
    ]


def _match_tokens(query: str) -> tuple[str, ...]:
    tokens = tuple(token for token in re.split(r"[^0-9A-Za-z]+", query.lower()) if len(token) >= 3)
    if not tokens:
        raise ValueError("GDELT export query needs a word of at least 3 letters")
    return tokens


def _added_at(value: str) -> str:
    text = value.strip()
    if not _ADDED.fullmatch(text):
        return ""
    try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return ""
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_from_url(url: str) -> str:
    match = _STAMP.search(url)
    if not match:
        raise GdeltFetchError("GDELT export URL had no 15-minute timestamp")
    return match.group(1)


def _shift_stamp(stamp: str, minutes: int) -> str:
    parsed = datetime.strptime(stamp, "%Y%m%d%H%M%S") + timedelta(minutes=minutes)
    return parsed.strftime("%Y%m%d%H%M%S")


def _export_url(stamp: str) -> str:
    return f"http://data.gdeltproject.org/gdeltv2/{stamp}.export.CSV.zip"


def _text(payload: bytes) -> str:
    return payload.decode("utf-8", "replace")


def _get_bytes(url: str) -> bytes:
    import requests

    try:
        response = requests.get(url, timeout=120, headers={"User-Agent": "GRIT-Scan wild-scan"})
    except requests.RequestException as exc:
        raise GdeltFetchError("GDELT request failed") from exc
    if response.status_code != 200:
        raise GdeltFetchError(f"GDELT returned HTTP {response.status_code}")
    return response.content


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
