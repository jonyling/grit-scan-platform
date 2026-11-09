"""The Thursday scan schedule."""

from __future__ import annotations

from datetime import date, timedelta

from grit.schedule import due_packs

# High end of the name rotation on the plans as of 9 November 2026.
PLANNED = {"dsc": 139, "urs": 151, "urs-tw": 60, "ste": 52, "ca": 92, "marine": 69}
FIRST = ["dsc", "urs", "urs-tw", "ste", "ca", "marine"]


def test_scans_run_on_thursdays_only_at_their_cadence() -> None:
    assert due_packs(date(2026, 11, 5)) == FIRST, "the first Thursday of November"
    assert due_packs(date(2027, 1, 7)) == FIRST, "a first Thursday that falls on the 7th"
    assert due_packs(date(2026, 11, 12)) == ["urs"]
    assert due_packs(date(2026, 11, 19)) == ["urs"]
    assert due_packs(date(2026, 11, 26)) == ["urs"]
    assert due_packs(date(2026, 10, 29)) == ["urs"], "a fifth Thursday adds no monthly scan"
    assert due_packs(date(2026, 11, 2)) == [], "no scan on Monday"
    assert due_packs(date(2026, 11, 9)) == []


def test_a_year_of_the_schedule_stays_within_the_planned_query_ceiling() -> None:
    spent = []
    for month in range(1, 13):
        days = [date(2027, month, 1) + timedelta(days=offset) for offset in range(31)]
        spent.append(sum(PLANNED[pack] for day in days if day.month == month for pack in due_packs(day)) + 16)
    assert min(spent) == 1032, "a four-Thursday month at the high end of each pack, plus the source sweep"
    assert max(spent) == 1183, "a five-Thursday month; above the free Brave credit, under the default monthly guard"
