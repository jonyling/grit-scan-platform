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


def topic_index_run_id(day: date, pack: str) -> str:
    """Synthetic run id for nightly topic reuse (pack-scoped so weekly prior_topics sees it)."""
    return f"{day.isoformat()}-topics-{pack}"


def ensure_topic_index_run(pool: Pool, day: date, pack: str) -> str:
    """Create or reuse the day's pack-scoped INDEX run for nightly topic attachments."""
    run_id = topic_index_run_id(day, pack)
    existing = pool.run(run_id)
    if existing is None:
        pool.start_run(
            run_id,
            run_date=day.isoformat(),
            pack=pack,
            kind="INDEX",
            config={"lane": "nightly-topics", "topic_collapse": 1},
        )
        pool.finish_run(run_id)
    elif not enabled(existing.get("config") or {}):
        pool.update_run_config(run_id, topic_collapse=1)
    return run_id


def _event_shell_from_members(
    event_id: str,
    members: Sequence[Mapping[str, Any]],
    *,
    lead_signal_id: str | None = None,
) -> dict[str, Any]:
    group = [dict(row) for row in members]
    lead = next((row for row in group if row.get("signal_id") == lead_signal_id), group[0] if group else {})
    return {"event_id": event_id, "lead": lead, "members": group, "reports": group}


def resolve_nightly_topic_reuse(
    pool: Pool,
    day: date,
    *,
    lookback_days: int = 14,
) -> dict[str, Any]:
    """P3: reuse-only topic assignment overnight.

    Attach new events to existing topic_keys when the event signature matches a child
    of that topic. Never mints NEW topics. No LLM (fallback / cached signatures only).
    """
    from grit.core.canonical import canonical_hash
    from grit.core.event_signature import same_event_candidate, signature_for_event
    from grit.core.topics import PRIOR_TOPIC_WEEKS, PROMPT_VERSION

    since = (day - timedelta(days=max(1, lookback_days))).isoformat()
    topic_since = (day - timedelta(days=PRIOR_TOPIC_WEEKS * 7)).isoformat()

    # Active topics by pack: latest VALIDATED row per topic_key.
    prior_rows = pool.rows(
        """SELECT t.*, r.pack, r.run_date FROM topic_resolutions t
           JOIN runs r ON r.run_id = t.run_id
           WHERE t.status = 'VALIDATED' AND r.run_date >= ? AND r.run_date <= ?
           ORDER BY t.topic_row_id""",
        (topic_since, day.isoformat()),
    )
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in prior_rows:
        pack = str(row.get("pack") or "")
        key = str(row.get("topic_key") or "")
        if pack and key and not key.startswith(("theme-", "unassigned-", "rejected-")):
            latest[(pack, key)] = row
    if not latest:
        summary = {"enabled": 1, "packs": 0, "candidates": 0, "attached": 0, "topics": 0}
        pool.log(actor_type="FUNCTION", actor="NightlyTopicReuse", status="SUCCEEDED", detail=summary)
        return summary

    # Recent events with members published in the lookback window.
    member_rows = pool.rows(
        """SELECT m.event_id, m.signal_id, s.title, s.text, s.publisher,
                  COALESCE(s.published_at, s.first_seen_at) AS published_at,
                  e.lead_signal_id
           FROM event_members m
           JOIN signals s ON s.signal_id = m.signal_id
           JOIN events e ON e.event_id = m.event_id
           WHERE substr(COALESCE(s.published_at, s.first_seen_at), 1, 10) >= ?
           ORDER BY m.event_id, m.signal_id""",
        (since,),
    )
    by_event: dict[str, list[dict[str, Any]]] = {}
    lead_of: dict[str, str | None] = {}
    for row in member_rows:
        by_event.setdefault(row["event_id"], []).append(row)
        lead_of[row["event_id"]] = row.get("lead_signal_id")

    # Child signatures already on active topics (from any member of listed event_ids).
    child_event_ids = {
        str(eid)
        for row in latest.values()
        for eid in (row.get("event_ids") or [])
    }
    child_members = {}
    if child_event_ids:
        marks = ",".join("?" for _ in child_event_ids)
        for row in pool.rows(
            f"""SELECT m.event_id, m.signal_id, s.title, s.text, s.publisher,
                       COALESCE(s.published_at, s.first_seen_at) AS published_at,
                       e.lead_signal_id
                FROM event_members m
                JOIN signals s ON s.signal_id = m.signal_id
                JOIN events e ON e.event_id = m.event_id
                WHERE m.event_id IN ({marks})
                ORDER BY m.event_id, m.signal_id""",
            list(child_event_ids),
        ):
            child_members.setdefault(row["event_id"], []).append(row)

    child_sigs: dict[str, Mapping[str, Any]] = {}
    for eid, members in child_members.items():
        shell = _event_shell_from_members(eid, members, lead_signal_id=(members[0].get("lead_signal_id") if members else None))
        child_sigs[eid] = signature_for_event(shell)

    attached = 0
    candidates = 0
    packs_touched: set[str] = set()
    # Already-listed event_ids per topic (avoid re-attaching the same child).
    listed = {
        (pack, key): set(str(eid) for eid in (row.get("event_ids") or []))
        for (pack, key), row in latest.items()
    }

    # Candidate events = recent events not already listed on any active topic of a pack.
    already = {eid for ids in listed.values() for eid in ids}
    for event_id, members in by_event.items():
        if event_id in already:
            continue
        candidates += 1
        shell = _event_shell_from_members(event_id, members, lead_signal_id=lead_of.get(event_id))
        signature = signature_for_event(shell)
        # Match against each pack's topics independently; first signature hit wins per pack.
        matched: list[tuple[str, str, dict[str, Any]]] = []
        for (pack, key), topic in latest.items():
            if event_id in listed[(pack, key)]:
                continue
            for child_id in listed[(pack, key)]:
                child_sig = child_sigs.get(child_id)
                if child_sig is None:
                    continue
                if same_event_candidate(signature, child_sig):
                    matched.append((pack, key, topic))
                    break
        for pack, key, topic in matched:
            run_id = ensure_topic_index_run(pool, day, pack)
            new_ids = list(dict.fromkeys([*list(topic.get("event_ids") or []), event_id]))
            digest = canonical_hash({
                "contract": CONTRACT,
                "lane": "nightly-reuse",
                "topic_key": key,
                "event_id": event_id,
                "day": day.isoformat(),
            })
            pool.add_topic_resolution(
                topic_key=key,
                run_id=run_id,
                label=str(topic.get("label") or key),
                event_ids=new_ids,
                basis=str(topic.get("basis") or "Nightly signature reuse against existing child"),
                common_ste_path=str(topic.get("common_ste_path") or ""),
                differences=str(topic.get("differences") or ""),
                status_vs_last_week="CONTINUING",
                confidence=float(topic.get("confidence") or 0.80),
                status="VALIDATED",
                input_hash=digest,
                model="nightly-reuse",
                prompt_version=PROMPT_VERSION,
                critic_verdict={"lane": "nightly-reuse", "attached_event_id": event_id, "new_topics": False},
            )
            listed[(pack, key)].add(event_id)
            already.add(event_id)
            attached += 1
            packs_touched.add(pack)

    summary = {
        "enabled": 1,
        "packs": len(packs_touched),
        "candidates": candidates,
        "attached": attached,
        "topics": len(latest),
    }
    pool.log(actor_type="FUNCTION", actor="NightlyTopicReuse", status="SUCCEEDED", detail=summary)
    return summary
