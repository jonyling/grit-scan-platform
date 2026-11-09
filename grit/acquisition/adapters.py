"""Turn fetched records into candidates. No adapter scores a signal."""

from __future__ import annotations

from typing import Any

from grit.acquisition.candidate import Candidate, SourceLane, content_hash


def from_articles(records: list[dict[str, Any]], *, lane: SourceLane, fetched_at: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    for record in records:
        title = str(record.get("title") or record.get("headline") or "").strip()
        text = str(record.get("text") or record.get("description") or record.get("summary") or "").strip()
        url = _optional(record.get("url") or record.get("article_url"))
        publisher = _optional(record.get("publisher") or record.get("source_name"))
        published_at = _optional(record.get("published_at") or record.get("publishedAt"))
        seen_at = _optional(record.get("seen_at"))
        if not title and not text:
            continue
        candidates.append(
            Candidate(
                source_lane=lane,
                title=title,
                text=text,
                url=url,
                publisher=publisher,
                published_at=published_at,
                fetched_at=fetched_at,
                content_hash=content_hash(lane, url, title, text),
                seen_at=seen_at,
            )
        )
    return candidates


def approved_web(records: list[dict[str, Any]], *, fetched_at: str) -> list[Candidate]:
    return from_articles(records, lane="APPROVED", fetched_at=fetched_at)


def wild_scan(records: list[dict[str, Any]], *, fetched_at: str) -> list[Candidate]:
    return from_articles(records, lane="WILD", fetched_at=fetched_at)


def sphera(records: list[dict[str, Any]], *, fetched_at: str) -> list[Candidate]:
    normalised = [
        {
            "title": record.get("title") or record.get("event_title"),
            "text": record.get("text") or record.get("description"),
            "url": record.get("url"),
            "publisher": record.get("publisher") or "Sphera",
            "published_at": record.get("published_at") or record.get("event_date"),
        }
        for record in records
    ]
    return from_articles(normalised, lane="SPHERA", fetched_at=fetched_at)


def agile(records: list[dict[str, Any]], *, fetched_at: str) -> list[Candidate]:
    normalised = [
        {
            "title": record.get("title") or record.get("headline"),
            "text": record.get("text") or record.get("summary"),
            "url": record.get("url"),
            "publisher": record.get("publisher") or "AGILE",
            "published_at": record.get("published_at"),
        }
        for record in records
    ]
    return from_articles(normalised, lane="AGILE", fetched_at=fetched_at)


def isos(records: list[dict[str, Any]] | None = None, *, fetched_at: str) -> list[Candidate]:
    if not records:
        return []
    return from_articles(records, lane="ISOS", fetched_at=fetched_at)


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
