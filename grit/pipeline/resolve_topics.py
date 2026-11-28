"""Topic Resolver pipeline: one call → validate → TopicCritic once → persist.

Used by the weekly reasoned path when topic_collapse=1. Agents propose; core applies.
Never writes SAME, band, tier, override, or challenge fields. Theme candidates never change band.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from grit.agents.topics import (
    ambiguous_topics,
    check_topic_critique,
    check_topics,
    critic_verdicts_by_id,
)
from grit.core.topics import (
    CONTRACT,
    NEAR_THRESHOLD_TOP_N,
    PRIOR_TOPIC_WEEKS,
    PROMPT_VERSION,
    apply_theme_candidates,
    apply_topic_resolution,
    enabled,
    pack_event_for_topics,
    pack_prior_topic,
    select_topic_events,
    topic_input_hash,
    topic_ledger_hash,
)
from grit.pool import Pool

InvokeFn = Callable[[str, str, dict[str, Any], Callable[[Any], Any]], tuple[Mapping[str, Any], Any]]


def prior_topics_for_pack(
    pool: Pool,
    *,
    pack: str,
    before_date: str,
    weeks: int = PRIOR_TOPIC_WEEKS,
) -> list[dict[str, Any]]:
    """Active topics from VALIDATED resolutions in the previous N weeks for this pack."""
    try:
        end = date.fromisoformat(str(before_date)[:10])
    except ValueError:
        end = date.today()
    start = end - timedelta(days=max(1, weeks) * 7)
    rows = pool.topic_resolutions_recent(pack=pack, since=start.isoformat(), until=before_date, validated_only=True)
    # Latest standing row per topic_key.
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("topic_key") or "")
        if key:
            latest[key] = row
    return [pack_prior_topic(row) for row in latest.values()]


def resolve_and_persist(
    pool: Pool,
    *,
    run_id: str,
    events: Sequence[Mapping[str, Any]],
    invoke: InvokeFn | None = None,
    model: str | None = None,
    near_top_n: int = NEAR_THRESHOLD_TOP_N,
    prior_topics: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Invoke TopicResolver over elevated + near-threshold + prior topics; Critic once; persist.

    No-op when topic_collapse is off. When invoke is None, only persists nothing and returns enabled stats
    (offline / flag-off callers still get a structured empty summary).
    """
    run = pool.run(run_id) or {}
    config = run.get("config") or {}
    if not enabled(config):
        return {
            "enabled": 0, "elevated": 0, "near": 0, "topics": 0, "unassigned": 0,
            "themes": 0, "critic": 0, "rejected": 0, "input_hash": None, "ledger_hash": None,
        }

    elevated, near = select_topic_events(events, near_top_n=near_top_n)
    elevated_ids = {str(e["event_id"]) for e in elevated}
    near_ids = {str(e["event_id"]) for e in near}
    allowed_ids = elevated_ids | near_ids

    pack = str(run.get("pack") or "")
    run_date = str(run.get("run_date") or "")[:10]
    if prior_topics is None:
        prior_topics = prior_topics_for_pack(pool, pack=pack, before_date=run_date)
    prior_keys = {str(row.get("topic_key") or "") for row in prior_topics if row.get("topic_key")}

    packed_events = [pack_event_for_topics(event) for event in [*elevated, *near]]
    digest = topic_input_hash(packed_events, prior_topics)
    empty = {
        "enabled": 1, "elevated": len(elevated), "near": len(near), "topics": 0, "unassigned": len(elevated_ids),
        "themes": 0, "critic": 0, "rejected": 0, "input_hash": digest, "ledger_hash": None,
        "topics_detail": [], "theme_candidates": [], "unassigned_ids": sorted(elevated_ids),
    }
    if not elevated_ids:
        return empty
    if invoke is None:
        # Offline: leave elevated unassigned (no LLM).
        return empty

    payload = {
        "contract": CONTRACT,
        "events": packed_events,
        "prior_topics": [pack_prior_topic(row) for row in prior_topics],
        "guidance": {
            "T_TOPIC": 0.80,
            "ambiguous": [0.55, 0.80],
            "basis_rule": "name a shared actor, mechanism or STE exposure; not a country alone",
            "theme_rule": "near-threshold only; never change band",
        },
    }

    def validate(raw: Any) -> Mapping[str, Any]:
        return check_topics(raw, elevated_ids=elevated_ids, allowed_ids=allowed_ids, prior_keys=prior_keys)

    response, _call_id = invoke("TopicResolver", "resolve-topics", payload, validate)
    topics_raw = list(response.get("topics") or [])
    # Stamp topic_id for Critic alignment.
    for index, row in enumerate(topics_raw):
        if isinstance(row, dict):
            row.setdefault("topic_id", f"T{index + 1}")

    ambiguous = ambiguous_topics(topics_raw)
    critic_count = 0
    verdicts: dict[str, dict[str, Any]] = {}
    if ambiguous:
        critic_count = len(ambiguous)
        critique_payload = {
            "contract": CONTRACT,
            "topics": [
                {
                    "topic_id": row["topic_id"],
                    "topic_key": row.get("topic_key"),
                    "label": row.get("label"),
                    "event_ids": row.get("event_ids"),
                    "basis": row.get("basis"),
                    "confidence": row.get("confidence"),
                    "status_vs_last_week": row.get("status_vs_last_week"),
                    "events": [pack for pack in packed_events if pack["event_id"] in set(row.get("event_ids") or [])],
                }
                for row in ambiguous
            ],
        }
        review, _ = invoke(
            "TopicCritic",
            "critique-topics",
            critique_payload,
            lambda raw: check_topic_critique(raw, {str(row["topic_id"]) for row in ambiguous}),
        )
        verdicts = critic_verdicts_by_id(review.get("reviews") or [])

    signatures_by_event = {
        str(event.get("event_id")): event.get("signature") or {}
        for event in [*elevated, *near]
        if event.get("event_id")
    }
    applied = apply_topic_resolution(
        topics_raw,
        elevated_ids=elevated_ids,
        near_ids=near_ids,
        prior_keys=prior_keys,
        signatures_by_event=signatures_by_event,
        run_date=run_date,
        critic_verdicts=verdicts,
    )
    # Merge contract-level unassigned with apply unassigned.
    contract_unassigned = [str(eid) for eid in response.get("unassigned") or [] if eid in elevated_ids]
    unassigned = list(dict.fromkeys([*contract_unassigned, *applied["unassigned"]]))
    # Drop unassigned that landed in a validated topic.
    assigned = {eid for topic in applied["topics"] for eid in topic.get("elevated_event_ids") or []}
    unassigned = [eid for eid in unassigned if eid not in assigned]

    themes = apply_theme_candidates(
        list(response.get("theme_candidates") or []),
        near_ids=near_ids,
        elevated_ids=elevated_ids,
    )

    persisted: list[dict[str, Any]] = []
    for topic in applied["topics"]:
        row = pool.add_topic_resolution(
            topic_key=topic["topic_key"],
            run_id=run_id,
            label=topic["label"],
            event_ids=topic["event_ids"],
            basis=topic["basis"],
            common_ste_path=topic.get("common_ste_path") or "",
            differences=topic.get("differences") or "",
            status_vs_last_week=topic["status_vs_last_week"],
            confidence=float(topic["confidence"]),
            status="VALIDATED",
            input_hash=digest,
            model=model,
            prompt_version=PROMPT_VERSION,
            critic_verdict=topic.get("critic_verdict"),
        )
        persisted.append(row)

    for topic in applied["rejected"]:
        key = str(topic.get("topic_key") or "REJECTED")
        if key.upper() == "NEW" or not key:
            key = f"rejected-{digest[:12]}"
        pool.add_topic_resolution(
            topic_key=key,
            run_id=run_id,
            label=str(topic.get("label") or ""),
            event_ids=list(topic.get("event_ids") or []),
            basis=str(topic.get("basis") or topic.get("reject_reason") or ""),
            common_ste_path=str(topic.get("common_ste_path") or ""),
            differences=str(topic.get("differences") or ""),
            status_vs_last_week=str(topic.get("status_vs_last_week") or "NEW"),
            confidence=float(topic.get("confidence") or 0),
            status=str(topic.get("status") or "REJECTED"),
            input_hash=digest,
            model=model,
            prompt_version=PROMPT_VERSION,
            critic_verdict=topic.get("critic_verdict") or {"reject_reason": topic.get("reject_reason")},
        )

    for theme in themes:
        pool.add_topic_resolution(
            topic_key=f"theme-{digest[:12]}-{theme['event_ids'][0][:12]}",
            run_id=run_id,
            label="Emerging theme (not elevated)",
            event_ids=theme["event_ids"],
            basis=theme["basis"],
            common_ste_path="",
            differences="",
            status_vs_last_week="NEW",
            confidence=float(theme["confidence"]),
            status="THEME_CANDIDATE",
            input_hash=digest,
            model=model,
            prompt_version=PROMPT_VERSION,
            critic_verdict={"band_unchanged": True},
        )

    for eid in unassigned:
        pool.add_topic_resolution(
            topic_key=f"unassigned-{eid}",
            run_id=run_id,
            label="Unassigned elevated",
            event_ids=[eid],
            basis="Unassigned by TopicResolver / apply rules",
            common_ste_path="",
            differences="",
            status_vs_last_week="NEW",
            confidence=0.0,
            status="UNASSIGNED",
            input_hash=digest,
            model=model,
            prompt_version=PROMPT_VERSION,
            critic_verdict=None,
        )

    ledger_rows = pool.topic_resolutions_for_run(run_id, validated_only=False)
    ledger = topic_ledger_hash([row for row in ledger_rows if row.get("status") == "VALIDATED"])
    try:
        pool.update_run_config(
            run_id,
            topic_stats={
                "elevated": len(elevated),
                "near": len(near),
                "topics": len(applied["topics"]),
                "unassigned": len(unassigned),
                "themes": len(themes),
                "critic": critic_count,
                "rejected": len(applied["rejected"]),
                "input_hash": digest,
                "ledger_hash": ledger,
            },
            topic_ledger_hash=ledger,
            emerging_themes=themes,
        )
    except Exception:
        pass

    return {
        "enabled": 1,
        "elevated": len(elevated),
        "near": len(near),
        "topics": len(applied["topics"]),
        "unassigned": len(unassigned),
        "themes": len(themes),
        "critic": critic_count,
        "rejected": len(applied["rejected"]),
        "input_hash": digest,
        "ledger_hash": ledger,
        "topics_detail": applied["topics"],
        "theme_candidates": themes,
        "unassigned_ids": unassigned,
    }
