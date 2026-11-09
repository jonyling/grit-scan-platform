"""Run the scans that are due today.

`grit scheduled` runs on Thursdays. `SCHEDULE` is the cadence, and `tests/test_schedule.py` locks the planned Brave counts. A run's count moves with the names due that week. These figures are the high end of that rotation on the plans as of 9 November 2026:

| Pack | Due | Planned queries a run |
|---|---|---|
| dsc | First Thursday | 139 |
| urs | Every Thursday | 151 |
| urs-tw | First Thursday | 60 |
| ste | First Thursday | 52 |
| ca | First Thursday | 92 |
| marine | First Thursday | 69 |

The schedule test adds one 16-query source sweep for the month. A four-Thursday month of these high-end counts is 1,032 queries. A five-Thursday month is 1,183, above Brave's free credit of about 1,000, before the adverse scan. `BRAVE_MONTHLY_LIMIT` defaults to 2000.

Packs due on the same day run in one process, entity packs first. Every Thursday, after those scans, the adverse news scan can send up to 40 more Brave queries. That extra spend sits outside the planned-count check. Raise `BRAVE_MONTHLY_LIMIT` to match the Brave plan, or pass `--no-adverse`.
"""

from __future__ import annotations

import argparse
from datetime import date

WEEKLY, FORTNIGHTLY, MONTHLY = "weekly", "fortnightly", "monthly"
THURSDAY = 3
SCHEDULE = {"dsc": MONTHLY, "urs": WEEKLY, "urs-tw": MONTHLY, "ste": MONTHLY, "ca": MONTHLY, "marine": MONTHLY}


def due_packs(day: date, schedule: dict[str, str] | None = None) -> list[str]:
    """The packs due on a day. Every cadence runs on Thursdays only."""
    if day.weekday() != THURSDAY:
        return []
    due = []
    for pack, cadence in (schedule or SCHEDULE).items():
        if cadence == WEEKLY or (cadence == FORTNIGHTLY and (day.day <= 7 or 15 <= day.day <= 21)) or (cadence == MONTHLY and day.day <= 7):
            due.append(pack)
    return due


def adverse_due(day: date) -> bool:
    """The adverse news scan (spec 46) runs every Thursday, after the scans due that day, so it reads their pages."""
    return day.weekday() == THURSDAY


def main(argv: list[str] | None = None) -> int:
    from grit.cli import main as grit_main

    parser = argparse.ArgumentParser(prog="grit scheduled", description="Run the scans due today under the Thursday schedule.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dry-run", action="store_true", help="Print the packs due and stop.")
    parser.add_argument("--no-adverse", action="store_true", help="Leave out the Thursday adverse news scan.")
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
