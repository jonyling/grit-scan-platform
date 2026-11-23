from grit.core.review_gate import review_gate


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


def test_approve_is_the_only_path_that_authorises_approval() -> None:
    result = review_gate(_context())
    assert result["review_outcome"] == "APPROVE"
    assert result["package_status_instruction"] == "APPROVED"
    assert result["signal_transition_instruction"] == "APPROVED"
    assert result["approval_eligible"] is True
    assert result["new_package_required"] is False


def test_rework_findings_override_approve_and_cover_the_package() -> None:
    finding = {
        "finding_id": "F-2",
        "finding_type": "MISSING_EVIDENCE",
        "severity": "LOW",
        "description": "Source missing",
        "resolution_required": False,
        "affected_signal_id": "sig-a",
    }
    result = review_gate(_context(review_findings=[finding]))
    assert result["review_outcome"] == "RETURN_FOR_REWORK"
    assert result["signal_transition_instruction"] == "ASSESSED"
    assert result["new_package_required"] is True
    assert result["approval_eligible"] is False
    assert result["material_finding_ids"] == ["F-2"]


def test_reject_and_hold_create_no_signal_transition() -> None:
    rejected = review_gate(_context(reviewer_intent="REJECT"))
    held = review_gate(_context(reviewer_intent="HOLD"))
    assert rejected["signal_transition_instruction"] == "NONE"
    assert rejected["package_status_instruction"] == "REJECTED"
    assert rejected["new_package_required"] is True
    assert held["signal_transition_instruction"] == "NONE"
    assert held["package_status_instruction"] == "HELD"
    assert held["new_package_required"] is False
    assert held["required_next_action"] == "RESOLVE_HOLD"


def test_unverified_reviewer_or_hash_holds_for_reconciliation() -> None:
    human = review_gate(_context(reviewer_is_human=False))
    mismatch = review_gate(_context(review_package_hash="shown-to-reviewer"))
    assert human["review_outcome"] == "HOLD"
    assert human["required_next_action"] == "STOP_AND_RECONCILE"
    assert mismatch["reason_code"] == "PACKAGE_HASH_MISMATCH"
    assert mismatch["signal_transition_instruction"] == "NONE"


def test_review_decision_ignores_invocation_timestamp() -> None:
    first = review_gate(_context())
    second = review_gate(_context(review_timestamp_utc="2026-09-25T09:00:00Z"))
    assert first["canonical_result_hash"] == second["canonical_result_hash"]
    assert first["input_hash"] == second["input_hash"]


def test_kept_physical_threat_override_holds_approval_for_stale_exposure() -> None:
    result = review_gate(_context(
        run_date="2026-09-25",
        kept_tier1_physical_threat_override_ids=["SIG-1"],
        exposure_data={"asOf": "2026-09-17", "sites": [{
            "entity": "STE", "city": "Doha", "country": "Qatar", "headcount": 1,
        }]},
    ))

    assert result["review_outcome"] == "HOLD"
    assert result["reason_code"] == "EXPOSURE_DATA_STALE"
    assert result["reason_summary"] == "exposure data stale"
    assert result["asOf"] == "2026-09-17"
    assert result["material_finding_ids"] == ["SIG-1"]
    assert result["signal_transition_instruction"] == "NONE"


def test_exposure_gate_accepts_exactly_seven_days_and_only_applies_to_physical_overrides() -> None:
    fresh = review_gate(_context(
        run_date="2026-09-25",
        kept_tier1_physical_threat_override_ids=["SIG-1"],
        exposure_data={"asOf": "2026-09-18", "sites": [{
            "entity": "STE", "city": "Doha", "country": "Qatar", "headcount": 1,
        }]},
    ))
    no_override = review_gate(_context(
        run_date="2026-09-25",
        kept_tier1_physical_threat_override_ids=[],
        exposure_data=None,
    ))

    assert fresh["review_outcome"] == "APPROVE"
    assert no_override["review_outcome"] == "APPROVE"


def test_missing_invalid_and_not_supplied_exposure_hold_but_other_intents_are_preserved() -> None:
    exposure_cases = [
        None,
        {"asOf": "not-a-date", "sites": [{
            "entity": "STE", "city": "Doha", "country": "Qatar", "headcount": 1,
        }]},
        {"asOf": "2026-09-25"},
        {"asOf": "2026-09-25", "sites": [{}]},
        {"asOf": "2026-09-25", "notSupplied": True},
    ]
    for exposure in exposure_cases:
        result = review_gate(_context(
            run_date="2026-09-25",
            kept_tier1_physical_threat_override_ids=["SIG-1"],
            exposure_data=exposure,
        ))
        assert result["reason_code"] == "EXPOSURE_DATA_STALE", exposure

    rejected = review_gate(_context(
        reviewer_intent="REJECT",
        run_date="2026-09-25",
        kept_tier1_physical_threat_override_ids=["SIG-1"],
        exposure_data=None,
    ))
    assert rejected["review_outcome"] == "REJECT"
    assert rejected["reason_code"] == "REVIEWER_REJECTED"
