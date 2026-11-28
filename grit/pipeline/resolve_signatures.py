"""Attach LLM/cue-list event signatures to elevated events; cache by input_hash.

Used before story merges so comparison stays deterministic. Agents propose; core compares.
Never writes band, tier, override, or challenge fields.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from grit.agents.signature import extract_signature
from grit.core.event_signature import (
    PROMPT_VERSION,
    SIGNATURE_SOURCE_CACHE,
    SIGNATURE_SOURCE_FALLBACK,
    SIGNATURE_SOURCE_LLM,
    normalise_signature,
    signature_input_hash,
    signature_near_miss,
)
from grit.core.identity import anchor_members
from grit.pool import Pool

InvokeFn = Callable[[str, str, dict[str, Any], Callable[[Any], Any]], tuple[Mapping[str, Any], Any]]


def attach_event_signatures(
    events: Sequence[Mapping[str, Any]],
    *,
    pool: Pool | None = None,
    run_id: str | None = None,
    invoke: InvokeFn | None = None,
    model: str | None = None,
    vectors: Mapping[str, Any] | None = None,
    near_misses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Extract or reuse signatures for events with members. Mutates a shallow copy of each event.

    Returns (events_with_signatures, stats). Cache hit when input_hash is unchanged.
    """
    out: list[dict[str, Any]] = []
    stats = {
        "events": 0,
        "calls": 0,
        "cache_hits": 0,
        "fallback": 0,
        "llm": 0,
        "near_misses": 0,
    }
    digests: list[str] = []
    anchors_by_event: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        if not event.get("members"):
            out.append(dict(event))
            continue
        stats["events"] += 1
        anchors = list(anchor_members(event, vectors=vectors))
        digest = signature_input_hash(anchors)
        digests.append(digest)
        anchors_by_event[str(event.get("event_id") or digest)] = anchors

    cached: dict[str, dict[str, Any]] = {}
    if pool is not None and digests:
        cached = pool.event_signatures_by_hashes(digests)

    signed: list[dict[str, Any]] = []
    for event in events:
        row = dict(event)
        if not event.get("members"):
            signed.append(row)
            continue
        anchors = list(anchor_members(event, vectors=vectors))
        digest = signature_input_hash(anchors)
        hit = cached.get(digest)
        payload = None
        if hit:
            if isinstance(hit.get("signature"), Mapping):
                payload = hit["signature"]
            elif isinstance(hit.get("signature_json"), Mapping):
                payload = hit["signature_json"]
        if payload is not None:
            signature = normalise_signature(payload, source=SIGNATURE_SOURCE_CACHE)
            signature["input_hash"] = digest
            stats["cache_hits"] += 1
        else:
            signature = extract_signature(anchors, invoke=invoke)
            source = str(signature.get("signature_source") or SIGNATURE_SOURCE_FALLBACK)
            if source == SIGNATURE_SOURCE_LLM:
                stats["llm"] += 1
                stats["calls"] += 1
            else:
                stats["fallback"] += 1
                if invoke is not None:
                    stats["calls"] += 1  # attempted call that fell back
            if pool is not None:
                try:
                    pool.add_event_signature(
                        input_hash=digest,
                        signature={k: v for k, v in signature.items() if k != "input_hash"},
                        signature_source=source if source in {
                            SIGNATURE_SOURCE_LLM, SIGNATURE_SOURCE_FALLBACK, SIGNATURE_SOURCE_CACHE,
                        } else SIGNATURE_SOURCE_FALLBACK,
                        event_id=str(event.get("event_id") or "") or None,
                        run_id=run_id,
                        model=model if source == SIGNATURE_SOURCE_LLM else None,
                        prompt_version=PROMPT_VERSION,
                    )
                except Exception:
                    pass
        row["signature"] = signature
        row["signature_input_hash"] = digest
        signed.append(row)

    # Near-miss instrumentation across elevated pairs (topic candidates for P2).
    elevated = [event for event in signed if event.get("elevated") and event.get("signature")]
    for i, left in enumerate(elevated):
        for right in elevated[i + 1:]:
            miss = signature_near_miss(left["signature"], right["signature"])
            if miss is None:
                continue
            stats["near_misses"] += 1
            record = {
                **miss,
                "left_event_id": left.get("event_id"),
                "right_event_id": right.get("event_id"),
                "left_lead": (left.get("lead") or {}).get("title"),
                "right_lead": (right.get("lead") or {}).get("title"),
            }
            if near_misses is not None:
                near_misses.append(record)
            if pool is not None and run_id:
                try:
                    pool.log(
                        actor_type="FUNCTION",
                        actor="SignatureNearMiss",
                        run_id=run_id,
                        version=PROMPT_VERSION,
                        status="SUCCEEDED",
                        detail=record,
                    )
                except Exception:
                    pass

    cache_denom = stats["cache_hits"] + stats["llm"] + stats["fallback"]
    stats["cache_hit_rate"] = round(stats["cache_hits"] / cache_denom, 4) if cache_denom else None
    return {"events": signed, "stats": stats}
