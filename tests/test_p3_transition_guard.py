from grit.core.transition_guard import transition_guard


def _envelope(**overrides):
    base = {
        "signal_id": "sig-1",
        "execution_id": "exec-1",
        "execution_version": 1,
        "correlation_id": "corr-1",
        "transition_reason_code": "RC_OK",
        "actor_type": "SUPERVISOR",
        "actor_id": "supervisor-01",
        "expected_transition_sequence": 5,
        "authoritative_latest_transition_sequence": 4,
        "authoritative_current_state_row_id": "row-4",
        "supplied_parent_transition_row_id": "row-4",
        "current_state_projection_matches_ledger": True,
        "configuration_version": "v1",
        "supervisor_version": "v1",
        "state_graph_version": "v1",
        "function_version": "1.0.0",
        "idempotency_key": "idem-1",
        "existing_transition_found": False,
        "trigger_record_exists": True,
        "trigger_record_status": "SUCCEEDED",
        "trigger_record_execution_id": "exec-1",
        "trigger_record_signal_id": "sig-1",
    }
    base.update(overrides)
    return base


def _succeeded(trigger_type: str) -> dict:
    return {
        "trigger_type": trigger_type,
        "trigger_record_exists": True,
        "trigger_record_status": "SUCCEEDED",
        "trigger_record_execution_id": "exec-1",
        "trigger_record_signal_id": "sig-1",
    }


def test_spec_20_section_12_matrix() -> None:
    cases = [
        ("NEW -> FETCHED", "ALLOW", None, {"current_state": "NEW", "requested_state": "FETCHED", "transition_type": "ADVANCE", **_succeeded("FUNCTION_EXECUTION")}),
        ("FETCHED -> DEDUPED", "ALLOW", None, {"current_state": "FETCHED", "requested_state": "DEDUPED", "transition_type": "ADVANCE", **_succeeded("FUNCTION_EXECUTION")}),
        ("DEDUPED -> ASSESSED", "ALLOW", None, {"current_state": "DEDUPED", "requested_state": "ASSESSED", "transition_type": "ADVANCE", **_succeeded("AGENT_EXECUTION")}),
        ("ASSESSED -> CRITIQUED", "ALLOW", None, {"current_state": "ASSESSED", "requested_state": "CRITIQUED", "transition_type": "ADVANCE", **_succeeded("AGENT_EXECUTION"), "final_score_available": True}),
        ("ScoreRisk only", "DENY", "EVIDENCE_NOT_COMPLETE", {"current_state": "ASSESSED", "requested_state": "CRITIQUED", "transition_type": "ADVANCE", **_succeeded("FUNCTION_EXECUTION"), "trigger_function_name": "ScoreRisk", "score_risk_only": True}),
        ("immutable package", "ALLOW", None, {"current_state": "CRITIQUED", "requested_state": "PENDING_REVIEW", "transition_type": "ADVANCE", "trigger_type": "OUTPUT_PACKAGE", "current_package_membership_contains_signal": True, "trigger_package_status": "PENDING_REVIEW", "trigger_package_hash": "h1", "trigger_package_membership_hash": "m1"}),
        ("exact approval", "ALLOW", None, {"current_state": "PENDING_REVIEW", "requested_state": "APPROVED", "transition_type": "ADVANCE", "trigger_type": "HUMAN_APPROVAL", "reviewgate_outcome": "APPROVE", "reviewgate_package_status_instruction": "APPROVED", "reviewgate_signal_transition_instruction": "APPROVED", "trigger_package_id": "p1", "trigger_package_version": 2, "trigger_package_hash": "h1", "trigger_package_membership_hash": "m1", "trigger_approval_package_id": "p1", "trigger_approval_package_version": 2, "trigger_approval_package_hash": "h1", "trigger_approval_membership_hash": "m1", "current_package_membership_contains_signal": True}),
        ("package-wide rework", "ALLOW", None, {"current_state": "PENDING_REVIEW", "requested_state": "ASSESSED", "transition_type": "REWORK", **_succeeded("FUNCTION_EXECUTION"), "reviewgate_outcome": "RETURN_FOR_REWORK", "reviewgate_package_status_instruction": "RETURNED_FOR_REWORK", "reviewgate_signal_transition_instruction": "ASSESSED", "current_package_membership_contains_signal": True, "all_package_members_rework_requested": True, "reviewgate_new_package_required": True}),
        ("publication confirmed", "ALLOW", None, {"current_state": "APPROVED", "requested_state": "PUBLICATION_CONFIRMED", "transition_type": "ADVANCE", "trigger_type": "PUBLICATION_CONFIRMATION", "publication_confirmation_verified": True, "trigger_approval_package_id": "p1", "trigger_approval_package_version": 2, "trigger_approval_package_hash": "h1", "publication_confirmation_package_id": "p1", "publication_confirmation_package_version": 2, "publication_confirmation_package_hash": "h1"}),
        ("airis independent", "ALLOW", None, {"current_state": "APPROVED", "requested_state": "AIRIS_HANDOFF_READY", "transition_type": "ADVANCE", "trigger_type": "OUTPUT_PACKAGE", "airis_handoff_payload_valid": True, "trigger_approval_package_id": "p1", "trigger_approval_package_version": 2, "trigger_approval_package_hash": "h1", "airis_handoff_package_id": "p1", "airis_handoff_package_version": 2, "airis_handoff_package_hash": "h1"}),
        ("reject creates no rework transition", "DENY", "REVIEWGATE_OUTCOME_MISMATCH", {"current_state": "PENDING_REVIEW", "requested_state": "ASSESSED", "transition_type": "REWORK", **_succeeded("FUNCTION_EXECUTION"), "reviewgate_outcome": "REJECT", "reviewgate_package_status_instruction": "REJECTED", "reviewgate_signal_transition_instruction": "NONE", "current_package_membership_contains_signal": True, "all_package_members_rework_requested": True, "reviewgate_new_package_required": True}),
        ("non-enumerated pair", "DENY", "ILLEGAL_TRANSITION", {"current_state": "NEW", "requested_state": "APPROVED", "transition_type": "ADVANCE", "trigger_type": "FUNCTION_EXECUTION"}),
        ("agent actor", "DENY", "INVALID_ACTOR", {"current_state": "NEW", "requested_state": "FETCHED", "transition_type": "ADVANCE", "trigger_type": "FUNCTION_EXECUTION", "actor_type": "AGENT", "actor_id": "researcher-agent"}),
        ("missing trigger record", "DENY", "TRIGGER_RECORD_MISSING", {"current_state": "NEW", "requested_state": "FETCHED", "transition_type": "ADVANCE", "trigger_type": "FUNCTION_EXECUTION", "trigger_record_exists": False}),
        ("wrong package hash", "DENY", "HASH_MISMATCH", {"current_state": "PENDING_REVIEW", "requested_state": "APPROVED", "transition_type": "ADVANCE", "trigger_type": "HUMAN_APPROVAL", "reviewgate_outcome": "APPROVE", "reviewgate_package_status_instruction": "APPROVED", "reviewgate_signal_transition_instruction": "APPROVED", "trigger_package_id": "p1", "trigger_package_version": 2, "trigger_package_hash": "h1", "trigger_package_membership_hash": "m1", "trigger_approval_package_id": "p1", "trigger_approval_package_version": 2, "trigger_approval_package_hash": "WRONG", "trigger_approval_membership_hash": "m1", "current_package_membership_contains_signal": True}),
        ("stale sequence", "DENY", "STALE_SEQUENCE", {"current_state": "NEW", "requested_state": "FETCHED", "transition_type": "ADVANCE", **_succeeded("FUNCTION_EXECUTION"), "expected_transition_sequence": 9}),
        ("restore trusted source", "ALLOW", None, {"current_state": "FAILED", "requested_state": "FETCHED", "transition_type": "RESTORE", "trigger_type": "SYSTEM_EVENT", "restoration_source_is_authoritative": True, "restoration_source_state": "FETCHED", "restoration_source_row_id": "row-2"}),
        ("restore without trusted source", "DENY", "RESTORATION_SOURCE_INVALID", {"current_state": "FAILED", "requested_state": "APPROVED", "transition_type": "RESTORE", "trigger_type": "SYSTEM_EVENT", "restoration_source_is_authoritative": False, "restoration_source_state": "APPROVED", "restoration_source_row_id": "row-2"}),
    ]
    for name, expected, reason, overrides in cases:
        result = transition_guard(_envelope(**overrides))
        assert result["decision"] == expected, name
        if reason is not None:
            assert result["reason_code"] == reason, name
        if expected == "DENY" and reason == "STALE_SEQUENCE":
            assert result["required_next_action"] == "STOP_AND_RECONCILE"


def test_exact_duplicate_is_already_applied_and_conflict_denies() -> None:
    original = _envelope(current_state="NEW", requested_state="FETCHED", transition_type="ADVANCE", **_succeeded("FUNCTION_EXECUTION"))
    first = transition_guard(original)
    duplicate = transition_guard(original | {"existing_transition_found": True, "existing_transition_input_hash": first["input_hash"], "existing_transition_row_id": "row-1"})
    assert duplicate["decision"] == "ALREADY_APPLIED"
    assert duplicate["persistence_instruction"] == "RETURN_EXISTING_TRANSITION"
    conflict = transition_guard(original | {"transition_reason_code": "OTHER", "existing_transition_found": True, "existing_transition_input_hash": first["input_hash"]})
    assert conflict["decision"] == "DENY"
    assert conflict["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["required_next_action"] == "STOP_AND_RECONCILE"


def test_same_input_has_the_same_result_hash() -> None:
    envelope = _envelope(current_state="NEW", requested_state="FETCHED", transition_type="ADVANCE", **_succeeded("FUNCTION_EXECUTION"))
    assert transition_guard(envelope)["canonical_result_hash"] == transition_guard(dict(envelope))["canonical_result_hash"]


def test_publication_lock_is_not_approval_evidence() -> None:
    result = transition_guard(
        _envelope(
            current_state="APPROVED",
            requested_state="PUBLICATION_CONFIRMED",
            transition_type="ADVANCE",
            trigger_type="PUBLICATION_CONFIRMATION",
            publication_confirmation_verified=False,
            publication_concurrency_lock_held=True,
            trigger_approval_package_hash="h1",
        )
    )
    assert result["decision"] == "DENY"
    assert result["reason_code"] == "PUBLICATION_CONFIRMATION_INVALID"


def test_wrong_transition_type_is_not_an_illegal_pair() -> None:
    result = transition_guard(_envelope(current_state="NEW", requested_state="FETCHED", transition_type="REWORK", **_succeeded("FUNCTION_EXECUTION")))
    assert result["decision"] == "DENY"
    assert result["reason_code"] == "INVALID_TRANSITION_TYPE"
