"""Screen actions. ReviewGate decides. The Supervisor persists. This module writes nothing."""

from __future__ import annotations

from typing import Any

from grit.core.review_gate import review_gate
from grit.workflow.supervisor import Supervisor


def submit_from_screen(
    supervisor: Supervisor,
    *,
    package_id: str,
    reviewer_id: str,
    reviewer_intent: str,
    findings: list[dict[str, Any]],
    correlation_id: str,
    accepted_at: str,
    reviewer_is_human: bool = True,
    reviewer_is_eligible: bool = True,
    separation_of_duties_passed: bool = True,
) -> dict[str, Any]:
    context = supervisor.review_context(
        package_id,
        reviewer_id=reviewer_id,
        reviewer_intent=reviewer_intent,
        findings=findings,
        correlation_id=correlation_id,
        reviewer_is_human=reviewer_is_human,
        reviewer_is_eligible=reviewer_is_eligible,
        separation_of_duties_passed=separation_of_duties_passed,
    )
    decision = review_gate(context)
    persisted = supervisor.submit_review(
        package_id,
        reviewer_id=reviewer_id,
        reviewer_intent=reviewer_intent,
        findings=findings,
        correlation_id=correlation_id,
        accepted_at=accepted_at,
        reviewer_is_human=reviewer_is_human,
        reviewer_is_eligible=reviewer_is_eligible,
        separation_of_duties_passed=separation_of_duties_passed,
    )
    if decision["canonical_result_hash"] != persisted["canonical_result_hash"]:
        raise RuntimeError("Review screen decision diverged from the Supervisor")
    approval = next((item for item in supervisor.approvals if item["package_id"] == package_id), None)
    return {"decision": decision, "persisted": persisted, "approval": approval}
