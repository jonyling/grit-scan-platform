"""grit daily-review: the daily two-stage review of signals on SharePoint (spec 48)."""

from __future__ import annotations

import argparse
import os
from datetime import date, datetime
from typing import Any

STEP_NAMES = ("ARGUE", "PUBLISH_BP", "MAIL_BP", "CLOSE_BP", "PUBLISH_MANAGER", "MAIL_MANAGER", "CLOSE_MANAGER", "MAIL_FINAL")


def _services(*, need_gemini: bool) -> Any:
    from grit.profiles import load_profiles
    from grit.review.cycle import Services
    from grit.review.graph import GraphClient, GraphError, graph_settings, missing

    services = Services(profiles=load_profiles(), model=os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash")
    gaps = missing(graph_settings(), site=True, mail=True)
    if gaps:
        services.graph_error = "Graph is not configured. Missing: " + ", ".join(gaps)
    else:
        try:
            services.graph = GraphClient.from_env(site=True, mail=True)
        except GraphError as exc:
            services.graph_error, services.graph_failed = str(exc), True
    if need_gemini:
        try:
            from grit.agents.gemini import gemini_client

            services.gemini = gemini_client()
        except RuntimeError as exc:
            services.gemini_error = str(exc)[:200]
    return services


def _now(config: Any, day: date, clock: str | None) -> datetime:
    if clock:
        hours, minutes = clock.split(":")
        return datetime.combine(day, datetime.min.time().replace(hour=int(hours), minute=int(minutes)), tzinfo=config.zone)
    return datetime.now(config.zone)


def _today(config: Any, value: date | None) -> date:
    return value or datetime.now(config.zone).date()


def main(argv: list[str] | None = None) -> int:
    from grit.pool import Pool
    from grit.review.config import STAGES, configured_packs, load_review_config
    from grit.settings import load_local_env

    parser = argparse.ArgumentParser(prog="grit daily-review", description="The daily two-stage review of signals on SharePoint (spec 48).")
    commands = parser.add_subparsers(dest="action", required=True)

    def common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--pack", nargs="+", help="Packs with a config/review/<pack>.json. Default: every one.")
        sub.add_argument("--date", type=date.fromisoformat, help="The working day, YYYY-MM-DD. Default: today in Singapore.")

    tick = commands.add_parser("tick", help="Run every step that is due. Run from cron every 15 minutes.")
    common(tick)
    tick.add_argument("--at", help="Act as if the time were HH:MM in Singapore. For a catch-up or a test.")
    status = commands.add_parser("status", help="Print each step and the decisions so far.")
    common(status)
    step = commands.add_parser("step", help="Run one step now, whatever the time. A done step needs --force.")
    common(step)
    step.add_argument("name", choices=STEP_NAMES)
    step.add_argument("--force", action="store_true")
    decide = commands.add_parser("decide", help="Record a decision without SharePoint, for the pilot or a reviewer who cannot reach the list.")
    common(decide)
    decide.add_argument("--stage", choices=STAGES, required=True)
    decide.add_argument("--signal", required=True)
    decide.add_argument("--decision", choices=("APPROVE", "REJECT"), required=True)
    decide.add_argument("--comment")
    decide.add_argument("--by", required=True, help="The named person recording it, for the audit trail.")
    learn = commands.add_parser("learning", help="Write what a reviewer's decisions have taught GRIT.")
    learn.add_argument("--profile", nargs="+", required=True)
    learn.add_argument("--pack")
    commands.add_parser("columns", help="Print the SharePoint list columns to create.")
    args = parser.parse_args(argv)
    load_local_env()

    if args.action == "columns":
        from grit.review.sharepoint import COLUMNS

        for name, kind, note in COLUMNS:
            print(f"{name:12} {kind:55} {note}")
        return 0
    pool = Pool()
    if args.action == "learning":
        from grit.profiles import load_profiles
        from grit.review.learning import write

        profiles = load_profiles()
        for profile_id in args.profile:
            print(write(pool, profiles[profile_id], pack=args.pack))
        return 0
    packs = args.pack or configured_packs()
    if not packs:
        print("No daily review is configured. Add config/review/<pack>.json.")
        return 1
    status_code = 0
    for pack in packs:
        config = load_review_config(pack)
        day = _today(config, args.date)
        if args.action == "tick":
            from grit.review.cycle import tick as run_tick

            run_tick(pool, config, day, _now(config, day, args.at), _services(need_gemini=True))
        elif args.action == "step":
            status_code = _step(pool, config, day, args.name, args.force) or status_code
        elif args.action == "decide":
            from grit.review.sharepoint import decide_locally

            recorded = decide_locally(pool, config, day, args.stage, args.signal, args.decision, comment=args.comment, actor=f"cli:{args.by}")
            print(f"Recorded {args.decision} on {args.signal} for the {args.stage} stage." if recorded else "Not recorded: the stage is closed or past its deadline.")
            status_code = 0 if recorded else 1
        else:
            _status(pool, config, day)
    return status_code


def _step(pool: Any, config: Any, day: date, name: str, force: bool) -> int:
    from grit.review.batch import mark_step, step_done
    from grit.review.config import batch_id
    from grit.review.cycle import run_step

    batch = batch_id(day, config.pack)
    if step_done(pool, batch, name) and not force:
        print(f"{batch}: {name} is already done. Add --force to run it again.")
        return 1
    outcome = run_step(pool, config, day, name, _services(need_gemini=name == "ARGUE"), datetime.now(config.zone))
    status = {"DONE": "DONE", "NOT_CONFIGURED": "SKIPPED", "UNCERTAIN": "UNCERTAIN"}.get(str(outcome.get("status")), "FAILED")
    mark_step(pool, batch, name, status, outcome)
    print(f"{batch}: {name} {status}. " + ", ".join(f"{key} {value}" for key, value in outcome.items() if key not in {"status", "files", "overflow"}))
    return 0 if status != "FAILED" else 1


def _status(pool: Any, config: Any, day: date) -> None:
    from grit.review.batch import decisions, items, steps
    from grit.review.config import BP, MANAGER, batch_id
    from grit.review.cycle import schedule

    batch = batch_id(day, config.pack)
    done = steps(pool, batch)
    print(f"{batch} ({'working day' if config.working_day(day) else 'not a working day'}; covers {', '.join(str(d) for d in config.covered_days(day))})")
    for step, due, _after in schedule(config):
        row = done.get(step)
        print(f"  {due:%H:%M} {step:16} {row['status'] + ' at ' + row['done_at'] if row else 'pending'}")
    for stage in (BP, MANAGER):
        published = items(pool, batch, stage)
        counts: dict[str, int] = {}
        for row in decisions(pool, batch, stage).values():
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
        print(f"  {stage}: {len(published)} row(s), {sum(1 for row in published if row['list_item_id'])} on SharePoint; decisions {counts or 'none'}")
