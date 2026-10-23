"""The Brave-saving scan schedule."""

from __future__ import annotations

from datetime import date, timedelta

from grit.schedule import due_packs

PLANNED = {"dsc": 124, "urs": 117, "urs-tw": 60, "ste": 48}


def test_scans_run_on_mondays_only_at_their_cadence() -> None:
    assert due_packs(date(2026, 11, 2)) == ["dsc", "urs", "urs-tw", "ste"], "the first Monday of November"
    assert due_packs(date(2026, 11, 9)) == ["dsc"]
    assert due_packs(date(2026, 11, 16)) == ["dsc", "urs"], "the third Monday"
    assert due_packs(date(2026, 11, 30)) == ["dsc"], "a fifth Monday adds no URS scan"
    assert due_packs(date(2026, 11, 3)) == [], "no scan on other days"


def test_a_year_of_the_schedule_stays_within_the_free_brave_credit() -> None:
    for month in range(1, 13):
        days = [date(2027, month, 1) + timedelta(days=offset) for offset in range(31)]
        spent = sum(PLANNED[pack] for day in days if day.month == month for pack in due_packs(day)) + 16
        assert spent <= 1000, (month, spent)
