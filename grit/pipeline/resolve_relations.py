"""Shared Event Relation Resolver: candidate-generate, classify, persist.

Used by the weekly reasoned path (P1) and the nightly index (P2 daily→weekly reuse).
Agents propose; core applies. Never writes band, tier, override, or challenge fields.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from grit.agents.relations import (
    ambiguous_edges,
    apply_critic_reviews,
    check_relation_critique,
    check_relations,
)
from grit.core.event_relations import (
    CONTRACT,
    PROMPT_VERSION,
    REASON_CHARS,
    T_SAME,
    candidate_edges,
    input_hash_for,
    needs_llm,
    ordered_pair,
    pack_candidate,
    relation_id_for,
)
from grit.pool import Pool

InvokeFn = Callable[[str, str, dict[str, Any], Callable[[Any], Any]], tuple[Mapping[str, Any], Any]]


def excerpt_of(row: Mapping[str, Any], *, chars: int = 500) -> str:
    return " ".join(str(row.get("text") or "").split())[:chars]


def pair_input_hash(left: Mapping[str, Any], right: Mapping[str, Any], features: Mapping[str, Any]) -> str:
    return input_hash_for(
        {"signal_id": left.get("signal_id"), "title": left.get("title"), "excerpt": excerpt_of(left)},
        {"signal_id": right.get("signal_id"), "title": right.get("title"), "excerpt": excerpt_of(right)},
        features,
    )


def load_cosines(pool: Pool, signal_ids: Sequence[str]) -> dict[tuple[str, str], float]:
    """Pairwise cosine for signals that have pool vectors. Empty on failure."""
    if len(signal_ids) < 2:
        return {}
    try:
        import numpy as np

        from grit.retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL

        vectors = {
            key: np.asarray(value, dtype=np.float32)
            for key, value in pool.vectors(
                list(signal_ids), model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS
            ).items()
        }
    except Exception:
        return {}
    cosines: dict[tuple[str, str], float] = {}
    keys = sorted(vectors)
    for i, left in enumerate(keys):
        for right in keys[i + 1:]:
            cosines[ordered_pair(left, right)] = float(vectors[left] @ vectors[right])
    return cosines


def resolve_and_persist(
    pool: Pool,
    *,
    run_id: str,
    sources: Mapping[str, Mapping[str, Any]],
    clusters: Mapping[str, str] | None = None,
    prior_relations: Sequence[Mapping[str, Any]] = (),
    invoke: InvokeFn | None = None,
    model: str | None = None,
    elevated_ids: set[str] | None = None,
    citation_pairs: Sequence[tuple[str, str]] = (),
) -> dict[str, int]:
    """Candidate-generate, reuse by input_hash, auto-SAME, optional LLM+Critic, persist under run_id.

    When invoke is None, only auto-SAME and cross-run hash reuse run (nightly cheap path).
    citation_pairs: identity nominations from `_group` / `_merge_stories` / `label_merges` (P0.2).
    """
    if len(sources) < 2:
        return {
            "enabled": 1, "candidates": 0, "llm": 0, "auto_same": 0,
            "reused": 0, "validated": 0, "critic": 0, "rejected": 0,
        }
    clusters = clusters or {}
    cosines = load_cosines(pool, list(sources))
    config = (pool.run(run_id) or {}).get("config") or {}
    stored = []
    for pair in config.get("identity_nomination_pairs") or []:
        if isinstance(pair, (list, tuple)) and len(pair) == 2:
            stored.append((str(pair[0]), str(pair[1])))
    pairs = list(citation_pairs) + stored
    edges = candidate_edges(
        list(sources.values()), clusters=clusters, cosines=cosines, prior_relations=prior_relations,
        citation_pairs=pairs,
    )
    digests = [
        pair_input_hash(sources[edge["left_signal_id"]], sources[edge["right_signal_id"]], edge.get("features") or {})
        for edge in edges
    ]
    cached_by_hash = pool.event_relations_by_hashes(digests)

    auto_rows: list[dict[str, Any]] = []
    llm_edges: list[dict[str, Any]] = []
    reused = 0
    for index, edge in enumerate(edges, 1):
        edge_id = f"E{index}"
        digest = digests[index - 1]
        cached = cached_by_hash.get(digest)
        if cached and {cached["left_signal_id"], cached["right_signal_id"]} == {
                edge["left_signal_id"], edge["right_signal_id"]}:
            reused += 1
            auto_rows.append({
                "edge_id": edge_id,
                "left_signal_id": edge["left_signal_id"], "right_signal_id": edge["right_signal_id"],
                "left_event_id": edge.get("left_event_id"), "right_event_id": edge.get("right_event_id"),
                "label": cached["label"], "confidence": float(cached["confidence"]),
                "reason": str(cached.get("reason") or "Reused validated relation.")[:REASON_CHARS],
                "evidence": {
                    **(cached.get("evidence") or {}),
                    "reused": True,
                    "reused_from_run": cached.get("run_id"),
                    "features": edge.get("features") or {},
                },
                "input_hash": digest, "status": "VALIDATED",
            })
            continue
        if not needs_llm(edge):
            auto_rows.append({
                "edge_id": edge_id,
                "left_signal_id": edge["left_signal_id"], "right_signal_id": edge["right_signal_id"],
                "left_event_id": edge.get("left_event_id"), "right_event_id": edge.get("right_event_id"),
                "label": "SAME",
                "confidence": max(T_SAME, float((edge.get("features") or {}).get("cosine") or T_SAME)),
                "reason": "Auto-SAME: cosine ≥ 0.90 with ≥2 shared terms on the same day.",
                "evidence": {"auto_same": True, "features": edge.get("features") or {}},
                "input_hash": digest, "status": "VALIDATED",
            })
            continue
        if invoke is None:
            # Nightly / offline: leave LLM-eligible edges unresolved (no INSUFFICIENT spam).
            continue
        packed = pack_candidate(edge, sources, edge_id=edge_id)
        llm_edges.append({**edge, "edge_id": edge_id, "input_hash": digest, "packed": packed})

    proposals: list[dict[str, Any]] = list(auto_rows)
    if llm_edges and invoke is not None:
        payload = {"contract": CONTRACT, "candidates": [item["packed"] for item in llm_edges]}
        edge_ids = {item["edge_id"] for item in llm_edges}
        response, _call_id = invoke(
            "EventRelation", "resolve-relations", payload,
            lambda raw: check_relations(raw, edge_ids),
        )
        by_edge = {item["edge_id"]: item for item in llm_edges}
        for row in response["relations"]:
            edge = by_edge[row["edge_id"]]
            proposals.append({
                "edge_id": row["edge_id"],
                "left_signal_id": edge["left_signal_id"], "right_signal_id": edge["right_signal_id"],
                "left_event_id": edge.get("left_event_id"), "right_event_id": edge.get("right_event_id"),
                "label": row["label"], "confidence": float(row["confidence"]),
                "reason": str(row["reason"]).strip()[:REASON_CHARS],
                "quote": row.get("quote"),
                "evidence": {"features": edge.get("features") or {}, "quote": row.get("quote")},
                "input_hash": edge["input_hash"], "status": "PROPOSED",
            })

    elevated = elevated_ids or {
        key for key, row in sources.items()
        if row.get("red") or (row.get("tier") or 9) <= 1
    }
    elevated_pairs = {ordered_pair(a, b) for a in elevated for b in elevated if a < b}
    ambiguous = ambiguous_edges(proposals, elevated_pairs=elevated_pairs) if invoke is not None else []
    critic_count = 0
    if ambiguous and invoke is not None:
        critic_count = len(ambiguous)
        critique_payload = {
            "contract": CONTRACT,
            "candidates": [
                pack_candidate(
                    {
                        "left_signal_id": row["left_signal_id"], "right_signal_id": row["right_signal_id"],
                        "left_event_id": row.get("left_event_id"), "right_event_id": row.get("right_event_id"),
                        "features": (row.get("evidence") or {}).get("features") or {},
                    },
                    sources, edge_id=str(row["edge_id"]),
                ) | {"proposal": {"label": row["label"], "confidence": row["confidence"], "reason": row["reason"]}}
                for row in ambiguous
            ],
        }
        review, _ = invoke(
            "RelationCritic", "critique-relations", critique_payload,
            lambda raw: check_relation_critique(raw, {str(row["edge_id"]) for row in ambiguous}),
        )
        proposals = apply_critic_reviews(proposals, review["reviews"])

    validated = 0
    rejected = 0
    for row in proposals:
        label, confidence = str(row["label"]), float(row["confidence"])
        status = "VALIDATED"
        if label == "SAME" and confidence < T_SAME and not (row.get("evidence") or {}).get("critic_upheld"):
            status = "REJECTED"
            rejected += 1
        elif status == "VALIDATED":
            validated += 1
        pool.add_event_relation(
            relation_id=relation_id_for(run_id, row["left_signal_id"], row["right_signal_id"]),
            run_id=run_id,
            left_signal_id=row["left_signal_id"],
            right_signal_id=row["right_signal_id"],
            left_event_id=row.get("left_event_id"),
            right_event_id=row.get("right_event_id"),
            label=label if status != "REJECTED" else "INSUFFICIENT",
            confidence=confidence,
            reason=str(row.get("reason") or "")[:REASON_CHARS],
            input_hash=str(row["input_hash"]),
            status="REJECTED" if status == "REJECTED" else "VALIDATED",
            evidence=row.get("evidence") or {},
            model=model,
            prompt_version=PROMPT_VERSION,
        )
    return {
        "enabled": 1,
        "candidates": len(edges),
        "llm": len(llm_edges),
        "auto_same": sum(1 for row in auto_rows if (row.get("evidence") or {}).get("auto_same")),
        "reused": reused,
        "validated": validated,
        "critic": critic_count,
        "rejected": rejected,
    }
