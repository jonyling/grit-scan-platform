"""grit daily, grit index and grit feeds (spec 42)."""

from __future__ import annotations

import argparse
from datetime import date


def daily_main(argv: list[str] | None = None) -> int:
    from grit.daily.feeds import active
    from grit.daily.index import nightly
    from grit.daily.lane import daily_packs, run_daily
    from grit.pool import Pool
    from grit.profiles import load_profiles
    from grit.settings import load_local_env

    parser = argparse.ArgumentParser(prog="grit daily", description="Run the daily lane for each pack, then the nightly index. Run it once a day, for example from cron.")
    parser.add_argument("--pack", nargs="+", help="Packs. Default: every pack an active feed serves, and the Group pack.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Day, YYYY-MM-DD. Default today.")
    parser.add_argument("--lookback-days", type=int, default=2, help="Read feed items published this many days before the day. Use 7 to catch up.")
    parser.add_argument("--no-currents", action="store_true", help="Read the feeds only.")
    parser.add_argument("--dry-run", action="store_true", help="Print the packs and feeds. Fetch nothing.")
    args = parser.parse_args(argv)
    load_local_env()
    packs = daily_packs(args.pack)
    if args.dry_run:
        for pack in packs:
            print(f"{pack}: feeds {', '.join(feed.feed_id for feed in active([pack])) or 'none'}" + ("" if args.no_currents else "; Currents: one query per standing condition and active request"))
        return 0
    pool = Pool()
    profiles = list(load_profiles().values())
    for pack in packs:
        print(f"== {pack} daily, {args.date}")
        result = run_daily(pool, pack, args.date, use_currents=not args.no_currents, lookback_days=args.lookback_days, profiles=profiles)
        print(f"  {result.signals} pages ({result.new_signals} new), {result.kept} kept, {result.queued} queued for the next scan, {result.red_drafts} RED drafts.")
    index = nightly(pool, args.date)
    print(f"Index: {index['fts_added']} pages indexed; events {index['events']}; digests {index['digests']}; {index['retained']} old dropped pages trimmed.")
    return 0


def index_main(argv: list[str] | None = None) -> int:
    from grit.daily.index import nightly
    from grit.pool import Pool

    parser = argparse.ArgumentParser(prog="grit index", description="Run the nightly index only: full text, events, digests and retention.")
    parser.add_argument("--date", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)
    print(nightly(Pool(), args.date))
    return 0


def feeds_main(argv: list[str] | None = None) -> int:
    from grit.daily.feeds import FEEDS, check_feeds

    parser = argparse.ArgumentParser(prog="grit feeds", description="List the daily feed register, or check every feed.")
    parser.add_argument("--check", action="store_true", help="Read every feed now and report its newest item.")
    args = parser.parse_args(argv)
    if args.check:
        for row in check_feeds():
            print(f"{row['status']:8} {row['feed']:18} items {row['items']:>4}  newest {row['newest'] or '-':20}  {row['error'] or ''}")
        return 0
    for feed in FEEDS:
        print(f"{feed.status:8} {feed.feed_id:18} packs {','.join(feed.packs):8} {feed.name}. Basis: {feed.basis}." + (f" {feed.note}" if feed.note else ""))
    return 0
