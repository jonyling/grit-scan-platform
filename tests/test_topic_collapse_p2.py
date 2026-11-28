"""Phase P2 topic collapse: contracts, apply rules, storage, package hash, no band drift."""

from __future__ import annotations

from pathlib import Path

import pytest

from grit.pool import Pool


def _sig(**overrides):
    base = {
        "actor": ["Houthi"],
        "action_class": "armed_attack",
        "action_text": "strike",
        "location": ["Saudi Arabia"],
        "date_window": ["2026-11-24", "2026-11-25"],
        "consequence_class": "security_escalation",
        "object": ["airport"],
        "signature_source": "fallback",
    }
    base.update(overrides)
    return base


def test_topic_resolutions_table_is_append_only(tmp_path: Path):
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-25", pack="ste", kind="READER_SCAN", config={"topic_collapse": 1})
    row = pool.add_topic_resolution(
        topic_key="middle-east-security-escalation-2026-11-25",
        run_id="R",
        label="Middle East security escalation",
        event_ids=["EVT-a", "EVT-b"],
        basis="Shared Houthi attack mechanism and regional STE exposure",
        common_ste_path="staff travel Gulf",
        differences="One strike vs alert",
        status_vs_last_week="NEW",
        confidence=0.9,
        status="VALIDATED",
        input_hash="h1",
    )
    assert row["event_ids"] == ["EVT-a", "EVT-b"]
    with pytest.raises(Exception, match="append-only"):
        with pool.transaction() as conn:
            conn.execute(
                "UPDATE topic_resolutions SET status = 'REJECTED' WHERE topic_row_id = ?",
                (row["topic_row_id"],),
            )


def test_contract_unknown_id_and_every_elevated_once():
    from grit.agents.topics import ContractError, check_topics

    elevated = {"EVT-a", "EVT-b"}
    allowed = elevated | {"EVT-n"}
    with pytest.raises(ContractError, match="Unknown event_id"):
        check_topics(
            {"topics": [{"topic_key": "NEW", "label": "X", "event_ids": ["EVT-z"], "basis": "shared Houthi attack mechanism",
                         "status_vs_last_week": "NEW", "confidence": 0.9}], "unassigned": []},
            elevated_ids=elevated, allowed_ids=allowed, prior_keys=set(),
        )
    with pytest.raises(ContractError, match="exactly once"):
        check_topics(
            {"topics": [{"topic_key": "NEW", "label": "X", "event_ids": ["EVT-a"], "basis": "shared Houthi attack mechanism",
                         "status_vs_last_week": "NEW", "confidence": 0.9}], "unassigned": []},
            elevated_ids=elevated, allowed_ids=allowed, prior_keys=set(),
        )
    ok = check_topics(
        {"topics": [
            {"topic_key": "NEW", "label": "ME security", "event_ids": ["EVT-a", "EVT-b"],
             "basis": "shared Houthi attack mechanism and STE exposure", "status_vs_last_week": "NEW", "confidence": 0.9},
        ], "unassigned": [], "theme_candidates": []},
        elevated_ids=elevated, allowed_ids=allowed, prior_keys=set(),
    )
    assert len(ok["topics"]) == 1


def test_contract_rejects_controlled_fields_and_continuing_unknown_key():
    from grit.agents.topics import ContractError, check_topics

    elevated = {"EVT-a"}
    with pytest.raises(ContractError, match="controlled"):
        check_topics(
            {"topics": [{"topic_key": "NEW", "label": "X", "event_ids": ["EVT-a"], "basis": "shared actor attack mechanism",
                         "status_vs_last_week": "NEW", "confidence": 0.9, "band": "Escalating"}],
             "unassigned": []},
            elevated_ids=elevated, allowed_ids=elevated, prior_keys=set(),
        )
    with pytest.raises(ContractError, match="CONTINUING"):
        check_topics(
            {"topics": [{"topic_key": "missing-key", "label": "X", "event_ids": ["EVT-a"],
                         "basis": "shared actor attack mechanism", "status_vs_last_week": "CONTINUING", "confidence": 0.9}],
             "unassigned": []},
            elevated_ids=elevated, allowed_ids=elevated, prior_keys={"other-key"},
        )


def test_country_only_basis_rejected():
    from grit.core.topics import basis_is_weak

    assert basis_is_weak("Both events are in Saudi Arabia")
    assert basis_is_weak("same country only")
    assert basis_is_weak("staff travel record links them")
    assert not basis_is_weak(
        "Shared Houthi attack mechanism and STE exposure across alerts",
        signatures=[_sig(), _sig(action_text="alert")],
    )


def test_theme_candidates_never_change_band():
    from grit.agents.topics import ContractError, check_topics
    from grit.core.topics import apply_theme_candidates

    elevated = {"EVT-a"}
    near = {"EVT-n"}
    with pytest.raises(ContractError, match="elevated"):
        check_topics(
            {"topics": [], "unassigned": ["EVT-a"],
             "theme_candidates": [{"event_ids": ["EVT-a"], "basis": "emerging cyber OT pattern", "confidence": 0.9}]},
            elevated_ids=elevated, allowed_ids=elevated | near, prior_keys=set(),
        )
    themes = apply_theme_candidates(
        [{"event_ids": ["EVT-n", "EVT-a"], "basis": "emerging cyber OT pattern across vendors", "confidence": 0.9}],
        near_ids=near, elevated_ids=elevated,
    )
    assert themes and themes[0]["event_ids"] == ["EVT-n"]
    assert themes[0]["band_unchanged"] is True


def test_apply_mints_new_key_and_persists_prior_key():
    from grit.core.topics import apply_topic_resolution, mint_topic_key

    key = mint_topic_key("Middle East security escalation", first_seen="2026-11-25")
    assert key.startswith("middle-east-security-escalation-2026-11-25")
    applied = apply_topic_resolution(
        [{
            "topic_key": "NEW",
            "label": "Middle East security escalation",
            "event_ids": ["EVT-a", "EVT-b"],
            "basis": "Shared Houthi attack mechanism and regional STE exposure",
            "common_ste_path": "Gulf staff",
            "differences": "strike vs alert",
            "status_vs_last_week": "NEW",
            "confidence": 0.91,
        }],
        elevated_ids={"EVT-a", "EVT-b"},
        near_ids=set(),
        prior_keys=set(),
        signatures_by_event={"EVT-a": _sig(), "EVT-b": _sig()},
        run_date="2026-11-25",
    )
    assert len(applied["topics"]) == 1
    assert applied["topics"][0]["topic_key"] == key
    assert applied["topics"][0]["status"] == "VALIDATED"

    continued = apply_topic_resolution(
        [{
            "topic_key": key,
            "label": "Middle East security escalation",
            "event_ids": ["EVT-c"],
            "basis": "Continuing Houthi attack mechanism vs prior week",
            "status_vs_last_week": "CONTINUING",
            "confidence": 0.88,
        }],
        elevated_ids={"EVT-c"},
        near_ids=set(),
        prior_keys={key},
        signatures_by_event={"EVT-c": _sig()},
        run_date="2026-11-26",
    )
    assert continued["topics"][0]["topic_key"] == key
    assert continued["topics"][0]["status_vs_last_week"] == "CONTINUING"


def test_ambiguous_runs_critic_once(tmp_path: Path):
    from grit.pipeline.resolve_topics import resolve_and_persist

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-25", pack="ste", kind="READER_SCAN", config={"topic_collapse": 1})
    events = [
        {"event_id": "EVT-a", "elevated": True, "score": 10, "band": "▲", "lead": {"title": "Houthi strike"},
         "members": [{"signal_id": "S1", "title": "Houthi strike", "publisher": "Reuters"}],
         "signature": _sig()},
        {"event_id": "EVT-b", "elevated": True, "score": 9, "band": "▲", "lead": {"title": "US alert"},
         "members": [{"signal_id": "S2", "title": "US alert", "publisher": "State"}],
         "signature": _sig(action_class="threat_warning", action_text="warn", object=["embassy"])},
    ]
    calls: list[str] = []

    def invoke(role, task, payload, validate):
        calls.append(role)
        if role == "TopicResolver":
            raw = {
                "topics": [{
                    "topic_key": "NEW", "label": "ME security", "event_ids": ["EVT-a", "EVT-b"],
                    "basis": "Shared Houthi / Iran attack mechanism and STE exposure",
                    "common_ste_path": "Gulf", "differences": "strike vs alert",
                    "status_vs_last_week": "NEW", "confidence": 0.70,
                }],
                "unassigned": [],
                "theme_candidates": [],
            }
            return validate(raw), 1
        raw = {"reviews": [{"topic_id": "T1", "action": "UPHOLD", "reason": "same decision frame: regional attack risk"}]}
        return validate(raw), 2

    summary = resolve_and_persist(pool, run_id="R", events=events, invoke=invoke, model="test")
    assert calls == ["TopicResolver", "TopicCritic"]
    assert summary["topics"] == 1
    assert summary["critic"] == 1
    rows = pool.topic_resolutions_for_run("R", validated_only=True)
    assert len(rows) == 1
    assert rows[0]["status_vs_last_week"] == "NEW"


def test_topics_do_not_write_same_or_band(tmp_path: Path):
    from grit.pipeline.resolve_topics import resolve_and_persist

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-25", pack="ste", kind="READER_SCAN",
                   config={"topic_collapse": 1, "event_relations": 1})
    events = [
        {"event_id": "EVT-a", "elevated": True, "score": 10, "band": "▲", "lead": {"title": "OFAC"},
         "members": [{"signal_id": "S1", "title": "OFAC", "publisher": "Treasury"}],
         "signature": _sig(actor=["OFAC"], action_class="sanctions", object=["SDN list"])},
    ]

    def invoke(role, task, payload, validate):
        raw = {
            "topics": [{
                "topic_key": "NEW", "label": "OFAC", "event_ids": ["EVT-a"],
                "basis": "OFAC sanctions designation mechanism",
                "status_vs_last_week": "NEW", "confidence": 0.95,
            }],
            "unassigned": [],
            "theme_candidates": [],
        }
        return validate(raw), 1

    summary = resolve_and_persist(pool, run_id="R", events=events, invoke=invoke)
    assert summary["topics"] == 1
    # No event_relations rows written; event band untouched.
    assert pool.event_relations_for_run("R") == []
    assert events[0]["band"] == "▲"


def test_review_gate_package_hash_includes_topic_ledger(tmp_path: Path):
    from grit.core.canonical import canonical_hash
    from grit.core.topics import topic_ledger_hash

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-25", pack="ste", kind="READER_SCAN",
                   config={"topic_collapse": 1, "weekly_mode": "legacy"})
    pool.add_topic_resolution(
        topic_key="k1", run_id="R", label="L", event_ids=["EVT-a"], basis="shared attack mechanism",
        common_ste_path="", differences="", status_vs_last_week="NEW", confidence=0.9,
        status="VALIDATED", input_hash="h",
    )
    ledger = pool.topic_ledger_hash_for_run("R")
    assert ledger == topic_ledger_hash(pool.topic_resolutions_for_run("R", validated_only=True))
    content_with = {"brief_hash": "b1", "profile": "test", "run": "R", "topic_ledger_hash": ledger}
    content_without = {"brief_hash": "b1", "profile": "test", "run": "R"}
    assert canonical_hash(content_with) != canonical_hash(content_without)


def test_open_escalating_challenge_still_blocks_approve():
    """Regression: topic path must not weaken Escalating challenge ReviewGate hold."""
    from grit.core.review_gate import review_gate

    context = {
        "review_package_id": "PKG-1",
        "review_package_version": 1,
        "review_package_hash": "hash-1",
        "reviewer_id": "jony",
        "reviewer_intent": "APPROVE",
        "correlation_id": "c1",
        "idempotency_key": "i1",
        "authoritative_package_hash": "hash-1",
        "authoritative_membership_hash": "m1",
        "submitted_membership_hash": "m1",
        "package_current_status": "PENDING_REVIEW",
        "package_completeness_known": True,
        "missing_signal_ids": [],
        "reviewer_is_human": True,
        "reviewer_is_eligible": True,
        "separation_of_duties_passed": True,
        "policy_is_active": True,
        "approval_policy_version": "1",
        "configuration_version": "1",
        "function_version": "1.0.0",
        "review_findings": [],
        "unresolved_escalating_challenge_ids": ["CHAL-1"],
        "require_escalating_challenges_resolved": True,
        "topic_ledger_hash": "topics-xyz",
    }
    result = review_gate(context)
    assert result["review_outcome"] == "HOLD"
    assert result["reason_code"] == "OPEN_ESCALATING_CHALLENGES"


def test_flag_off_is_noop(tmp_path: Path):
    from grit.pipeline.resolve_topics import resolve_and_persist

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-25", pack="ste", kind="READER_SCAN", config={})
    summary = resolve_and_persist(
        pool, run_id="R",
        events=[{"event_id": "EVT-a", "elevated": True, "score": 10, "members": [{}], "signature": _sig()}],
        invoke=lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not call")),
    )
    assert summary["enabled"] == 0
