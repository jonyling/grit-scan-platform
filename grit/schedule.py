"""Run the weekly scans that are due today.

`grit scheduled` runs on Mondays. `SCHEDULE` is the cadence, and `tests/test_schedule.py` locks the planned Brave counts:

| Pack | Due | Planned queries a run |
|---|---|---|
| dsc | Every Monday | 124 |
| urs | First and third Monday | 117 |
| urs-tw | First Monday | 60 |
| ste | First Monday | 48 |

The schedule test adds one 16-query source sweep for the month. A five-Monday month of these packs stays at or under 1,000 queries before the adverse scan. Commercial Aerospace (`ca`) and Marine (`marine`) stay off `SCHEDULE`. Run them with `grit weekly --pack` when you accept the extra search cost.

Packs due on the same day run in one process, entity packs first. Every Monday, after those scans, the adverse news scan can send up to 40 more Brave queries. That extra spend sits outside the 1,000-query check. Raise `BRAVE_MONTHLY_LIMIT` to match the Brave plan, or pass `--no-adverse`.
"""

from __future__ import annotations

import argparse
from datetime import date

WEEKLY, FORTNIGHTLY, MONTHLY = "weekly", "fortnightly", "monthly"
SCHEDULE = {"dsc": WEEKLY, "urs": FORTNIGHTLY, "urs-tw": MONTHLY, "ste": MONTHLY}


def due_packs(day: date, schedule: dict[str, str] | None = None) -> list[str]:
    """The packs due on a day. Every cadence runs on Mondays only."""
    if day.weekday() != 0:
        return []
    due = []
    for pack, cadence in (schedule or SCHEDULE).items():
        if cadence == WEEKLY or (cadence == FORTNIGHTLY and (day.day <= 7 or 15 <= day.day <= 21)) or (cadence == MONTHLY and day.day <= 7):
            due.append(pack)
    return due


def adverse_due(day: date) -> bool:
    """The adverse news scan (spec 46) runs every Monday, after the weekly scans due that day, so it reads their pages."""
    return day.weekday() == 0


def main(argv: list[str] | None = None) -> int:
    from grit.cli import main as grit_main

    parser = argparse.ArgumentParser(prog="grit scheduled", description="Run the weekly scans due today under the Brave-saving schedule.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dry-run", action="store_true", help="Print the packs due and stop.")
    parser.add_argument("--no-adverse", action="store_true", help="Leave out the Monday adverse news scan.")
    args = parser.parse_args(argv)
    due = due_packs(args.date)
    adverse = adverse_due(args.date) and not args.no_adverse
    print(f"{args.date} ({args.date.strftime('%A')}): " + (f"due {', '.join(due)}." if due else "no scan due.") + (" Adverse news scan due." if adverse else ""))
    if args.dry_run:
        return 0
    status = grit_main(["weekly", "--pack", *due, "--date", args.date.isoformat()]) if due else 0
    if adverse:
        status = grit_main(["adverse", "--date", args.date.isoformat(), "--quiet"]) or status
    return status
