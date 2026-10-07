"""Embed one Brave run and write a similarity table. It drops nothing and applies no threshold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from acquisition.brave_plan import canonical_url
from retrieval.anchors import build_anchors
from retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL, Embedder
from retrieval.similarity import dot, top_matches
from retrieval.store import VectorStore, text_hash

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORE = ROOT / "output" / "vector" / "index.sqlite"
ANCHOR_TASK = "RETRIEVAL_QUERY"
CANDIDATE_TASK = "RETRIEVAL_DOCUMENT"
SCORE_DIGITS = 6


def candidate_text(candidate: dict[str, Any]) -> str:
    return f"{candidate.get('title') or ''}\n{candidate.get('text') or ''}".strip()


def main(argv: list[str] | None = None, *, embedder: Any | None = None, store_path: Path = DEFAULT_STORE) -> int:
    from grit_scan.local_env import load_local_env
    from grit_scan.ontology import load_ontology_pack

    load_local_env()
    parser = argparse.ArgumentParser(description="Embed a Brave run's candidates and the ontology anchors, then write semantic.json.")
    parser.add_argument("--run", required=True, type=Path, help="A run folder, such as output/brave-plan/2026-10-06.")
    parser.add_argument("--top", type=int, default=3, help="Best anchors to keep per candidate.")
    parser.add_argument("--dry-run", action="store_true", help="Count what would be embedded. Call nothing.")
    args = parser.parse_args(argv)

    source = args.run / "candidates.json"
    if not source.is_file():
        print(f"No candidates.json in {args.run}.")
        return 1
    run = json.loads(source.read_text(encoding="utf-8"))
    candidates = run["candidates"]
    anchors = build_anchors(load_ontology_pack(ROOT / "config", "urs"))
    embedder = embedder or Embedder()
    candidate_texts = [candidate_text(candidate) for candidate in candidates]
    anchor_texts = [anchor.text for anchor in anchors]
    log = args.run / "searches.jsonl"
    searches = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()] if log.is_file() else []
    queries = list({entry["query_id"]: entry["query"] for entry in searches}.items())
    print(f"Candidates: {len(candidates)}. Anchors: {len(anchors)}. Queries: {len(queries)}. Model {embedder.model}, {embedder.dimensions} dimensions.")
    if args.dry_run:
        print("Dry run. Nothing was embedded.")
        return 0
    store = VectorStore(store_path)
    anchor_vectors = store.vectors(anchor_texts, task=ANCHOR_TASK, embedder=embedder)
    candidate_vectors = store.vectors(candidate_texts, task=CANDIDATE_TASK, embedder=embedder)
    query_vectors = dict(zip((query_id for query_id, _ in queries), store.vectors([text for _, text in queries], task=ANCHOR_TASK, embedder=embedder)))
    by_url = {candidate["url"]: vector for candidate, vector in zip(candidates, candidate_vectors)}
    query_scores: dict[str, list[dict[str, Any]]] = {}
    for entry in searches:
        for result in entry["results"]:
            url = canonical_url(result["url"])
            if url in by_url:
                score = round(dot(by_url[url], query_vectors[entry["query_id"]]), SCORE_DIGITS)
                query_scores.setdefault(url, []).append({"query_id": entry["query_id"], "rank": result["rank"], "score": score})
    scores = []
    for candidate, text, vector in zip(candidates, candidate_texts, candidate_vectors):
        best = [{"anchor_id": anchors[index].anchor_id, "score": round(score, SCORE_DIGITS)} for index, score in top_matches(vector, anchor_vectors, args.top)]
        scores.append({"url": candidate["url"], "text_hash": text_hash(text), "best": best})
    (args.run / "anchors.json").write_text(json.dumps([anchor.as_record() for anchor in anchors], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.run / "semantic.json").write_text(
        json.dumps(
            {
                "runDate": run.get("runDate"),
                "model": embedder.model,
                "dimensions": embedder.dimensions,
                "anchorTask": ANCHOR_TASK,
                "candidateTask": CANDIDATE_TASK,
                "anchorCount": len(anchors),
                "queries": [{"query_id": query_id, "query": text} for query_id, text in queries],
                "scores": scores,
                "query_scores": query_scores,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    store.close()
    best = sorted(item["best"][0]["score"] for item in scores if item["best"])
    if best:
        histogram = {}
        for score in best:
            histogram[int(score * 20) / 20] = histogram.get(int(score * 20) / 20, 0) + 1
        print(f"Embedded {store.embedded}, from cache {store.hits}. Best-anchor similarity: min {best[0]}, median {best[len(best) // 2]}, max {best[-1]}.")
        print("Distribution (lower bound of each 0.05 band):", dict(sorted(histogram.items())))
    print(f"Wrote {args.run / 'semantic.json'} and anchors.json. No candidate was dropped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
