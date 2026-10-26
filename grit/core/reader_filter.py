"""Keep rule for a reader scan. Pure: it keeps or drops with a logged reason and never scores harm.

1. A page older than the window is STALE, and one published after the run date is AFTER_RUN_DATE. The window is the run
   date and the six days before it (seven days). A page with no date is kept. A market report or listicle is NOT_AN_EVENT.
2. A page is relevant when it links to the business-area register, names a reader-pack item strongly, bears on a standing
   condition or hazard strongly, or hits a KEY theme in its title or lead. Anything else is NOT_RELEVANT.
3. Pages with the same headline, or at cosine 0.95 or above, are one story (NEAR_DUPLICATE). A relevant page absorbs pages from other outlets at 0.90 or
   above (SAME_EVENT), so one event is one signal. Merged outlets count as corroboration, by publisher family. Two reports of
   an official daily series (SERIES_PUBLISHERS, such as Taiwan MND's PLA activity reports) dated on different days are
   separate observations and are never merged, however alike their text.
4. Each survivor gets a briefing priority from grit.core.priority. A reader-pack project is a DIRECT link and a named supplier
   or customer is a COUNTERPARTY link, as the link ladder defines them.
No cap applies here. The brief caps what it prints.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from grit.core.doc_type import NON_EVENT_TYPES
from grit.core.priority import PriorityConfig, assess, key_gate, outlet_groups
from grit.core.scale_cues import ScaleCues
from grit.ontology.link import COUNTERPARTY, DIRECT, rung_rank

KEEP, DROP = "KEEP", "DROP"
STALE, NOT_AN_EVENT, NOT_RELEVANT, NEAR_DUPLICATE, SAME_EVENT = "STALE", "NOT_AN_EVENT", "NOT_RELEVANT", "NEAR_DUPLICATE", "SAME_EVENT"
AFTER_RUN_DATE = "AFTER_RUN_DATE"
# Official sources that publish one report per day or per event in near-identical words (grit.daily.feeds).
SERIES_PUBLISHERS = frozenset({"mnd.gov.tw", "usgs.gov", "alerts.ncdr.nat.gov.tw", "nasa.gov", "cisa.gov"})
_READER_RUNG = {"PROJECT": DIRECT, "SITE": DIRECT, "SUPPLIER": COUNTERPARTY, "CUSTOMER": COUNTERPARTY}


@dataclass(frozen=True)
class ReaderFilterConfig:
    window_days: int = 7  # the run date and the six days before it
    near_duplicate: float = 0.95
    same_event: float = 0.90
    series_publishers: frozenset[str] = SERIES_PUBLISHERS
    priority: PriorityConfig = PriorityConfig()


def _published(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value or ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _dot(first: Sequence[float], second: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(first, second))


def effective_rung(link_rung: str | None, hits: Sequence[Any]) -> tuple[str | None, str | None]:
    """The stronger of the register link and the reader-pack link, and a reason when the reader pack raised it."""
    best, reason = link_rung, None
    for hit in hits:
        if not hit.strong or hit.item_type not in _READER_RUNG:
            continue
        rung = _READER_RUNG[hit.item_type]
        if rung_rank(rung) > rung_rank(best):
            best, reason = rung, f"{rung}: names reader-pack {hit.item_type.lower()} {hit.item_name} as '{hit.matched}' in the {hit.where.lower()}."
    return best, reason


def filter_reader(
    records: Sequence[Mapping[str, Any]],
    *,
    run_date: date,
    vectors: Mapping[str, Sequence[float]],
    config: ReaderFilterConfig = ReaderFilterConfig(),
) -> dict[str, dict[str, Any]]:
    """records carry signal_id, title, publisher, published_at, doc_type (a DocType), link (a Link), hits (ReaderHits) and scale."""
    by_id = {str(record["signal_id"]): record for record in records}
    out: dict[str, dict[str, Any]] = {}
    for signal_id, record in by_id.items():
        rung, raised = effective_rung(record["link"].rung, record["hits"])
        out[signal_id] = {"decision": None, "reason": None, "rung": rung, "rung_reason": raised, "duplicate_of": None, "also_reported_by": [], "priority": None, "corroboration": 1}

    def drop(signal_id: str, reason: str) -> None:
        out[signal_id]["decision"], out[signal_id]["reason"] = DROP, reason

    alive: list[str] = []
    for signal_id, record in by_id.items():
        published = _published(record.get("published_at"))
        age = (run_date - published.date()).days if published is not None else None
        if age is not None and age < 0:
            drop(signal_id, AFTER_RUN_DATE)  # a run dated in the past keeps only what was published by its date
        elif age is not None and age >= config.window_days:
            drop(signal_id, STALE)
        elif record["doc_type"].doc_type in NON_EVENT_TYPES:
            drop(signal_id, NOT_AN_EVENT)
        elif out[signal_id]["rung"] is None and not any(hit.strong for hit in record["hits"]) and not key_gate(record["link"].themes):
            drop(signal_id, NOT_RELEVANT)
        else:
            alive.append(signal_id)

    def strength(signal_id: str) -> tuple[Any, ...]:
        record = by_id[signal_id]
        published = _published(record.get("published_at"))
        strong = [hit for hit in record["hits"] if hit.strong]
        return (
            -rung_rank(out[signal_id]["rung"]),
            -len(strong),
            -(published.timestamp() if published else 0.0),
            signal_id,
        )

    def separate_reports(first: str, second: str) -> bool:
        one, two = by_id[first], by_id[second]
        publisher = str(one.get("publisher") or "").casefold()
        if publisher not in config.series_publishers or publisher != str(two.get("publisher") or "").casefold():
            return False
        days = {(_published(record.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc)).date() for record in (one, two)}
        return len(days) == 2

    kept: list[str] = []
    for signal_id in sorted(alive, key=strength):
        vector = vectors.get(signal_id)
        match = None
        title = str(by_id[signal_id].get("title") or "").strip().casefold()
        for other in kept:
            if separate_reports(signal_id, other):
                continue
            other_vector = vectors.get(other)
            if title and title == str(by_id[other].get("title") or "").strip().casefold():
                match = (other, NEAR_DUPLICATE)
                break
            if vector is not None and other_vector is not None:
                score = _dot(vector, other_vector)
                if score >= config.near_duplicate:
                    match = (other, NEAR_DUPLICATE)
                    break
                if score >= config.same_event:
                    match = (other, SAME_EVENT)
                    break
        if match is None:
            kept.append(signal_id)
            continue
        drop(signal_id, match[1])
        out[signal_id]["duplicate_of"] = match[0]
        out[match[0]]["also_reported_by"].append({"signal_id": signal_id, "url": by_id[signal_id].get("url"), "publisher": by_id[signal_id].get("publisher")})

    for signal_id in kept:
        record = by_id[signal_id]
        publishers = [record.get("publisher"), *(item["publisher"] for item in out[signal_id]["also_reported_by"])]
        cues = record.get("scale") or ScaleCues((), False, (), ())
        priority = assess(rung=out[signal_id]["rung"], themes=record["link"].themes, cues=cues, publishers=publishers, config=config.priority)
        out[signal_id].update(
            decision=KEEP,
            priority=priority.as_record(),
            corroboration=len(outlet_groups([publisher or record.get("url") for publisher in publishers])),
        )
    return out

