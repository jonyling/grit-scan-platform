"""grit ask: put a question to the Co-Pilot as a named reader, from the command line (spec 40)."""

from __future__ import annotations

import argparse
from datetime import date


def build(profile_id: str, *, window: str = "90d", pack: str | None = None, run_id: str | None = None, focus: str | None = None, today: date | None = None):  # noqa: ANN201
    """The pool, scope, client, model and embedder the dashboard and the CLI share."""
    from grit.agents.gemini import gemini_client
    from grit.copilot.scope import scope_for
    from grit.pool import Pool
    from grit.profiles import load_profiles
    from grit.retrieval.embedder import Embedder
    from grit.settings import load_local_env

    load_local_env()
    profiles = load_profiles()
    if profile_id not in profiles:
        raise SystemExit(f"Unknown profile {profile_id}. Known: {', '.join(sorted(profiles))}.")
    scope = scope_for(profiles[profile_id], window=window, pack=pack, run_id=run_id, focus=focus, today=today)
    client = gemini_client()
    embedder = Embedder(client=client)
    from grit.cli import _model

    model = _model(None)
    return Pool(), scope, client, model, embedder


def main(argv: list[str] | None = None) -> int:
    from grit.copilot.agent import ask, handle, history, record
    from grit.copilot.web import WebSearch

    parser = argparse.ArgumentParser(prog="grit ask", description="Ask the Co-Pilot a question as a reader. The turn is saved to that reader's private chat.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--window", choices=("run", "90d", "all"), default="90d")
    parser.add_argument("--pack", help="Narrow the search to one pack in the reader's reach.")
    parser.add_argument("--run", help="The run on screen. With --window run, search only this run.")
    parser.add_argument("--focus", help="A condition or theme id to focus on.")
    parser.add_argument("--no-save", action="store_true", help="Do not save the turn to the reader's chat (it is still logged).")
    parser.add_argument("--no-web", action="store_true", help="Keep the web off for this question.")
    parser.add_argument("question", nargs="+")
    args = parser.parse_args(argv)
    question = " ".join(args.question)
    pool, scope, client, model, embedder = build(args.profile, window=args.window, pack=args.pack, run_id=args.run, focus=args.focus)
    web = None if args.no_web else WebSearch(pool, scope.profile, today=scope.today)
    print(f"Scope: {scope.describe()}. Model: {model}." + (f" Web searches left today: {web.remaining()}." if web else " Web off."))

    def answer() -> dict:
        return ask(pool, scope, question, client=client, model=model, embedder=embedder,
                   embed=lambda text: embedder.embed([text], task="RETRIEVAL_QUERY")[0], past=history(pool, scope.profile.profile_id), web=web)

    result = handle(pool, scope, question, answer=answer)
    print()
    print(result["markdown"])
    print()
    print(f"Status: {result['status']}.")
    for item in [row for row in result.get("citations") or [] if row.get("kind") == "WEB"]:
        print(f"Web source {item['id']}: to add it to the pool for the next scan, run: grit pool-add --profile {args.profile} --url \"{item['url']}\" --title \"{item.get('title')}\"")
    if args.no_save:
        log = result.get("log") or {}
        if log.get("actor") == "CoPilot":
            pool.log(actor_type="AGENT", actor="CoPilot", version=log.get("version"), model=log.get("model"), subject=scope.profile.profile_id, run_id=args.run,
                     input_hash=log.get("input_hash"), output_hash=log.get("output_hash"), status=log["status"], detail={**log.get("detail", {}), "question": question})
    else:
        record(pool, scope.profile, question, result["markdown"], citations=result.get("citations") or [], log=result.get("log") if (result.get("log") or {}).get("actor") == "CoPilot" else None, run_id=args.run)
    return 0 if result["status"] in {"VALIDATED", "COMMAND"} else 1


def pool_add_main(argv: list[str] | None = None) -> int:
    """grit pool-add: queue a web page for the next scan of the reader's packs, as the dashboard's "Add to pool" does."""
    from grit.copilot.scope import scope_for
    from grit.copilot.web import add_to_pool
    from grit.pool import Pool
    from grit.profiles import load_profiles

    parser = argparse.ArgumentParser(prog="grit pool-add", description="Queue a web page for the next scan. The scan's rules decide whether it is kept.")
    parser.add_argument("--profile", required=True, help="The reader adding it.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--text", default="", help="The snippet or summary, if any.")
    parser.add_argument("--pack", nargs="+", help="Packs to queue it for. Default: every pack the reader can search.")
    args = parser.parse_args(argv)
    profiles = load_profiles()
    if args.profile not in profiles:
        raise SystemExit(f"Unknown profile {args.profile}.")
    scope = scope_for(profiles[args.profile])
    packs = args.pack or list(scope.packs)
    outside = [pack for pack in packs if pack not in scope.packs]
    if outside:
        raise SystemExit(f"{', '.join(outside)} is outside {args.profile}'s packs: {', '.join(scope.packs)}.")
    result = add_to_pool(Pool(), profiles[args.profile], {"url": args.url, "title": args.title, "text": args.text}, packs)
    print(f"{result['signal_id']}: queued for {', '.join(result['queued']) or 'no new pack (already queued)'}. The next scan of each pack assesses it.")
    return 0
