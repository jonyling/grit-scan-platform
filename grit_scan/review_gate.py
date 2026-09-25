"""ReviewGate(): the only business approval gate. It does not write state."""

from __future__ import annotations

from typing import Any

from grit_scan.canonical import canonical_hash

FUNCTION_NAME = "ReviewGate"
FUNCTION_VERSION = "1.0.0"
INTENTS = {"APPROVE", "REJECT", "HOLD", "REQUEST_REWORK"}
ALWAYS_REWORK = {"MISSING_SIGNAL", "INCORRECT_CLASSIFICATION", "MISSING_EVIDENCE", "MATERIAL_OMISSION"}
FINDING_FIELDS = ("finding_id", "finding_type", "severity", "description", "resolution_required")
INVOCATION_FIELDS = {"review_timestamp_utc", "invocation_id", "retry_attempt", "signature", "signed_payload_hash"}


def review_gate(context: dict[str, Any]) -> dict[str, Any]:
    findings = context.get("review_findings")
    canonical = _canonical(context, findings if isinstance(findings, list) else [])
    input_hash = canonical_hash(canonical)
    if context.get("existing_review_found") is True and context.get("existing_input_hash") not in {None, input_hash}:
        return _decision(
            context,
            input_hash,
            outcome="HOLD",
            package_status="HELD",
            signal_instruction="NONE",
            next_action="STOP_AND_RECONCILE",
            reason_code="IDEMPOTENCY_CONFLICT",
            material_ids=[],
        )
    problem = _authority_problem(context, findings)
    if problem is not None:
        return _decision(
            context,
            input_hash,
            outcome="HOLD",
            package_status="HELD",
            signal_instruction="NONE",
            next_action="STOP_AND_RECONCILE",
            reason_code=problem,
            material_ids=[],
        )
    material_ids = _rework_finding_ids(findings)
    missing = list(context.get("missing_signal_ids") or [])
    if context.get("reviewer_intent") == "REQUEST_REWORK" or missing or material_ids:
        return _decision(
            context,
            input_hash,
            outcome="RETURN_FOR_REWORK",
            package_status="RETURNED_FOR_REWORK",
            signal_instruction="ASSESSED",
            next_action="CREATE_NEW_PACKAGE_VERSION",
            reason_code="REWORK_REQUIRED",
            material_ids=material_ids,
            new_package_required=True,
        )
    intent = context.get("reviewer_intent")
    if intent == "REJECT":
        return _decision(
            context,
            input_hash,
            outcome="REJECT",
            package_status="REJECTED",
            signal_instruction="NONE",
            next_action="PERSIST_REJECTION",
            reason_code="REVIEWER_REJECTED",
            material_ids=[],
            new_package_required=True,
        )
    if intent == "HOLD":
        return _decision(
            context,
            input_hash,
            outcome="HOLD",
            package_status="HELD",
            signal_instruction="NONE",
            next_action="RESOLVE_HOLD",
            reason_code="REVIEWER_HOLD",
            material_ids=[],
        )
    if intent == "APPROVE" and context.get("package_current_status") == "PENDING_REVIEW":
        return _decision(
            context,
            input_hash,
            outcome="APPROVE",
            package_status="APPROVED",
            signal_instruction="APPROVED",
            next_action="PERSIST_APPROVAL",
            reason_code="APPROVED",
            material_ids=[],
            approval_eligible=True,
        )
    return _decision(
        context,
        input_hash,
        outcome="HOLD",
        package_status="HELD",
        signal_instruction="NONE",
        next_action="STOP_AND_RECONCILE",
        reason_code="CONTRADICTORY_INPUT",
        material_ids=[],
    )


def _canonical(context: dict[str, Any], findings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        key: value
        for key, value in context.items()
        if key not in INVOCATION_FIELDS and key not in {"existing_review_found", "existing_input_hash", "existing_result"}
    } | {"review_findings": _sorted_findings(findings), "missing_signal_ids": sorted(context.get("missing_signal_ids") or [])}


def _authority_problem(context: dict[str, Any], findings: Any) -> str | None:
    required = (
        "review_package_id",
        "review_package_version",
        "review_package_hash",
        "reviewer_id",
        "reviewer_intent",
        "correlation_id",
        "idempotency_key",
        "authoritative_package_hash",
        "authoritative_membership_hash",
        "submitted_membership_hash",
        "package_current_status",
        "package_completeness_known",
        "missing_signal_ids",
        "reviewer_is_human",
        "reviewer_is_eligible",
        "separation_of_duties_passed",
        "policy_is_active",
        "approval_policy_version",
        "configuration_version",
        "function_version",
    )
    if any(key not in context or context.get(key) is None or context.get(key) == "" for key in required):
        return "INVALID_CONTRACT"
    if context.get("reviewer_intent") not in INTENTS:
        return "INVALID_CONTRACT"
    if not isinstance(findings, list) or not isinstance(context.get("missing_signal_ids"), list):
        return "INVALID_CONTRACT"
    if context.get("review_package_hash") != context.get("authoritative_package_hash"):
        return "PACKAGE_HASH_MISMATCH"
    if context.get("submitted_membership_hash") != context.get("authoritative_membership_hash"):
        return "MEMBERSHIP_HASH_MISMATCH"
    if context.get("policy_is_active") is not True:
        return "INACTIVE_POLICY"
    if context.get("package_completeness_known") is not True:
        return "COMPLETENESS_UNKNOWN"
    if context.get("reviewer_is_human") is not True or context.get("reviewer_is_eligible") is not True:
        return "REVIEWER_NOT_ELIGIBLE"
    if context.get("separation_of_duties_passed") is not True:
        return "SEPARATION_OF_DUTIES_FAILED"
    for finding in findings:
        if not isinstance(finding, dict) or any(field not in finding for field in FINDING_FIELDS):
            return "FINDING_CONTRACT_INVALID"
    return None


def _rework_finding_ids(findings: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for finding in findings:
        kind = finding.get("finding_type")
        rework = kind in ALWAYS_REWORK or (
            kind == "OTHER" and (finding.get("resolution_required") is True or finding.get("severity") == "MATERIAL")
        )
        if rework:
            ids.append(str(finding.get("finding_id")))
    return sorted(ids)


def _sorted_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(findings, key=lambda item: str(item.get("finding_id") or ""))


def _decision(
    context: dict[str, Any],
    input_hash: str,
    *,
    outcome: str,
    package_status: str,
    signal_instruction: str,
    next_action: str,
    reason_code: str,
    material_ids: list[str],
    new_package_required: bool = False,
    approval_eligible: bool = False,
) -> dict[str, Any]:
    result = {
        "review_outcome": outcome,
        "package_status_instruction": package_status,
        "signal_transition_instruction": signal_instruction,
        "required_next_action": next_action,
        "approval_eligible": approval_eligible,
        "new_package_required": new_package_required,
        "missing_signal_ids": sorted(context.get("missing_signal_ids") or []),
        "material_finding_ids": material_ids,
        "reason_code": reason_code,
        "reason_summary": reason_code,
        "package_id": context.get("review_package_id"),
        "package_version": context.get("review_package_version"),
        "package_hash": context.get("authoritative_package_hash"),
        "input_hash": input_hash,
        "function_name": FUNCTION_NAME,
        "function_version": context.get("function_version") or FUNCTION_VERSION,
        "correlation_id": context.get("correlation_id"),
    }
    result["canonical_result_hash"] = canonical_hash(result)
    return result
