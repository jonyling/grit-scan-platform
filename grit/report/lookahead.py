"""Look-ahead for brief format v2 (spec 44): dated events in the next LOOK_AHEAD_DAYS, taken by rule from the week's kept
sources. A date is kept only when a source states it, as a day and a month, such as "3 November" or "Nov 3". The row is
the source's own sentence, so nothing is inferred and nothing is predicted.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

LOOK_AHEAD_DAYS = 30
MAX_ROWS = 15
MAX_SENTENCE = 320
TEXT_CHARS = 4000
_MONTHS = {name: number for number, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"), ("jul", "july"),
     ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")], start=1) for name in names}
_MONTH = r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
_DAY_FIRST = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}(?:,?\s+(\d{{4}}))?\b")
_MONTH_FIRST = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?")
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_WORDS = re.compile(r"[a-z0-9]+")


def _date(day: str, month: str, year: str | None, run_date: date) -> date | None:
    number = _MONTHS.get(month.casefold().rstrip("."))
    if number is None:
        return None
    try:
        found = date(int(year) if year else run_date.year, number, int(day))
    except ValueError:
        return None
    if not year and found < run_date - timedelta(days=180):
        found = date(found.year + 1, found.month, found.day)  # "5 January" read in late October is next year
    return found


def dates_in(sentence: str, run_date: date) -> list[date]:
    found = [_date(m.group(1), m.group(2), m.group(3), run_date) for m in _DAY_FIRST.finditer(sentence)]
    found += [_date(m.group(2), m.group(1), m.group(3), run_date) for m in _MONTH_FIRST.finditer(sentence)]
    return sorted({item for item in found if item})


def _words(text: str) -> set[str]:
    return set(_WORDS.findall(text.casefold()))


def look_ahead(rows: Sequence[Mapping[str, Any]], run_date: date, *, days: int = LOOK_AHEAD_DAYS, relevance: Any = None,
               event_of: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """rows: kept signals with title, text, published_at and signal_id; the caller passes only rows linked to the business
    area. relevance(row) gives the row's relevance text. Two sentences with the same date are one row when they share most
    of their words or come from the same event (event_of: signal id -> event id). Returns [{date, event, signal_ids,
    relevance}], earliest first."""
    end = run_date + timedelta(days=days)
    found: list[dict[str, Any]] = []
    for row in rows:
        text = f"{row.get('title') or ''}. {str(row.get('text') or '')[:TEXT_CHARS]}"
        for sentence in _SENTENCES.split(" ".join(text.split())):
            sentence = sentence.strip()
            if len(sentence) > MAX_SENTENCE or len(sentence) < 25:
                continue
            for when in dates_in(sentence, run_date):
                if not run_date < when <= end:
                    continue
                words = _words(sentence)
                event = (event_of or {}).get(row["signal_id"])
                same = next((item for item in found if item["date"] == when and (
                    len(words & item["words"]) >= 0.6 * min(len(words), len(item["words"])) or (event and item["event_id"] == event))), None)
                if same:
                    same["signal_ids"].append(row["signal_id"])
                    continue
                found.append({"date": when, "event": sentence, "words": words, "event_id": event, "signal_ids": [row["signal_id"]], "tier": row.get("tier") or 9,
                              "relevance": relevance(row) if relevance else ""})
    found.sort(key=lambda item: (item["date"], item["tier"], -len(item["signal_ids"])))
    return [{key: value for key, value in item.items() if key not in {"words", "event_id"}} for item in found[:MAX_ROWS]]
