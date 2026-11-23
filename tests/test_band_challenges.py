"""P2 ranking quality: band challenge flags + ReviewGate UX."""

from __future__ import annotations

import pytest

from grit.agents.weekly import CONTROLLED, ContractError, check_selections
from grit.core.challenges import (
    MISS_KIND_BAND_RULE_GAP,
    STATUS_ACCEPT_RULE_GAP,
    STATUS_DISMISS,
    STATUS_OPEN,
    STATUS_UPHOLD,
    open_from_editorial,
    package_challenges,
    resolve_challenge,
    review_gate_challenge_block,
    sync_package_challenges,
    unresolved_escalating,
)
from grit.core.overrides import BAND_ELEVATED, BAND_ESCALATING, BAND_WATCH, reader_band_of
from grit.core.review_gate import review_gate
from grit.pool import Pool

CHAIN = {
    "fact": "Strike reported at a staffed site.",
    "exposure": "Staff in country X.",
    "transmission": "People risk at a URS site.",
    "impact": "not established",
    "significance": {"exposure": 2, "consequence": 2, "immediacy": 2, "evidence": 1, "change": 1},
    "reach_channel": "person",
    "urs_reach_reason": "URS staff present in the named country.",
}


def _context(**overrides):
    base = {
        "review_package_id": "RP-1",
        "review_package_version": 1,
        "review_package_hash": "hash-1",
        "reviewer_id": "reviewer-01",
        "reviewer_intent": "APPROVE",
        "review_findings": [],
        "review_timestamp_utc": "2026-09-25T00:00:00Z",
        "correlation_id": "corr-1",
        "idempotency_key": "review-1",
        "authoritative_package_hash": "hash-1",
        "authoritative_membership_hash": "members-1",
        "submitted_membership_hash": "members-1",
        "package_current_status": "PENDING_REVIEW",
        "package_completeness_known": True,
        "missing_signal_ids": [],
        "reviewer_is_human": True,
        "reviewer_is_eligible": True,
        "separation_of_duties_passed": True,
        "policy_is_active": True,
        "approval_policy_version": "v1",
        "configuration_version": "v1",
        "function_version": "1.0.0",
    }
    base.update(overrides)
    return base


def test_challenge_status_is_controlled() -> None:
    assert "challenge_status" in CONTROLLED
    assert "band" in CONTROLLED


def test_challenge_stored_without_moving_band() -> None:
    sources = {"A": {"priority": {"band": BAND_WATCH, "tier": 4}, "tier": 4}}
    row = {
        **CHAIN,
        "signal_id": "A",
        "decision": "INCLUDE",
        "reason": "Named",
        "source_ids": ["A"],
        "challenged_band": BAND_ESCALATING,
        "challenge_reason": "Staff threat should force Escalating via override.",
    }
    checked = check_selections({"selections": [row]}, sources, {"A"})["selections"][0]
    assert checked["challenged_band"] == BAND_ESCALATING
    assert checked["challenge_status"] == STATUS_OPEN
    assert reader_band_of(sources["A"]) == BAND_WATCH
    assert "band" not in checked


def test_model_cannot_write_challenge_status() -> None:
    with pytest.raises(ContractError, match="controlled"):
        check_selections(
            {
                "selections": [
                    {
                        **CHAIN,
                        "signal_id": "A",
                        "decision": "INCLUDE",
                        "reason": "Named",
                        "source_ids": ["A"],
                        "challenged_band": BAND_ESCALATING,
                        "challenge_reason": "Disagree",
                        "challenge_status": STATUS_UPHOLD,
                    }
                ]
            },
            {"A": {"priority": {"band": BAND_WATCH}}},
            {"A"},
        )


def test_challenge_same_as_core_band_rejected() -> None:
    with pytest.raises(ContractError, match="differ"):
        check_selections(
            {
                "selections": [
                    {
                        **CHAIN,
                        "signal_id": "A",
                        "decision": "INCLUDE",
                        "reason": "Named",
                        "source_ids": ["A"],
                        "challenged_band": BAND_WATCH,
                        "challenge_reason": "Same as core.",
                    }
                ]
            },
            {"A": {"priority": {"band": BAND_WATCH}}},
            {"A"},
        )


def test_challenge_reason_required_with_band() -> None:
    with pytest.raises(ContractError, match="challenge_reason"):
        check_selections(
            {
                "selections": [
                    {
                        **CHAIN,
                        "signal_id": "A",
                        "decision": "INCLUDE",
                        "reason": "Named",
                        "source_ids": ["A"],
                        "challenged_band": BAND_ELEVATED,
                    }
                ]
            },
            {"A": {"priority": {"band": BAND_WATCH}}},
            {"A"},
        )


def test_review_gate_holds_open_escalating_challenges() -> None:
    result = review_gate(
        _context(
            unresolved_escalating_challenge_ids=["CHAL-1"],
            require_escalating_challenges_resolved=True,
        )
    )
    assert result["review_outcome"] == "HOLD"
    assert result["reason_code"] == "OPEN_ESCALATING_CHALLENGES"
    assert result["approval_eligible"] is False
    assert "CHAL-1" in result["material_finding_ids"]


def test_review_gate_approves_when_escalating_challenges_resolved() -> None:
    result = review_gate(_context(unresolved_escalating_challenge_ids=[]))
    assert result["review_outcome"] == "APPROVE"
    assert result["approval_eligible"] is True


def test_watch_challenges_do_not_block_approve_policy() -> None:
    challenges = [
        {
            "challenge_id": "CHAL-W",
            "status": STATUS_OPEN,
            "band": BAND_WATCH,
            "challenged_band": BAND_ESCALATING,
        }
    ]
    assert review_gate_challenge_block(challenges) == []
    assert unresolved_escalating(challenges) == []


def _seed_signal(pool: Pool, signal_id: str = "SIG-aaaaaaaaaaaaaaaa") -> None:
    pool.insert(
        "signals",
        {
            "signal_id": signal_id,
            "url": f"https://example.com/{signal_id}",
            "title": "Staffed strike",
            "publisher": "Test",
            "published_at": "2026-11-01",
            "lane": "MANUAL",
            "text": "Strike in a staffed country.",
            "content_hash": signal_id,
            "first_seen_at": "2026-11-01T00:00:00Z",
            "last_seen_at": "2026-11-01T00:00:00Z",
        },
    )


def test_open_resolve_uphold_does_not_move_band(tmp_path) -> None:
    pool = Pool(tmp_path / "pool.db")
    pool.insert(
        "runs",
        {
            "run_id": "2026-11-01-demo",
            "run_date": "2026-11-01",
            "pack": "demo",
            "kind": "READER_SCAN",
            "status": "COMPLETE",
            "started_at": "2026-11-01T00:00:00Z",
            "config_json": {},
        },
    )
    _seed_signal(pool)
    opened = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-aaaaaaaaaaaaaaaa",
        band=BAND_ESCALATING,
        challenged_band=BAND_WATCH,
        challenge_reason="Override over-fired; should be Watch.",
        package_id="PKG-1",
        package_version=1,
    )
    assert opened["status"] == STATUS_OPEN
    resolved = resolve_challenge(
        pool,
        challenge_id=opened["challenge_id"],
        resolution=STATUS_UPHOLD,
        reviewer_id="wilson",
        note="Override stands.",
    )
    assert resolved["status"] == STATUS_UPHOLD
    assert resolved["band"] == BAND_ESCALATING
    standing = pool.band_challenge_by_id(opened["challenge_id"])
    assert standing["status"] == STATUS_UPHOLD
    assert standing["band"] == BAND_ESCALATING


def test_accept_rule_gap_records_miss_without_moving_band(tmp_path) -> None:
    pool = Pool(tmp_path / "pool.db")
    pool.insert(
        "runs",
        {
            "run_id": "2026-11-01-demo",
            "run_date": "2026-11-01",
            "pack": "demo",
            "kind": "READER_SCAN",
            "status": "COMPLETE",
            "started_at": "2026-11-01T00:00:00Z",
            "config_json": {},
        },
    )
    _seed_signal(pool, "SIG-bbbbbbbbbbbbbbbb")
    opened = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-bbbbbbbbbbbbbbbb",
        band=BAND_WATCH,
        challenged_band=BAND_ESCALATING,
        challenge_reason="In-force chip rule missed by OVERRIDE_REG_IN_FORCE.",
        package_id="PKG-2",
        package_version=1,
    )
    resolve_challenge(
        pool,
        challenge_id=opened["challenge_id"],
        resolution=STATUS_ACCEPT_RULE_GAP,
        reviewer_id="wilson",
    )
    standing = pool.band_challenge_by_id(opened["challenge_id"])
    assert standing["status"] == STATUS_ACCEPT_RULE_GAP
    assert standing["band"] == BAND_WATCH  # still not moved
    misses = pool.misses("demo")
    assert len(misses) == 1
    assert misses[0]["kind"] == MISS_KIND_BAND_RULE_GAP
    assert "OVERRIDE" in misses[0]["text"] or "Escalating" in misses[0]["text"]


def test_dismiss_clears_block_for_approve(tmp_path) -> None:
    pool = Pool(tmp_path / "pool.db")
    pool.insert(
        "runs",
        {
            "run_id": "2026-11-01-demo",
            "run_date": "2026-11-01",
            "pack": "demo",
            "kind": "READER_SCAN",
            "status": "COMPLETE",
            "started_at": "2026-11-01T00:00:00Z",
            "config_json": {},
        },
    )
    _seed_signal(pool, "SIG-cccccccccccccccc")
    opened = open_from_editorial(
        pool,
        run_id="2026-11-01-demo",
        signal_id="SIG-cccccccccccccccc",
        band=BAND_ESCALATING,
        challenged_band=BAND_ELEVATED,
        challenge_reason="Noise challenge.",
        package_id="PKG-3",
        package_version=1,
    )
    assert review_gate_challenge_block(package_challenges(pool, "PKG-3", 1)) == [opened["challenge_id"]]
    resolve_challenge(
        pool, challenge_id=opened["challenge_id"], resolution=STATUS_DISMISS, reviewer_id="wilson"
    )
    assert review_gate_challenge_block(package_challenges(pool, "PKG-3", 1)) == []


def test_resolved_challenge_does_not_reopen_for_next_package_version(tmp_path) -> None:
    pool = Pool(tmp_path / "pool.db")
    run_id = "2026-11-01-demo"
    pool.insert(
        "runs",
        {
            "run_id": run_id,
            "run_date": "2026-11-01",
            "pack": "demo",
            "kind": "READER_SCAN",
            "status": "COMPLETE",
            "started_at": "2026-11-01T00:00:00Z",
            "config_json": {},
        },
    )
    _seed_signal(pool, "SIG-eeeeeeeeeeeeeeee")
    opened = open_from_editorial(
        pool,
        run_id=run_id,
        signal_id="SIG-eeeeeeeeeeeeeeee",
        band=BAND_ESCALATING,
        challenged_band=BAND_ELEVATED,
        challenge_reason="Override is too broad.",
        package_id="PKG-5",
        package_version=1,
    )
    resolve_challenge(
        pool, challenge_id=opened["challenge_id"], resolution=STATUS_UPHOLD, reviewer_id="wilson"
    )

    reopened = open_from_editorial(
        pool,
        run_id=run_id,
        signal_id="SIG-eeeeeeeeeeeeeeee",
        band=BAND_ESCALATING,
        challenged_band=BAND_ELEVATED,
        challenge_reason="Override is too broad.",
        package_id="PKG-5",
        package_version=2,
    )

    assert reopened is None
    assert pool.band_challenge(run_id, "SIG-eeeeeeeeeeeeeeee")["status"] == STATUS_UPHOLD
    assert package_challenges(pool, "PKG-5", 2) == []


def test_sync_package_challenges_from_editorial(tmp_path, monkeypatch) -> None:
    pool = Pool(tmp_path / "pool.db")
    run_id = "2026-11-01-demo"
    pool.insert(
        "runs",
        {
            "run_id": run_id,
            "run_date": "2026-11-01",
            "pack": "demo",
            "kind": "READER_SCAN",
            "status": "COMPLETE",
            "started_at": "2026-11-01T00:00:00Z",
            "config_json": {"weekly_mode": "reasoned"},
        },
    )
    signal_id = "SIG-dddddddddddddddd"
    _seed_signal(pool, signal_id)
    pool.insert(
        "assessments",
        {
            "signal_id": signal_id,
            "run_id": run_id,
            "pack": "demo",
            "decision": "KEEP",
            "priority_json": {"band": BAND_WATCH, "tier": 4},
            "tier": 4,
            "assessed_at": "2026-11-01T00:00:00Z",
        },
    )
    # Avoid weekly_assessments path: sync falls back when weekly not ready.
    monkeypatch.setattr(
        pool,
        "weekly_assessments",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("no weekly")),
    )
    # Direct open path used when editorial is on assessments is via sync with assessments+editorial —
    # here we exercise open_from_editorial after a manual editorial-like open:
    open_from_editorial(
        pool,
        run_id=run_id,
        signal_id=signal_id,
        band=BAND_WATCH,
        challenged_band=BAND_ESCALATING,
        challenge_reason="Should escalate.",
    )
    linked = sync_package_challenges(
        pool, package_id="PKG-4", version=2, run_id=run_id, member_ids=[signal_id]
    )
    # Relink OPEN challenge to package (may append a relink row or return None if already linked).
    standing = pool.band_challenge(run_id, signal_id)
    assert standing["status"] == STATUS_OPEN
    assert standing["band"] == BAND_WATCH


def test_rank_instruction_mentions_challenge() -> None:
    from grit.agents.harness import _instruction

    text = _instruction("rank-demo")
    assert "challenged_band" in text
    assert "never write challenge_status" in text.lower() or "Never write challenge_status" in text
