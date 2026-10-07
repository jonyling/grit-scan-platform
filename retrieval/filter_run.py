"""Apply the keep rule to one embedded Brave run. It reads local files and the vector cache, and calls no API."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from grit_scan.doc_type import classify
from grit_scan.link import LinkIndex, link_candidate
from grit_scan.matchers.credibility_lookup import credibility_lookup
from grit_scan.ontology_graph import build_graph
from grit_scan.priority import PriorityConfig
from grit_scan.scale_cues import scale_cues
from retrieval.embed_run import CANDIDATE_TASK, DEFAULT_STORE, ROOT, candidate_text
from retrieval.signal_filter import FilterConfig, filter_candidates
from retrieval.store import VectorStore, text_hash


class _CachedOnly:
    """Stands in for the embedder. A missing vector means embed_run has not run, and that is an error."""

    batch_size = 1

    def __init__(self, model: str, dimensions: int) -> None:
        self.model, self.dimensions = model, dimensions

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        raise RuntimeError("A vector is missing. Run retrieval.embed_run on this folder first.")


def cached_vectors(candidates: list[dict[str, Any]], semantic: dict[str, Any], store_path: Path = DEFAULT_STORE) -> dict[str, list[float]]:
    """The vector of every candidate, from the cache only. A missing vector raises, because embed_run has not run."""
    store = VectorStore(store_path)
    try:
        found = store.vectors([candidate_text(c) for c in candidates], task=CANDIDATE_TASK, embedder=_CachedOnly(semantic["model"], semantic["dimensions"]))
    finally:
        store.close()
    return dict(zip((c["url"] for c in candidates), found))


def main(argv: list[str] | None = None, *, store_path: Path = DEFAULT_STORE) -> int:
    from grit_scan.ontology import load_ontology_pack

    defaults = FilterConfig()
    parser = argparse.ArgumentParser(description="Keep bound signals and a few relevant unbound ones from an embedded run.")
    parser.add_argument("--run", required=True, type=Path, help="A run folder that already holds semantic.json.")
    parser.add_argument("--window-days", type=int, default=defaults.window_days)
    parser.add_argument("--near-duplicate", type=float, default=defaults.near_duplicate, help="Cosine at or above which two pages are one story.")
    parser.add_argument("--per-query-top", type=int, default=defaults.per_query_top, help="An unbound page must rank this high in a query that found it.")
    parser.add_argument("--unbound-cap", type=int, default=defaults.unbound_cap)
    parser.add_argument("--bound-cap", type=int, default=defaults.bound_cap, help="A safety valve. A trial run stays under it. A full week can exceed it, and the cut is reported.")
    parser.add_argument("--diverse-similarity", type=float, default=defaults.diverse_similarity, help="An unbound page this close to a kept one is cut, so one event takes one slot.")
    parser.add_argument("--outlets-needed", type=int, default=defaults.priority.outlets_needed, help="Outlets that make the OUTLETS cue. A wire service counts once.")
    parser.add_argument("--min-cues", type=int, default=defaults.priority.min_cues, help="Cues, besides a KEY theme hit, that make an event big. The maximum is 3.")
    parser.add_argument("--same-event", type=float, default=defaults.priority.same_event, help="Cosine at or above which pages from different outlets are one event.")
    args = parser.parse_args(argv)
    config = FilterConfig(
        args.window_days, args.near_duplicate, args.per_query_top, args.unbound_cap, args.bound_cap, args.diverse_similarity,
        PriorityConfig(args.outlets_needed, args.min_cues, args.same_event),
    )

    try:
        run = json.loads((args.run / "candidates.json").read_text(encoding="utf-8"))
        semantic = json.loads((args.run / "semantic.json").read_text(encoding="utf-8"))
        anchors = {item["anchor_id"]: item for item in json.loads((args.run / "anchors.json").read_text(encoding="utf-8"))}
    except FileNotFoundError as exc:
        print(f"{exc.filename} is missing. Run retrieval.embed_run on this folder first.")
        return 1
    candidates = run["candidates"]
    pack = load_ontology_pack(ROOT / "config", "urs")
    graph = build_graph(pack, reviewed_aliases=json.loads((ROOT / "config" / "registry_aliases.json").read_text(encoding="utf-8-sig")))
    index = LinkIndex(graph, pack.lob_profiles)
    try:
        vectors = cached_vectors(candidates, semantic, store_path)
    except RuntimeError as exc:
        print(exc)
        return 1

    result = filter_candidates(
        candidates,
        run_date=date.fromisoformat(run["runDate"]),
        links={c["url"]: link_candidate(c, index, pack.lob_profiles) for c in candidates},
        doc_types={c["url"]: classify(c) for c in candidates},
        scales={c["url"]: scale_cues(c) for c in candidates},
        best_anchors={item["url"]: item["best"] for item in semantic["scores"]},
        query_scores=semantic.get("query_scores") or {},
        vectors=vectors,
        source_registered={c["url"]: credibility_lookup(c["url"], pack.source_register)["matched"] for c in candidates},
        config=config,
    )
    decisions = {item["url"]: item for item in result["decisions"]}
    queries = {item["query_id"]: item["query"] for item in semantic.get("queries") or []}

    def signal(url: str) -> dict[str, Any]:
        candidate = next(c for c in candidates if c["url"] == url)
        record = dict(decisions[url])
        record["text"] = candidate.get("text")
        record["best_anchors"] = [{**hit, "anchor": anchors[hit["anchor_id"]]["text"], "kind": anchors[hit["anchor_id"]]["kind"]} for hit in record["best_anchors"]]
        record["found_by"] = [{**hit, "query": queries.get(hit["query_id"])} for hit in (semantic.get("query_scores") or {}).get(url, [])]
        return record

    (args.run / "decisions.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in result["decisions"]), encoding="utf-8")
    (args.run / "signals.json").write_text(
        json.dumps(
            {
                "runDate": run["runDate"],
                "config": result["config"],
                "summary": result["summary"],
                "bound": [signal(url) for url in result["bound"]],
                "unbound": [signal(url) for url in result["unbound"]],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    summary = result["summary"]
    print(f"Candidates {summary['candidates']}: kept {summary['keep_bound']} linked and {summary['keep_unbound']} not linked.")
    print(f"Kept linked signals by tier (1 big and direct, 2 big or direct, 3 linked only): {summary['tiers']}. Big events: {summary['big']} (with one cue besides the KEY theme: {summary['big_with_one_cue']}).")
    print(f"Dropped, by reason: {summary['dropped']}")
    print(f"Wrote {args.run / 'signals.json'} and decisions.jsonl. Every candidate has a decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
