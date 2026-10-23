"""grit scheduled: the weekly scans that are due today, run one after another (README, "Run the scans on a schedule").

The schedule keeps Brave within its free credit of about 1,000 queries a month:

| Pack | Due | Brave queries a month |
|---|---|---|
| dsc | every Monday | 496 to 620 (four or five Mondays) |
| urs | the first and third Monday of the month | 234 |
| urs-tw | the first Monday of the month | about 60 |
| ste | the first Monday of the month | 48 |

With the 16-query source sweep, the heaviest month (five Mondays) comes to about 978 queries. On other days nothing is
scanned with Brave; the daily lane still runs. Packs due on the same day run in one process, entity
packs first, so they never write the pool at the same time.
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


def main(argv: list[str] | None = None) -> int:
    from grit.cli import main as grit_main

    parser = argparse.ArgumentParser(prog="grit scheduled", description="Run the weekly scans due today under the Brave-saving schedule.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--dry-run", action="store_true", help="Print the packs due and stop.")
    args = parser.parse_args(argv)
    due = due_packs(args.date)
    print(f"{args.date} ({args.date.strftime('%A')}): " + (f"due {', '.join(due)}." if due else "no scan due."))
    if not due or args.dry_run:
        return 0
    return grit_main(["weekly", "--pack", *due, "--date", args.date.isoformat()])
