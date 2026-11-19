"""The QA log blocks what it withholds.

A draft that failed its checks is withheld, and the QA log names the date or figure that failed ("'30 November' is not
on the cited page(s) S1"). That date or figure must not reach the reader by another route: any row, sentence or
look-ahead entry that rests on the withheld item's own reports, unless a page that text cites states it. This module
finds such leaks so a report that has one is not released. The same date about a different event, from different
reports, is not a leak, and neither is a date another report of the same event gives.
"""

from __future__ import annotations

import re
from typing import Any, Iterator, Mapping

# The parts of a brief a reader does not see as findings: the QA log itself, run metadata, and citations. A citation's
# date is when its source was published, not a claim. "plain" repeats the prose of "runs" with its citations inline.
SKIP_KEYS = frozenset({"qa", "method", "meta", "membership", "evidence_ids", "content_hash", "tiers", "sources",
                       "unrated_outlets", "gate", "completeness", "corrections", "cite", "plain"})
ID_KEYS = ("signal_id", "signal_ids", "source_ids", "support_ids", "lead")
_FAILED = re.compile(r"(?:UNSUPPORTED_DATE|UNSUPPORTED_NUMBER|UNGROUNDED_NUMBER)\s*:\s*[^:]*:\s*'?(.+?)'? is not (?:on|in) the cited")
_SIGNAL = re.compile(r"SIG-[0-9a-f]{16}")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_DAY_MONTH = re.compile(r"^(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3})[a-z]*\.?$|^([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})$")
# A bare one- or two-digit number also counts list positions and risk numbers, so only longer figures are policed.
MIN_FIGURE_CHARS = 3


def withheld_terms(qa: Any) -> list[dict[str, Any]]:
    """[{item, terms, signal_ids}] for each withheld draft the QA log names with its reports and failed dates or figures."""
    found: list[dict[str, Any]] = []
    for row in qa or []:
        if not isinstance(row, Mapping) or row.get("origin") != "CHECK":
            continue
        correction = row.get("correction")
        text = str(correction.get("plain") if isinstance(correction, Mapping) else correction or "")
        if "withheld" not in text.casefold():
            continue
        terms = [term.strip() for term in _FAILED.findall(text)]
        terms = list(dict.fromkeys(term for term in terms if _date(term) or len(term) >= MIN_FIGURE_CHARS))
        signal_ids = set(_ids(row.get("source_ids"))) | set(_ids(correction))
        if terms and signal_ids:
            found.append({"item": str(row.get("item") or "withheld draft"), "terms": terms, "signal_ids": signal_ids})
    return found


def _date(term: str) -> tuple[int, str] | None:
    match = _DAY_MONTH.match(term.strip())
    if not match:
        return None
    day, month = (match.group(1), match.group(2)) if match.group(1) else (match.group(4), match.group(3))
    month = month[:3].casefold()
    return (int(day), month) if month in MONTHS else None


def _pattern(term: str) -> re.Pattern[str]:
    parsed = _date(term)
    if parsed:
        day, month = parsed
        return re.compile(rf"(?<!\d){day}(?:st|nd|rd|th)?\s+{month}[a-z]*\.?(?![a-z])|(?<![a-z]){month}[a-z]*\.?\s+{day}(?!\d)", re.IGNORECASE)
    return re.compile(rf"(?<![\w.,]){re.escape(term)}(?![\w]|[.,]\d)", re.IGNORECASE)


def _ids(value: Any) -> Iterator[str]:
    """Every signal id in a value, citations included."""
    if isinstance(value, str):
        yield from _SIGNAL.findall(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _ids(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _ids(item)


def _strings(value: Any, path: str, context: frozenset[str]) -> Iterator[tuple[str, str, frozenset[str]]]:
    """(path, text, the reports the text rests on). A row, or a field with its own citations, sets the context."""
    if isinstance(value, str):
        yield path, value, context
    elif isinstance(value, Mapping):
        if "runs" in value or any(key in value for key in ID_KEYS):
            context = frozenset(_ids(value))
        for key, item in value.items():
            if key not in SKIP_KEYS:
                yield from _strings(item, f"{path}.{key}" if path else str(key), context)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _strings(item, f"{path}[{index}]", context)


def leaks(brief: Mapping[str, Any], pages: Mapping[str, str] | None = None) -> list[str]:
    """Each place a withheld date or figure reappears in text resting on the withheld item's own reports, where no page
    the text cites states it. pages: {signal_id: title and text}. A page not supplied states nothing."""
    pages = pages or {}
    withheld = withheld_terms(brief.get("qa"))
    if not withheld:
        return []
    v2 = brief.get("v2") if isinstance(brief.get("v2"), Mapping) else {}
    # The status line's dated items are the first look-ahead entries, in order, without their ids.
    window = [(f"v2.risk_window[{index}]", text, frozenset(_ids(entry)))
              for index, (text, entry) in enumerate(zip(v2.get("risk_window") or [], v2.get("look_ahead") or []))]
    found: list[str] = []
    for path, text, context in [*_strings(brief, "", frozenset()), *window]:
        for row in withheld:
            if not context & row["signal_ids"]:
                continue
            for term in row["terms"]:
                pattern = _pattern(term)
                if pattern.search(text) and not any(pattern.search(pages.get(key) or "") for key in context):
                    found.append(f"'{term}', withheld for {row['item']}, appears in {path}.")
    return list(dict.fromkeys(found))
