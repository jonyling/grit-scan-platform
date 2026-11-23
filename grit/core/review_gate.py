"""ReviewGate(): the only business approval gate. It does not write state."""

from __future__ import annotations

from datetime import date
from typing import Any

from grit.core.canonical import canonical_hash

FUNCTION_NAME = "ReviewGate"
FUNCTION_VERSION = "1.0.0"
INTENTS = {"APPROVE", "REJECT", "HOLD", "REQUEST_REWORK"}
ALWAYS_REWORK = {"MISSING_SIGNAL", "INCORRECT_CLASSIFICATION", "MISSING_EVIDENCE", "MATERIAL_OMISSION"}
FINDING_FIELDS = ("finding_id", "finding_type", "severity", "description", "resolution_required")
INVOCATION_FIELDS = {"review_timestamp_utc", "invocation_id", "retry_attempt", "signature", "signed_payload_hash"}
EXPOSURE_REQUIRED = {
    "sites": ("entity", "city", "country", "headcount"),
    "travel": ("destination", "country", "travellers"),
    "projects": ("name", "entity", "country"),
    "financial": ("item", "entity", "amount"),
    "alerts": ("date", "location", "country", "summary"),
}
EXPOSURE_DATE_FIELDS = ("safetyConfirmed", "until", "date")


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
    # Escalating-band challenges must be resolved (or policy opted out) before APPROVE.
    open_escalating = list(context.get("unresolved_escalating_challenge_ids") or [])
    if (
        intent == "APPROVE"
        and context.get("package_current_status") == "PENDING_REVIEW"
        and open_escalating
        and context.get("require_escalating_challenges_resolved", True) is not False
    ):
        return _decision(
            context,
            input_hash,
            outcome="HOLD",
            package_status="HELD",
            signal_instruction="NONE",
            next_action="RESOLVE_HOLD",
            reason_code="OPEN_ESCALATING_CHALLENGES",
            material_ids=sorted(str(item) for item in open_escalating),
        )
    exposure_as_of, exposure_stale = _stale_exposure_approval(context)
    if intent == "APPROVE" and context.get("package_current_status") == "PENDING_REVIEW" and exposure_stale:
        return _decision(
            context,
            input_hash,
            outcome="HOLD",
            package_status="HELD",
            signal_instruction="NONE",
            next_action="RESOLVE_HOLD",
            reason_code="EXPOSURE_DATA_STALE",
            reason_summary="exposure data stale",
            material_ids=_physical_threat_override_ids(context),
            as_of=exposure_as_of,
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


def _physical_threat_override_ids(context: dict[str, Any]) -> list[str]:
    value = (
        context.get("kept_tier1_physical_threat_override_ids")
        if "kept_tier1_physical_threat_override_ids" in context
        else context.get("physical_threat_override_signal_ids")
    )
    if isinstance(value, list):
        return sorted(str(item) for item in value if str(item))
    return []


def _stale_exposure_approval(context: dict[str, Any]) -> tuple[str | None, bool]:
    override_ids = _physical_threat_override_ids(context)
    has_override = bool(override_ids) or (
        context.get("has_kept_tier1_physical_threat_override") is True
    )
    if not has_override:
        return None, False

    exposure = context.get("exposure_data", context.get("exposure_snapshot"))
    if not isinstance(exposure, dict):
        return None, True
    from grit.pipeline.exposure_snapshot import exposure_freshness

    as_of, stale = exposure_freshness(exposure, run_date=str(context.get("run_date") or ""))
    if stale:
        return as_of, True
    typed = exposure.get("exposures")
    if typed is not None:
        return as_of, not _valid_typed_exposure_records(typed)
    return as_of, not _valid_exposure_sections(exposure)


def _valid_typed_exposure_records(records: Any) -> bool:
    if not isinstance(records, list) or not records:
        return False
    for row in records:
        if not isinstance(row, dict):
            return False
        if any(row.get(field) in (None, "") for field in ("entity", "status", "snapshot_date", "record_type")):
            return False
        if not _iso_date(str(row.get("snapshot_date") or "")):
            return False
        count = row.get("staff_count")
        if count is not None and (not isinstance(count, int) or isinstance(count, bool) or count < 0):
            return False
        confirmation = row.get("last_safety_confirmation")
        if confirmation and not _iso_date(str(confirmation)):
            return False
    return True


def _valid_exposure_sections(exposure: dict[str, Any]) -> bool:
    present = [(section, exposure[section]) for section in EXPOSURE_REQUIRED if section in exposure]
    if not present or not any(rows for _, rows in present):
        return False
    for section, rows in present:
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict):
                return False
            if any(row.get(field) in (None, "") for field in EXPOSURE_REQUIRED[section]):
                return False
            for field in EXPOSURE_DATE_FIELDS:
                if row.get(field) and not _iso_date(str(row[field])):
                    return False
            if section in {"sites", "travel"}:
                count = row["headcount"] if section == "sites" else row["travellers"]
                if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                    return False
            if section == "alerts":
                nearest = row.get("nearest") or []
                if not isinstance(nearest, list) or any(
                    not isinstance(item, dict)
                    or not item.get("entity")
                    or not isinstance(item.get("distanceKm"), (int, float))
                    for item in nearest
                ):
                    return False
    return True


def _iso_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


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
    reason_summary: str | None = None,
    as_of: str | None = None,
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
        "reason_summary": reason_summary or reason_code,
        "package_id": context.get("review_package_id"),
        "package_version": context.get("review_package_version"),
        "package_hash": context.get("authoritative_package_hash"),
        "input_hash": input_hash,
        "function_name": FUNCTION_NAME,
        "function_version": context.get("function_version") or FUNCTION_VERSION,
        "correlation_id": context.get("correlation_id"),
    }
    if as_of is not None:
        result["asOf"] = as_of
    result["canonical_result_hash"] = canonical_hash(result)
    return result
