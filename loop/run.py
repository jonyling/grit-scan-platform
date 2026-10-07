"""Run the finding loop over the top signals of a filtered run. It calls the model, and it may call Brave once per finding."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from loop.engine import FindingLoop, LoopConfig
from loop.prompts import DRAFT_VERSION, EVALUATE_VERSION
from loop.tools import FollowUpTool

DEFAULT_MODEL = "gemini-2.5-flash"
FOLLOW_UP_FRESHNESS = "pm"
FOLLOW_UP_COUNT = 20


def brave_search(run_folder: Path, *, getter: Any | None = None, usage_path: Path | None = None) -> Callable[..., list[dict[str, Any]]]:
    """One Brave News search per call. It counts against the same monthly guard as the plan run, and every search is logged."""
    from acquisition.brave import BraveFetchError
    from acquisition.brave_plan import DEFAULT_USAGE_PATH, collect_plan
    from retrieval.store import text_hash

    from acquisition.brave_plan import candidates_from_searches

    log = run_folder / "followups.jsonl"

    def convert(entry: dict[str, Any]) -> list[dict[str, Any]]:
        candidates, _hits = candidates_from_searches([entry], fetched_at=entry["fetched_at"])
        return [candidate.as_record() for candidate in candidates]

    def logged(query: str, key: str | None) -> list[dict[str, Any]] | None:
        """A search already sent for this signal, or for this exact query, in this run folder. Reusing it spends no Brave query.
        The signal is the key because the model words its query differently each run."""
        if not log.is_file():
            return None
        same_query = None
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # a half-written line from an interrupted run
            if entry.get("results") is None or "fetched_at" not in entry:
                continue
            if key and entry.get("signal_url") == key:
                return convert(entry)
            if same_query is None and entry.get("query") == query:
                same_query = entry
        return convert(same_query) if same_query else None

    def search(query: str, key: str | None = None) -> list[dict[str, Any]]:
        reused = logged(query, key)
        if reused is not None:
            return reused
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        spec = SimpleNamespace(query_id=f"followup-{text_hash(query)[:12]}", query=query, exact=False)
        entries: list[dict[str, Any]] = []
        try:
            run = collect_plan(
                [spec], fetched_at=fetched_at, freshness=FOLLOW_UP_FRESHNESS, count=FOLLOW_UP_COUNT, country="ALL", getter=getter,
                usage_path=usage_path or DEFAULT_USAGE_PATH, on_search=entries.append,
            )
        except (ValueError, BraveFetchError) as exc:
            entries.append({"query": query, "error": str(exc)[:200], "fetched_at": fetched_at})
            run = SimpleNamespace(candidates=[], stopped="ERROR")
        with log.open("a", encoding="utf-8") as handle:
            for entry in entries or [{"query": query, "stopped": run.stopped, "fetched_at": fetched_at}]:
                handle.write(json.dumps({**entry, "signal_url": key}, ensure_ascii=False) + "\n")
        return [candidate.as_record() for candidate in run.candidates]

    return search


def main(argv: list[str] | None = None, *, client: Any | None = None, search: Callable[..., list[dict[str, Any]]] | None = None, embedder: Any | None = None) -> int:
    from grit_scan.local_env import load_local_env
    from grit_scan.ontology import load_ontology_pack
    from grit_scan.ontology_graph import build_graph
    from grit_scan.link import LinkIndex
    from retrieval.embed_run import CANDIDATE_TASK, DEFAULT_STORE, ROOT, candidate_text
    from retrieval.embedder import Embedder
    from retrieval.filter_run import cached_vectors
    from retrieval.store import VectorStore

    load_local_env()
    defaults = LoopConfig()
    parser = argparse.ArgumentParser(description="Write a six-question finding card for the top signals of a filtered run, in a self-evaluating loop.")
    parser.add_argument("--run", required=True, type=Path, help="A run folder that holds signals.json.")
    parser.add_argument("--top", type=int, default=10, help="Findings to write, taken in ranked order.")
    parser.add_argument("--max-tier", type=int, default=2, choices=(1, 2, 3), help="Include signals up to this priority tier. 1 is big and direct, 2 is big or direct.")
    parser.add_argument("--max-iterations", type=int, default=defaults.max_iterations)
    parser.add_argument("--follow-ups", type=int, default=10, help="Follow-up searches for the whole run. Each finding may use one.")
    parser.add_argument("--no-search", action="store_true", help="Never call Brave from the loop.")
    parser.add_argument("--model", default=None, help="Defaults to GEMINI_MODEL, then " + DEFAULT_MODEL + ".")
    parser.add_argument("--dry-run", action="store_true", help="Build each finding's ontology context and print its size. Call no model and no search.")
    args = parser.parse_args(argv)

    try:
        signals = json.loads((args.run / "signals.json").read_text(encoding="utf-8"))
        run = json.loads((args.run / "candidates.json").read_text(encoding="utf-8"))
        semantic = json.loads((args.run / "semantic.json").read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        print(f"{exc.filename} is missing. Run retrieval.filter_run on this folder first.")
        return 1
    chosen = [item for item in signals["bound"] if (item.get("priority") or {}).get("tier", 9) <= args.max_tier][: args.top]
    print(f"Signals {len(signals['bound'])} linked. Tiers {signals['summary'].get('tiers')}. Selected {len(chosen)} up to tier {args.max_tier}.")
    if not chosen:
        print("No signal reaches that tier. Nothing to write. Try --max-tier 3.")
        return 0

    pack = load_ontology_pack(ROOT / "config", "urs")
    aliases = json.loads((ROOT / "config" / "registry_aliases.json").read_text(encoding="utf-8-sig"))
    graph = build_graph(pack, reviewed_aliases=aliases)
    index = LinkIndex(graph, pack.lob_profiles)
    pool: list[dict[str, Any]] = list(run["candidates"])
    try:
        vectors = dict(cached_vectors(pool, semantic, DEFAULT_STORE))
    except RuntimeError as exc:
        print(exc)
        return 1
    same_event = signals["config"]["priority"]["same_event"]
    config = LoopConfig(max_iterations=args.max_iterations, same_event=same_event)
    model = args.model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL

    if args.dry_run:
        from loop.context import build_context

        for number, signal in enumerate(chosen, start=1):
            context = build_context(f"F{number:02d}", signal, graph=graph, index=index, themes=pack.themes["themes"], pool=pool, vectors=vectors, same_event=same_event)
            sent = len(json.dumps(context.view, ensure_ascii=False))
            print(
                f"  F{number:02d} tier {signal['priority']['tier']} {signal['rung']:12} scope {context.scope:13} paths {len(context.paths)} entities {context.entities_total} "
                f"evidence {len(context.evidence)} (independent outlets {context.independent_outlets()}) input {sent} chars | {signal['title'][:56]}"
            )
        print(f"Dry run. No model and no search was called. Model would be {model}.")
        return 0

    if client is None:
        from agents.gemini import gemini_client

        client = gemini_client()
    store = VectorStore(DEFAULT_STORE)
    real_embedder = embedder or Embedder(model=semantic["model"], dimensions=semantic["dimensions"])

    def embed(candidates: Any) -> dict[str, list[float]]:
        found = store.vectors([candidate_text(c) for c in candidates], task=CANDIDATE_TASK, embedder=real_embedder)
        return dict(zip((c["url"] for c in candidates), found))

    tool = FollowUpTool(
        search=None if args.no_search else (search or brave_search(args.run)), embed=embed, index=index, pool=pool, vectors=vectors, same_event=same_event, budget=args.follow_ups
    )
    log_path = args.run / "loop-log.jsonl"
    log_path.write_text("", encoding="utf-8")

    def log(entry: dict[str, Any]) -> None:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    engine = FindingLoop(client, model, graph=graph, index=index, themes=pack.themes["themes"], pool=pool, vectors=vectors, tool=tool, config=config, log=log)
    findings: list[dict[str, Any]] = []
    findings_path = args.run / "findings.json"
    findings_path.unlink(missing_ok=True)  # an older file must never be mistaken for this run

    def summary() -> dict[str, Any]:
        return {
            "findings": len(findings),
            "status": dict(Counter(item["status"] for item in findings)),
            "stops": dict(Counter(item["loop"]["stop"] for item in findings)),
            "confidence": dict(Counter(item["q6"]["level"] for item in findings)),
            "doubted": sum(1 for item in findings if item.get("doubted")),
            "model_calls": sum(item["loop"]["model_calls"] for item in findings),
            "follow_up_searches": tool.spent,
        }

    def write(status: str) -> None:
        findings_path.write_text(
            json.dumps(
                {
                    "runDate": signals["runDate"], "status": status, "model": model, "prompt_versions": {"draft": DRAFT_VERSION, "evaluate": EVALUATE_VERSION},
                    "config": {
                        "top": args.top, "max_tier": args.max_tier, "max_iterations": config.max_iterations, "followups_per_finding": config.followups_per_finding,
                        "follow_up_budget": args.follow_ups, "same_event": same_event,
                    },
                    "summary": summary(), "findings": findings,
                },
                indent=2, ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    try:
        for number, signal in enumerate(chosen, start=1):
            finding = engine.run(f"F{number:02d}", signal)
            findings.append(finding)
            write("IN_PROGRESS")  # a crash or an interrupt keeps what the paid calls already made
            loop = finding["loop"]
            print(
                f"  {finding['finding_id']} {finding['status']:9} confidence {finding['q6']['level']:15} iterations {len(loop['iterations'])} stop {loop['stop']:15} "
                f"calls {loop['model_calls']} searches {loop['follow_up_searches']} | {str(finding['title'])[:50]}"
            )
    finally:
        store.close()
    write("DRAFT_FOR_REVIEW")
    print(f"Wrote {findings_path} and loop-log.jsonl. {summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
