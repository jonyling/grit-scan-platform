"""Persist and surface earlier cases named by a WSH Council bulletin (spec 47)."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from grit.pool import Pool

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)
_MONTH_RE = "|".join(_MONTHS)


def parse_named_dates(text: str) -> list[str]:
    """Extract month-year or ISO dates that a bulletin uses for earlier cases. Empty when none named."""
    found: list[str] = []
    folded = text or ""
    for match in re.finditer(rf"\b({_MONTH_RE})\s+(\d{{4}})\b", folded, flags=re.I):
        month = match.group(1).casefold()
        year = match.group(2)
        index = _MONTHS.index(month) + 1
        stamp = f"{year}-{index:02d}"
        if stamp not in found:
            found.append(stamp)
    for match in re.finditer(r"\b(20\d{2})-(\d{2})-(\d{2})\b", folded):
        stamp = match.group(0)
        if stamp not in found:
            found.append(stamp)
    return found


def store_from_selection(
    pool: Pool,
    run_id: str,
    *,
    signal_id: str,
    hazard: str,
    earlier_cases: str,
    mechanism: str = "",
) -> list[int]:
    """Store precedents only when earlier_cases names a date. Keyword matching across old accidents is forbidden."""
    if not earlier_cases or earlier_cases.strip().casefold() in {"", "not established", "none"}:
        return []
    if not hazard or hazard.strip().casefold() in {"", "not established"}:
        return []
    dates = parse_named_dates(earlier_cases)
    if not dates:
        return []
    mech = (mechanism or earlier_cases).strip()[:240] or "named by bulletin"
    ids: list[int] = []
    for earlier_date in dates:
        ids.append(
            pool.add_wsh_precedent(
                hazard=hazard.strip(),
                mechanism=mech,
                earlier_date=earlier_date,
                stated_by=signal_id,
                run_id=run_id,
            )
        )
    return ids


def surface_for_note(pool: Pool, hazard: str, *, exclude_signal: str | None = None) -> list[str]:
    """Lines for a note with the same hazard. Only rows a prior bulletin stated."""
    if not hazard or hazard.strip().casefold() in {"", "not established"}:
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for row in pool.wsh_precedents_for_hazard(hazard):
        if exclude_signal and row.get("stated_by") == exclude_signal:
            continue
        key = f"{row.get('earlier_date')}|{row.get('mechanism')}"
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"A prior WSH Council bulletin named a similar case ({row.get('earlier_date')}: {row.get('mechanism')})."
        )
    return lines
