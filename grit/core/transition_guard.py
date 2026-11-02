"""TransitionGuard(): pure workflow validation. It does not read or write state."""

from __future__ import annotations

from typing import Any

from grit.core.canonical import canonical_hash

FUNCTION_NAME = "TransitionGuard"
FUNCTION_VERSION = "1.0.0"

STATES = (
    "NEW",
    "FETCHED",
    "DEDUPED",
    "ASSESSED",
    "CRITIQUED",
    "PENDING_REVIEW",
    "APPROVED",
    "PUBLICATION_CONFIRMED",
    "AIRIS_HANDOFF_READY",
    "FAILED",
)
TERMINAL_STATES = {"PUBLICATION_CONFIRMED", "AIRIS_HANDOFF_READY"}
TRANSITION_TYPES = {"START", "ADVANCE", "REWORK", "RESTORE", "FAIL"}
TRIGGER_TYPES = {
    "FUNCTION_EXECUTION",
    "AGENT_EXECUTION",
    "HUMAN_APPROVAL",
    "OUTPUT_PACKAGE",
    "SYSTEM_EVENT",
    "PUBLICATION_CONFIRMATION",
}
EDGES = {
    ("NEW", "FETCHED"): "ADVANCE",
    ("FETCHED", "DEDUPED"): "ADVANCE",
    ("DEDUPED", "ASSESSED"): "ADVANCE",
    ("ASSESSED", "CRITIQUED"): "ADVANCE",
    ("CRITIQUED", "PENDING_REVIEW"): "ADVANCE",
    ("PENDING_REVIEW", "APPROVED"): "ADVANCE",
    ("PENDING_REVIEW", "ASSESSED"): "REWORK",
    ("APPROVED", "PUBLICATION_CONFIRMED"): "ADVANCE",
    ("APPROVED", "AIRIS_HANDOFF_READY"): "ADVANCE",
}
REQUIRED = (
    "signal_id",
    "execution_id",
    "execution_version",
    "correlation_id",
    "current_state",
    "requested_state",
    "transition_type",
    "transition_reason_code",
    "actor_type",
    "actor_id",
    "trigger_type",
    "expected_transition_sequence",
    "authoritative_latest_transition_sequence",
    "current_state_projection_matches_ledger",
    "configuration_version",
    "supervisor_version",
    "state_graph_version",
    "function_version",
    "idempotency_key",
    "existing_transition_found",
)
# existing_transition_* are lookup fields. They are excluded so a replay
# hashes the same as the original request. publication_concurrency_lock_held
# is excluded because a technical lock is not decision evidence.
CANONICAL_FIELDS = (
    "signal_id",
    "execution_id",
    "execution_version",
    "correlation_id",
    "current_state",
    "requested_state",
    "transition_type",
    "transition_reason_code",
    "actor_type",
    "actor_id",
    "trigger_type",
    "trigger_record_id",
    "trigger_record_exists",
    "trigger_record_status",
    "trigger_record_execution_id",
    "trigger_record_signal_id",
    "trigger_function_name",
    "trigger_package_id",
    "trigger_package_version",
    "trigger_package_status",
    "trigger_package_hash",
    "trigger_package_membership_hash",
    "trigger_approval_id",
    "trigger_approval_package_id",
    "trigger_approval_package_version",
    "trigger_approval_package_hash",
    "trigger_approval_membership_hash",
    "current_package_membership_contains_signal",
    "all_package_members_rework_requested",
    "reviewgate_outcome",
    "reviewgate_package_status_instruction",
    "reviewgate_signal_transition_instruction",
    "reviewgate_new_package_required",
    "publication_confirmation_verified",
    "publication_confirmation_package_id",
    "publication_confirmation_package_version",
    "publication_confirmation_package_hash",
    "airis_handoff_payload_valid",
    "airis_handoff_package_id",
    "airis_handoff_package_version",
    "airis_handoff_package_hash",
    "blocking_error_durably_recorded",
    "restoration_source_row_id",
    "restoration_source_state",
    "restoration_source_is_authoritative",
    "score_risk_only",
    "final_score_available",
    "controlled_assessment_status",
    "controlled_assessment_hash",
    "supervisor_validation_passed",
    "expected_transition_sequence",
    "authoritative_latest_transition_sequence",
    "authoritative_current_state_row_id",
    "supplied_parent_transition_row_id",
    "current_state_projection_matches_ledger",
    "configuration_version",
    "supervisor_version",
    "state_graph_version",
    "function_version",
    "idempotency_key",
)
NEXT_ACTION = {
    "INVALID_CONTRACT": "CORRECT_INPUT",
    "INVALID_ACTOR": "CORRECT_INPUT",
    "UNKNOWN_STATE": "CORRECT_INPUT",
    "ILLEGAL_TRANSITION": "CORRECT_INPUT",
    "INVALID_TRANSITION_TYPE": "CORRECT_INPUT",
    "INVALID_TRIGGER_TYPE": "CORRECT_INPUT",
    "TRIGGER_RECORD_MISSING": "CORRECT_INPUT",
    "TRIGGER_RECORD_MISMATCH": "CORRECT_INPUT",
    "EVIDENCE_NOT_COMPLETE": "CORRECT_INPUT",
    "PACKAGE_MEMBERSHIP_MISMATCH": "CORRECT_INPUT",
    "REVIEWGATE_OUTCOME_MISMATCH": "CORRECT_INPUT",
    "HASH_MISMATCH": "CORRECT_INPUT",
    "APPROVAL_PACKAGE_MISMATCH": "CORRECT_INPUT",
    "PUBLICATION_CONFIRMATION_INVALID": "CORRECT_INPUT",
    "AIRIS_PAYLOAD_INVALID": "CORRECT_INPUT",
    "CURRENT_STATE_MISMATCH": "STOP_AND_RECONCILE",
    "STALE_SEQUENCE": "STOP_AND_RECONCILE",
    "PARENT_TRANSITION_MISMATCH": "STOP_AND_RECONCILE",
    "RESTORATION_SOURCE_INVALID": "STOP_AND_RECONCILE",
    "IDEMPOTENCY_CONFLICT": "STOP_AND_RECONCILE",
}


def transition_guard(envelope: dict[str, Any]) -> dict[str, Any]:
    input_hash = canonical_hash({key: envelope.get(key) for key in CANONICAL_FIELDS})
    if _invalid_contract(envelope):
        return _deny(envelope, input_hash, "INVALID_CONTRACT", actor_valid=False)
    actor_valid = envelope.get("actor_type") == "SUPERVISOR" and bool(str(envelope.get("actor_id") or "").strip())
    if not actor_valid:
        return _deny(envelope, input_hash, "INVALID_ACTOR", actor_valid=False)
    if envelope.get("current_state") not in STATES or envelope.get("requested_state") not in STATES:
        return _deny(envelope, input_hash, "UNKNOWN_STATE", actor_valid=True)
    rule = _rule(envelope)
    if rule is None:
        return _deny(envelope, input_hash, "ILLEGAL_TRANSITION", actor_valid=True)
    if envelope.get("transition_type") != rule:
        return _deny(envelope, input_hash, "INVALID_TRANSITION_TYPE", actor_valid=True)

    sequence_valid = envelope.get("expected_transition_sequence") == envelope.get("authoritative_latest_transition_sequence") + 1
    if not sequence_valid:
        return _deny(envelope, input_hash, "STALE_SEQUENCE", actor_valid=True, sequence_valid=False)
    parent_valid = _parent_valid(envelope)
    if not parent_valid:
        return _deny(envelope, input_hash, "PARENT_TRANSITION_MISMATCH", actor_valid=True, sequence_valid=True, parent_valid=False)
    if envelope.get("current_state_projection_matches_ledger") is not True:
        return _deny(
            envelope,
            input_hash,
            "CURRENT_STATE_MISMATCH",
            actor_valid=True,
            sequence_valid=True,
            parent_valid=True,
        )

    # Spec 20 pseudo-code checks idempotency before evidence. Section 12's
    # duplicate case has no evidence fields, so this order is the contract.
    if envelope.get("existing_transition_found") is True:
        if envelope.get("existing_transition_input_hash") == input_hash:
            return _already_applied(envelope, input_hash)
        return _deny(
            envelope,
            input_hash,
            "IDEMPOTENCY_CONFLICT",
            actor_valid=True,
            sequence_valid=True,
            parent_valid=True,
            idempotency_status="CONFLICT",
        )

    evidence_code = _evidence_problem(envelope)
    if evidence_code is not None:
        return _deny(
            envelope,
            input_hash,
            evidence_code,
            actor_valid=True,
            sequence_valid=True,
            parent_valid=True,
            trigger_valid=evidence_code not in {"INVALID_TRIGGER_TYPE", "TRIGGER_RECORD_MISSING", "TRIGGER_RECORD_MISMATCH"},
            evidence_valid=False,
        )
    return _allow(envelope, input_hash, rule)


def _invalid_contract(envelope: dict[str, Any]) -> bool:
    for key in REQUIRED:
        if key not in envelope or envelope.get(key) is None or envelope.get(key) == "":
            return True
    if envelope.get("transition_type") not in TRANSITION_TYPES:
        return True
    if envelope.get("actor_type") is None:
        return True
    if envelope.get("trigger_type") not in TRIGGER_TYPES:
        return True
    if not isinstance(envelope.get("expected_transition_sequence"), int):
        return True
    if not isinstance(envelope.get("authoritative_latest_transition_sequence"), int):
        return True
    if not isinstance(envelope.get("existing_transition_found"), bool):
        return True
    if not isinstance(envelope.get("current_state_projection_matches_ledger"), bool):
        return True
    return False


def _rule(envelope: dict[str, Any]) -> str | None:
    current = envelope.get("current_state")
    requested = envelope.get("requested_state")
    if current not in TERMINAL_STATES and current != "FAILED" and requested == "FAILED":
        return "FAIL"
    if current == "FAILED" and requested != "FAILED" and envelope.get("transition_type") == "RESTORE":
        return "RESTORE"
    return EDGES.get((current, requested))


def _parent_valid(envelope: dict[str, Any]) -> bool:
    parent = envelope.get("supplied_parent_transition_row_id")
    current = envelope.get("authoritative_current_state_row_id")
    return bool(parent) and parent == current


def _evidence_problem(envelope: dict[str, Any]) -> str | None:
    current = envelope.get("current_state")
    requested = envelope.get("requested_state")
    pair = (current, requested)
    if requested == "FAILED":
        return _failed_evidence(envelope)
    if current == "FAILED":
        return _restore_evidence(envelope)
    if envelope.get("trigger_record_exists") is not True:
        # Spec 20 §12. The PowerShell harness used EVIDENCE_NOT_COMPLETE here.
        return "TRIGGER_RECORD_MISSING"
    if pair in {("NEW", "FETCHED"), ("FETCHED", "DEDUPED")}:
        return _function_evidence(envelope, "FUNCTION_EXECUTION")
    if pair == ("DEDUPED", "ASSESSED"):
        problem = _function_evidence(envelope, "AGENT_EXECUTION")
        if problem:
            return problem
        if envelope.get("supervisor_validation_passed") is False:
            return "EVIDENCE_NOT_COMPLETE"
        return None
    if pair == ("ASSESSED", "CRITIQUED"):
        if envelope.get("trigger_type") not in {"AGENT_EXECUTION", "FUNCTION_EXECUTION"}:
            return "INVALID_TRIGGER_TYPE"
        problem = _record_matches(envelope)
        if problem:
            return problem
        if envelope.get("score_risk_only") is True or envelope.get("trigger_function_name") == "ScoreRisk":
            return "EVIDENCE_NOT_COMPLETE"
        outcome = envelope.get("controlled_assessment_status")
        if outcome is not None:
            if outcome not in {"SCORED", "NOT_RUN", "NOT_ASSESSED", "INSUFFICIENT_DATA"} or not envelope.get("controlled_assessment_hash"):
                return "EVIDENCE_NOT_COMPLETE"
        elif envelope.get("final_score_available") is False:
            return "EVIDENCE_NOT_COMPLETE"
        return None
    if pair == ("CRITIQUED", "PENDING_REVIEW"):
        if envelope.get("trigger_type") != "OUTPUT_PACKAGE":
            return "INVALID_TRIGGER_TYPE"
        if envelope.get("current_package_membership_contains_signal") is not True:
            return "PACKAGE_MEMBERSHIP_MISMATCH"
        if envelope.get("trigger_package_status") != "PENDING_REVIEW":
            return "PACKAGE_MEMBERSHIP_MISMATCH"
        if not envelope.get("trigger_package_hash") or not envelope.get("trigger_package_membership_hash"):
            return "HASH_MISMATCH"
        return None
    if pair == ("PENDING_REVIEW", "APPROVED"):
        return _approval_evidence(envelope)
    if pair == ("PENDING_REVIEW", "ASSESSED"):
        return _rework_evidence(envelope)
    if pair == ("APPROVED", "PUBLICATION_CONFIRMED"):
        return _publication_evidence(envelope)
    if pair == ("APPROVED", "AIRIS_HANDOFF_READY"):
        return _airis_evidence(envelope)
    return "ILLEGAL_TRANSITION"


def _failed_evidence(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_type") not in {"FUNCTION_EXECUTION", "AGENT_EXECUTION", "SYSTEM_EVENT"}:
        return "INVALID_TRIGGER_TYPE"
    if envelope.get("blocking_error_durably_recorded") is not True or not str(envelope.get("transition_reason_code") or "").strip():
        return "EVIDENCE_NOT_COMPLETE"
    if envelope.get("trigger_record_signal_id") not in {None, envelope.get("signal_id")}:
        return "TRIGGER_RECORD_MISMATCH"
    if envelope.get("trigger_record_execution_id") not in {None, envelope.get("execution_id")}:
        return "TRIGGER_RECORD_MISMATCH"
    return None


def _restore_evidence(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_type") != "SYSTEM_EVENT":
        return "INVALID_TRIGGER_TYPE"
    source_state = envelope.get("restoration_source_state")
    if (
        envelope.get("restoration_source_is_authoritative") is not True
        or source_state in {None, "FAILED"}
        or source_state != envelope.get("requested_state")
        or not envelope.get("restoration_source_row_id")
    ):
        return "RESTORATION_SOURCE_INVALID"
    return None


def _function_evidence(envelope: dict[str, Any], trigger_type: str) -> str | None:
    if envelope.get("trigger_type") != trigger_type:
        return "INVALID_TRIGGER_TYPE"
    return _record_matches(envelope)


def _record_matches(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_record_status") != "SUCCEEDED":
        return "EVIDENCE_NOT_COMPLETE"
    if envelope.get("trigger_record_signal_id") != envelope.get("signal_id"):
        return "TRIGGER_RECORD_MISMATCH"
    if envelope.get("trigger_record_execution_id") != envelope.get("execution_id"):
        return "TRIGGER_RECORD_MISMATCH"
    return None


def _approval_evidence(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_type") != "HUMAN_APPROVAL":
        return "INVALID_TRIGGER_TYPE"
    if envelope.get("current_package_membership_contains_signal") is not True:
        return "PACKAGE_MEMBERSHIP_MISMATCH"
    if (
        envelope.get("reviewgate_outcome") != "APPROVE"
        or envelope.get("reviewgate_package_status_instruction") != "APPROVED"
        or envelope.get("reviewgate_signal_transition_instruction") != "APPROVED"
    ):
        return "REVIEWGATE_OUTCOME_MISMATCH"
    if (
        envelope.get("trigger_approval_package_id") != envelope.get("trigger_package_id")
        or envelope.get("trigger_approval_package_version") != envelope.get("trigger_package_version")
    ):
        return "APPROVAL_PACKAGE_MISMATCH"
    if (
        envelope.get("trigger_approval_package_hash") != envelope.get("trigger_package_hash")
        or envelope.get("trigger_approval_membership_hash") != envelope.get("trigger_package_membership_hash")
    ):
        return "HASH_MISMATCH"
    if not envelope.get("trigger_package_hash") or not envelope.get("trigger_approval_package_hash"):
        return "HASH_MISMATCH"
    return None


def _rework_evidence(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_type") != "FUNCTION_EXECUTION":
        return "INVALID_TRIGGER_TYPE"
    if (
        envelope.get("reviewgate_outcome") != "RETURN_FOR_REWORK"
        or envelope.get("reviewgate_package_status_instruction") != "RETURNED_FOR_REWORK"
        or envelope.get("reviewgate_signal_transition_instruction") != "ASSESSED"
        or envelope.get("current_package_membership_contains_signal") is not True
        or envelope.get("all_package_members_rework_requested") is not True
        or envelope.get("reviewgate_new_package_required") is not True
    ):
        return "REVIEWGATE_OUTCOME_MISMATCH"
    return _record_matches(envelope)


def _publication_evidence(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_type") != "PUBLICATION_CONFIRMATION":
        return "INVALID_TRIGGER_TYPE"
    confirmed = (
        envelope.get("publication_confirmation_verified") is True
        and envelope.get("publication_confirmation_package_id") == envelope.get("trigger_approval_package_id")
        and envelope.get("publication_confirmation_package_version") == envelope.get("trigger_approval_package_version")
        and envelope.get("publication_confirmation_package_hash") == envelope.get("trigger_approval_package_hash")
        and bool(envelope.get("trigger_approval_package_hash"))
    )
    if not confirmed:
        return "PUBLICATION_CONFIRMATION_INVALID"
    return None


def _airis_evidence(envelope: dict[str, Any]) -> str | None:
    if envelope.get("trigger_type") != "OUTPUT_PACKAGE":
        return "INVALID_TRIGGER_TYPE"
    valid = (
        envelope.get("airis_handoff_payload_valid") is True
        and envelope.get("airis_handoff_package_id") == envelope.get("trigger_approval_package_id")
        and envelope.get("airis_handoff_package_version") == envelope.get("trigger_approval_package_version")
        and envelope.get("airis_handoff_package_hash") == envelope.get("trigger_approval_package_hash")
        and bool(envelope.get("trigger_approval_package_hash"))
    )
    if not valid:
        return "AIRIS_PAYLOAD_INVALID"
    return None


def _deny(
    envelope: dict[str, Any],
    input_hash: str,
    reason_code: str,
    *,
    actor_valid: bool,
    sequence_valid: bool | None = None,
    parent_valid: bool | None = None,
    trigger_valid: bool | None = None,
    evidence_valid: bool | None = None,
    idempotency_status: str = "NEW",
) -> dict[str, Any]:
    return _result(
        envelope,
        input_hash,
        decision="DENY",
        transition_valid=False,
        reason_code=reason_code,
        actor_valid=actor_valid,
        trigger_valid=trigger_valid,
        sequence_valid=sequence_valid,
        parent_valid=parent_valid,
        evidence_valid=evidence_valid,
        idempotency_status=idempotency_status,
        persistence_instruction="DO_NOT_PERSIST",
        required_next_action=NEXT_ACTION[reason_code],
    )


def _already_applied(envelope: dict[str, Any], input_hash: str) -> dict[str, Any]:
    return _result(
        envelope,
        input_hash,
        decision="ALREADY_APPLIED",
        transition_valid=False,
        reason_code="ALREADY_APPLIED",
        actor_valid=True,
        trigger_valid=None,
        sequence_valid=True,
        parent_valid=True,
        evidence_valid=None,
        idempotency_status="ALREADY_APPLIED",
        persistence_instruction="RETURN_EXISTING_TRANSITION",
        required_next_action="RETURN_EXISTING_RESULT",
    )


def _allow(envelope: dict[str, Any], input_hash: str, rule: str) -> dict[str, Any]:
    return _result(
        envelope,
        input_hash,
        decision="ALLOW",
        transition_valid=True,
        reason_code="TRANSITION_PERMITTED",
        actor_valid=True,
        trigger_valid=True,
        sequence_valid=True,
        parent_valid=True,
        evidence_valid=True,
        idempotency_status="NEW",
        persistence_instruction="PERSIST_TRANSITION",
        required_next_action="CONTINUE_WORKFLOW",
        required_trigger_type=envelope.get("trigger_type"),
        validated_trigger_record_id=envelope.get("trigger_record_id"),
    )


def _result(
    envelope: dict[str, Any],
    input_hash: str,
    *,
    decision: str,
    transition_valid: bool,
    reason_code: str,
    actor_valid: bool,
    trigger_valid: bool | None,
    sequence_valid: bool | None,
    parent_valid: bool | None,
    evidence_valid: bool | None,
    idempotency_status: str,
    persistence_instruction: str,
    required_next_action: str,
    required_trigger_type: str | None = None,
    validated_trigger_record_id: str | None = None,
) -> dict[str, Any]:
    result = {
        "decision": decision,
        "transition_valid": transition_valid,
        "from_state": envelope.get("current_state"),
        "requested_to_state": envelope.get("requested_state"),
        "transition_type": envelope.get("transition_type"),
        "reason_code": reason_code,
        "reason_summary": reason_code,
        "required_trigger_type": required_trigger_type,
        "validated_trigger_record_id": validated_trigger_record_id,
        "actor_valid": actor_valid,
        "trigger_valid": trigger_valid,
        "sequence_valid": sequence_valid,
        "parent_valid": parent_valid,
        "evidence_valid": evidence_valid,
        "idempotency_status": idempotency_status,
        "persistence_instruction": persistence_instruction,
        "required_next_action": required_next_action,
        "expected_transition_sequence": envelope.get("expected_transition_sequence"),
        "input_hash": input_hash,
        "function_name": FUNCTION_NAME,
        "function_version": envelope.get("function_version"),
        "state_graph_version": envelope.get("state_graph_version"),
        "correlation_id": envelope.get("correlation_id"),
    }
    result["canonical_result_hash"] = canonical_hash({key: value for key, value in result.items()})
    return result
