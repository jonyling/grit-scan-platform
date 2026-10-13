"""GRIT-Scan command line. Run from the repository root:

    .venv/bin/python -m grit --help

The reader commands work on the signal pool (output/grit.sqlite, or GRIT_DB). The digest commands run the earlier
business-area digest on run folders under output/brave-plan/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import Any, Callable

from grit.settings import OUTPUT_DIR, load_local_env

DEFAULT_MODEL = "gemini-2.5-flash"


def _pool() -> Any:
    from grit.pool import Pool

    return Pool()


def _model(value: str | None) -> str:
    return value or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL


def _client() -> Any:
    from grit.agents.gemini import gemini_client

    return gemini_client()


def _packs(args: argparse.Namespace) -> list[str]:
    """The packs named with --pack, or every weekly pack with a scan plan, entity packs first. An unknown pack stops the
    command before any run is registered."""
    from grit.ontology.pack import load_ontology_pack, manifests, scanned_packs
    from grit.settings import CONFIG_DIR

    if not args.pack:
        # A monthly pack, such as the Group pack (spec 37), runs only when named.
        return [name for name in scanned_packs() if int((load_ontology_pack(CONFIG_DIR, name).scan_plan or {}).get("windowDays") or 8) <= 10]
    known = manifests()
    unknown = [name for name in args.pack if name not in known]
    if unknown:
        raise SystemExit(f"Unknown pack(s): {', '.join(unknown)}. Known packs: {', '.join(sorted(known))}")
    return list(args.pack)


def _run(pool: Any, args: argparse.Namespace, pack: str) -> Any:
    from grit.pipeline.reader_scan import new_run

    return new_run(pool, args.date, pack)


def _for_each_pack(step: Callable[[Any, argparse.Namespace, str], int]) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        pool = _pool()
        for pack in _packs(args):
            print(f"== {pack}")
            code = step(pool, args, pack)
            if code:
                return code
        return 0

    run.__doc__ = step.__doc__
    return run


def _profiles(names: list[str] | None) -> list[Any]:
    from grit.profiles import load_profiles

    profiles = load_profiles()
    if not names or names == ["all"]:
        return list(profiles.values())
    missing = [name for name in names if name not in profiles]
    if missing:
        raise SystemExit(f"Unknown profile(s): {', '.join(missing)}. Known: {', '.join(sorted(profiles))}")
    return [profiles[name] for name in names]


# Reader pipeline -------------------------------------------------------------------------------------------------


def _sweep(pool: Any, run: Any, count: int) -> list[Any]:
    """Due queries from the business-area 12-week plan, folded into this run so their pages go through the same rules."""
    from grit.ontology.pack import load_ontology_pack
    from grit.ontology.query_plan import QuerySpec, build_query_plan, select_queries
    from grit.pipeline.reader_scan import _aliases
    from grit.settings import CONFIG_DIR

    pack = load_ontology_pack(CONFIG_DIR, run.pack)
    if count <= 0 or pack.level != "BA" or not pack.keyword_library:
        return []
    selection = select_queries(build_query_plan(pack, _aliases()), run.run_date, count)
    return [QuerySpec(**{**record, "sources": tuple(record["sources"])}) for record in selection["selected"]]


def plan_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    from grit.pipeline.reader_scan import ReaderRun, plan, run_id_for

    specs = plan(pool, ReaderRun(run_id_for(args.date, pack), args.date, pack))  # a preview registers no run
    lanes: dict[str, int] = {}
    for spec in specs:
        lanes[spec.lane] = lanes.get(spec.lane, 0) + 1
    print(f"Reader plan for {pack}: {len(specs)} queries this week. " + ", ".join(f"{lane} {count}" for lane, count in lanes.items()))
    for spec in specs[: args.list]:
        print(f"  {spec.lane:10} {str(spec.sources[0].get('target')):24} {spec.query}")
    return 0


def scan_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    """Researcher, embed, and the deterministic assessment."""
    from grit.pipeline import reader_scan

    run = _run(pool, args, pack)
    specs = reader_scan.plan(pool, run) + _sweep(pool, run, getattr(args, "sweep", 0) or 0)
    if args.dry_run:
        chosen = reader_scan._balanced(specs, args.limit_queries) if args.limit_queries else specs
        print(f"Dry run. {len(chosen)} of {len(specs)} queries would be sent to Brave News for run {run.run_id}. Nothing was sent.")
        for spec in chosen:
            print(f"  {spec.lane:10} {spec.query}")
        return 0
    print(f"Run {run.run_id}: searching.")
    found = reader_scan.acquire(pool, run, specs, limit=args.limit_queries, currents_queries=args.currents or [])
    print(f"Searched {found['queries_sent']} queries ({found['skipped_answered']} already answered). {found['results']} results, {found['signals_new']} new pages. Stopped: {found['stopped'] or 'no'}.")
    if not args.no_embed:
        print(f"Embedding: {reader_scan.embed(pool, run)}")
    print(f"Assessed: {reader_scan.assess(pool, run)}")
    return 0


def assess_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    from grit.pipeline import reader_scan

    print(reader_scan.assess(pool, _run(pool, args, pack)))
    return 0


def agents_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    from grit.pipeline import reader_scan

    run = _run(pool, args, pack)
    client, model = _client(), _model(args.model)
    print(f"Run {run.run_id}: agents on {model}.")
    print(reader_scan.run_agents(pool, run, client=client, model=model))
    if args.findings:
        search = None
        if args.follow_ups:
            from grit.agents.loop.run import brave_search

            search = brave_search(run.folder)
        print(reader_scan.findings(pool, run, client=client, model=model, top=args.findings, search=search))
    pool.finish_run(run.run_id)
    return 0


def report_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    from grit.profiles import load_profiles
    from grit.report.package import write_pack
    from grit.report.red import draft_red_alerts

    run = _run(pool, args, pack)
    if (pool.run(run.run_id) or {}).get("status") != "COMPLETE":
        pool.finish_run(run.run_id)
    for profile in _profiles(args.profile):
        result = write_pack(pool, run.run_id, profile, submit=not args.no_submit)
        print(json.dumps({key: value for key, value in result.items() if key != "package"} | {"package": {k: v for k, v in (result.get("package") or {}).items() if k != "members"}}, indent=2, default=str))
    drafts = draft_red_alerts(pool, run.run_id, pack, list(load_profiles().values()), run.folder / "alerts")
    print(f"RED drafts written: {len(drafts)}")
    return 0


def baseline_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    """Draft each standing condition's current position for sign-off (spec 37)."""
    from grit.pipeline.group import draft_baseline
    from grit.pipeline.reader_scan import ReaderRun, run_id_for

    from grit.pipeline.reader_scan import _load_pack

    if _load_pack(pack).level != "GROUP":
        print(f"Skipped {pack}: this command is for Group packs.")
        return 0
    run = ReaderRun(run_id_for(args.date, pack), args.date, pack)
    if pool.run(run.run_id) is None:
        raise SystemExit(f"No run {run.run_id}. Run grit weekly --pack {pack} --date {args.date} first.")
    print(json.dumps(draft_baseline(pool, run, client=_client(), model=_model(args.model)), indent=2))
    return 0


def scenarios_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    """Propose scenarios for each strategic shift, for a person to review (spec 37)."""
    from grit.pipeline.group import propose_scenarios
    from grit.pipeline.reader_scan import ReaderRun, run_id_for

    from grit.pipeline.reader_scan import _load_pack

    if _load_pack(pack).level != "GROUP":
        print(f"Skipped {pack}: this command is for Group packs.")
        return 0
    run = ReaderRun(run_id_for(args.date, pack), args.date, pack)
    if pool.run(run.run_id) is None:
        raise SystemExit(f"No run {run.run_id}. Run grit weekly --pack {pack} --date {args.date} first.")
    print(json.dumps(propose_scenarios(pool, run, client=_client(), model=_model(args.model)), indent=2))
    return 0


cmd_plan = _for_each_pack(plan_step)
cmd_baseline = _for_each_pack(baseline_step)
cmd_scenarios = _for_each_pack(scenarios_step)
cmd_scan = _for_each_pack(scan_step)
cmd_assess = _for_each_pack(assess_step)
cmd_agents = _for_each_pack(agents_step)
cmd_report = _for_each_pack(report_step)


def cmd_weekly(args: argparse.Namespace) -> int:
    """Everything for each reader pack and one week: scan, agents, findings, packs, and RED drafts. Entity packs run first,
    so a business-area brief can summarise them."""
    if args.dry_run:
        return cmd_scan(args)
    pool = _pool()
    for pack in _packs(args):
        print(f"== {pack}")
        for step in (scan_step, agents_step, report_step):
            code = step(pool, args, pack)
            if code:
                return code
    return 0


# Review, memory, requests, AIRIS -----------------------------------------------------------------------------------


def cmd_feedback(args: argparse.Namespace) -> int:
    pool = _pool()
    _profiles([args.profile])
    pool.add_feedback(args.profile, args.signal, args.decision, args.rationale)
    print(f"Recorded {args.decision} by {args.profile} on {args.signal}.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    from grit.workflow.packages import review_package

    pool = _pool()
    reviewer = _profiles([args.profile])[0]
    result = review_package(pool, package_id=args.package, version=args.version, reviewer=reviewer, intent=args.intent)
    print(f"{result['outcome']}: package is now {result['status']}.")
    return 0


def cmd_requests(args: argparse.Namespace) -> int:
    from grit.pool import utc_now

    pool = _pool()
    if args.action == "list":
        for row in pool.management_requests():
            print(f"{row['request_id']:14} {row['status']:9} {row['origin']:9} {row['requested_by']:14} {row['title']}  keywords={row['keywords']}")
        return 0
    if args.action == "add":
        request_id = f"MR-{utc_now()[:10]}-{len(pool.management_requests()) + 1:03d}"
        pool.insert("management_requests", {
            "request_id": request_id, "title": args.title, "description": args.description, "requested_by": args.by, "request_date": utc_now()[:10], "kind": args.kind,
            "keywords_json": [item.strip() for item in args.keywords.split(",") if item.strip()], "country": args.country, "lob": args.lob, "status": "PROPOSED", "origin": "DASHBOARD" if args.by else "CHAT",
        })
        print(f"Added {request_id} as PROPOSED. A reviewer activates it with: python -m grit requests activate --id {request_id} --profile wilson")
        return 0
    reviewer = _profiles([args.profile])[0]
    if reviewer.review_role not in {"REVIEWER", "BA_HEAD"}:
        raise SystemExit(f"{reviewer.label} cannot activate a request.")
    status = "ACTIVE" if args.action == "activate" else "CLOSED"
    with pool.transaction() as conn:
        conn.execute("UPDATE management_requests SET status = ? WHERE request_id = ?", (status, args.id))
    pool.log(actor_type="FUNCTION", actor="ManagementRequestGate", status=status, subject=args.id, detail={"by": reviewer.profile_id})
    print(f"{args.id} is {status}.")
    return 0


def cmd_airis(args: argparse.Namespace) -> int:
    from grit.report.airis import export_to_airis, preview

    pool = _pool()
    head = _profiles([args.profile])[0]
    if args.dry_run:
        rows = preview(pool, head)
        print(f"{len(rows)} signal(s) ready for AIRIS.")
        for row in rows:
            print(f"  {row['signal_id']} {row['title']}")
        return 0
    print(json.dumps(export_to_airis(pool, head), indent=2, default=str))
    return 0


def dataverse_step(pool: Any, args: argparse.Namespace, pack: str) -> int:
    from grit.dataverse.sync import plan_sync, push
    from grit.ontology.pack import load_ontology_pack
    from grit.pipeline.reader_scan import standing_items
    from grit.settings import CONFIG_DIR

    run_id = f"{args.date.isoformat()}-{pack}"
    names = {item["id"]: item["name"] for item in standing_items(load_ontology_pack(CONFIG_DIR, pack))}
    bodies = plan_sync(pool, run_id, names)
    if not args.push:
        print(f"Dry run. {len(bodies)} kept signal(s) from {run_id} would be created or patched in grit_signals. Add --push to write.")
        for _signal_id, body in bodies[:10]:
            print(f"  {body['grit_headline'][:40]:40} | {body['grit_signalname'][:70]}")
        return 0
    from grit.dataverse.client import DataverseClient

    print(push(pool, run_id, bodies, client=DataverseClient.from_env()))
    return 0


cmd_dataverse = _for_each_pack(dataverse_step)


def cmd_db(args: argparse.Namespace) -> int:
    pool = _pool()
    print(f"Pool: {pool.path}")
    for table, count in pool.counts().items():
        print(f"  {table:22} {count}")
    return 0


def cmd_ui(_args: argparse.Namespace) -> int:
    import subprocess

    from grit.settings import ROOT

    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(ROOT / "grit" / "ui" / "app.py")])


# Ontology and the business-area digest ---------------------------------------------------------------------------


def _delegate(target: Callable[..., Any], prog: str) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        sys.argv = [prog, *args.rest]
        result = target(args.rest) if _takes_argv(target) else target()
        return int(result or 0)

    return run


def _takes_argv(target: Callable[..., Any]) -> bool:
    import inspect

    return "argv" in inspect.signature(target).parameters


def _delegates() -> dict[str, tuple[str, Callable[[], Callable[..., Any]], str]]:
    return {
        "pack-build": ("Rebuild a business-area pack's registers from its workbook.", lambda: __import__("grit.ontology.build_pack", fromlist=["_main"])._main, "grit pack-build"),
        "pack-show": ("Print what a reader pack contains.", lambda: __import__("grit.ontology.pack", fromlist=["_main"])._main, "grit pack-show"),
        "pack-init": ("Write the Group pack's starting registers and reader profiles. Existing files are kept.", lambda: __import__("grit.ontology.group_seed", fromlist=["_main"])._main, "grit pack-init"),
        "gaps": ("Export or import the BA relationship and alias questions.", lambda: __import__("grit.ontology.relationship_gaps", fromlist=["main"]).main, "grit gaps"),
        "digest-plan": ("Print the business-area query plan for a date.", lambda: __import__("grit.ontology.query_plan", fromlist=["_main"])._main, "grit digest-plan"),
        "digest-search": ("Send the business-area query plan to Brave.", lambda: __import__("grit.acquisition.brave_plan", fromlist=["main"]).main, "grit digest-search"),
        "digest-embed": ("Embed a digest run folder.", lambda: __import__("grit.retrieval.embed_run", fromlist=["main"]).main, "grit digest-embed"),
        "digest-filter": ("Filter a digest run folder.", lambda: __import__("grit.retrieval.filter_run", fromlist=["main"]).main, "grit digest-filter"),
        "digest-loop": ("Write finding cards for a digest run folder.", lambda: __import__("grit.agents.loop.run", fromlist=["main"]).main, "grit digest-loop"),
        "digest-report": ("Draft the Markdown digest for a digest run folder.", lambda: __import__("grit.report.digest", fromlist=["main"]).main, "grit digest-report"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grit", description="GRIT-Scan: agents propose, deterministic functions decide, a named human approves.")
    commands = parser.add_subparsers(dest="command", required=True)

    def reader(name: str, handler: Callable[[argparse.Namespace], int], help_text: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text)
        sub.add_argument("--pack", nargs="+", help="Reader packs. Default: every pack with a scan plan, entity packs first.")
        sub.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Run date, YYYY-MM-DD. Default today.")
        sub.set_defaults(handler=handler)
        return sub

    sub = reader("plan", cmd_plan, "Print the reader pack's weekly queries. Sends nothing.")
    sub.add_argument("--list", type=int, default=80)
    for name, handler, text in (("scan", cmd_scan, "Search, embed, and assess for one reader pack and week."), ("weekly", cmd_weekly, "Run the whole week: scan, agents, findings, packs, RED drafts.")):
        sub = reader(name, handler, text)
        sub.add_argument("--limit-queries", type=int, help="Send at most this many queries, dealt one per lane in turn.")
        sub.add_argument("--currents", nargs="*", help="Up to two Currents queries to add to the pool.")
        sub.add_argument("--no-embed", action="store_true", help="Skip embeddings. Duplicates then merge by identical title only.")
        sub.add_argument("--dry-run", action="store_true", help="Print the queries and stop. Spends nothing.")
        sub.add_argument("--sweep", type=int, default=0, help="Add up to N due queries from a business-area pack's 12-week digest plan. Default 0.")
        if name == "weekly":
            sub.add_argument("--model")
            sub.add_argument("--findings", type=int, default=6, help="Finding cards to write. 0 skips the loop.")
            sub.add_argument("--follow-ups", action="store_true", help="Let the finding loop spend Brave follow-up searches.")
            sub.add_argument("--profile", nargs="*", default=["all"])
            sub.add_argument("--no-submit", action="store_true", help="Write the packs without putting them into review.")
    reader("assess", cmd_assess, "Rerun the deterministic assessment on a scanned run. No network.")
    reader("baseline-draft", cmd_baseline, "Draft a Group pack's baseline for sign-off from a completed run.").add_argument("--model")
    reader("scenarios", cmd_scenarios, "Propose scenarios for a Group pack's strategic shifts from a completed run, for review.").add_argument("--model")
    sub = reader("agents", cmd_agents, "Run the Assessor, Critic and Reporter, then the finding loop.")
    sub.add_argument("--model")
    sub.add_argument("--findings", type=int, default=6)
    sub.add_argument("--follow-ups", action="store_true")
    sub = reader("report", cmd_report, "Write each reader's Word, PowerPoint, Excel and Markdown pack, submit it for review, and draft RED emails.")
    sub.add_argument("--profile", nargs="*", default=["all"])
    sub.add_argument("--no-submit", action="store_true")

    sub = commands.add_parser("feedback", help="Record a reader's approve, reject, or watch decision on a signal.")
    sub.add_argument("--profile", required=True)
    sub.add_argument("--signal", required=True)
    sub.add_argument("--decision", required=True, choices=("APPROVE", "REJECT", "WATCH"))
    sub.add_argument("--rationale")
    sub.set_defaults(handler=cmd_feedback)

    sub = commands.add_parser("review", help="A named reviewer's decision on a package, through ReviewGate.")
    sub.add_argument("--package", required=True)
    sub.add_argument("--version", type=int, required=True)
    sub.add_argument("--profile", required=True)
    sub.add_argument("--intent", required=True, choices=("APPROVE", "REJECT", "HOLD", "REQUEST_REWORK"))
    sub.set_defaults(handler=cmd_review)

    sub = commands.add_parser("requests", help="List, add, activate, or close management requests (spec 29).")
    sub.add_argument("action", choices=("list", "add", "activate", "close"))
    sub.add_argument("--id")
    sub.add_argument("--title")
    sub.add_argument("--description")
    sub.add_argument("--keywords", default="", help="Comma-separated. Each needs 6 or more characters or two words to match.")
    sub.add_argument("--kind", default="CONCERN", choices=("CONCERN", "TOPIC", "TREND", "KEYWORD"))
    sub.add_argument("--by", help="Who asked, e.g. Wilson.")
    sub.add_argument("--country")
    sub.add_argument("--lob")
    sub.add_argument("--profile", default="wilson", help="The reviewer who activates or closes.")
    sub.set_defaults(handler=cmd_requests)

    sub = commands.add_parser("airis", help="Export the BA head's approved signals for AIRIS.")
    sub.add_argument("--profile", default="gareth")
    sub.add_argument("--dry-run", action="store_true")
    sub.set_defaults(handler=cmd_airis)

    sub = reader("dataverse", cmd_dataverse, "Push a run's kept signals to Dataverse grit_signals. Dry run unless --push.")
    sub.add_argument("--push", action="store_true")

    commands.add_parser("db", help="Print the pool's path and row counts.").set_defaults(handler=cmd_db)
    commands.add_parser("ui", help="Start the Streamlit dashboard.").set_defaults(handler=cmd_ui)

    for name, (text, loader, prog) in _delegates().items():
        sub = commands.add_parser(name, help=text, description=f"{text} Arguments after the command go to it unchanged.", add_help=False)
        sub.add_argument("rest", nargs=argparse.REMAINDER)
        sub.set_defaults(handler=lambda args, loader=loader, prog=prog: _delegate(loader(), prog)(args))
    return parser


def main(argv: list[str] | None = None) -> int:
    load_local_env()
    argv = list(sys.argv[1:] if argv is None else argv)
    delegates = _delegates()
    if argv and argv[0] in delegates and not {"-h", "--help"} & set(argv[:1]):
        _text, loader, prog = delegates[argv[0]]
        return _delegate(loader(), prog)(argparse.Namespace(rest=argv[1:]))
    args = build_parser().parse_args(argv)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return int(args.handler(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
