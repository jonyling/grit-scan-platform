"""One pending package so the review screen can open without Dataverse."""

from __future__ import annotations

from supervisor.service import Supervisor

ACCEPTED_AT = "2026-09-25T00:00:00Z"
CORRELATION_ID = "ui-seed"


def seed_pending_package() -> tuple[Supervisor, str, dict[str, dict[str, object]]]:
    supervisor = Supervisor(actor_id="supervisor-01", known_entity_ids=frozenset({"ENT-KMRT"}))
    _advance(supervisor, "SIG-1", "EXEC-1")
    package = supervisor.create_package(["SIG-1"], package_id="RP-UI-1", version=1, content={"signal": "SIG-1"})
    supervisor.request_transition(
        "SIG-1",
        "EXEC-1",
        requested_state="PENDING_REVIEW",
        transition_type="ADVANCE",
        trigger_type="OUTPUT_PACKAGE",
        trigger_record_id=package.package_id,
        correlation_id=CORRELATION_ID,
        accepted_at=ACCEPTED_AT,
    )
    assessments = {
        "SIG-1": {
            "signal_id": "SIG-1",
            "state": "PENDING_REVIEW",
            "entity": None,
            "lob": "Mobility Rail",
            "severity": "NOT_ASSESSED",
            "driving_dimension": None,
            "financial_basis": "INSUFFICIENT_DATA",
            "transmission": "AMBIENT",
            "evidence": "LOW",
            "horizon": "LATENT",
            "verdict": "NOT_HIGH",
            "opportunity_cost": "NOT_ASSESSED",
            "coverage_gaps": ["value_at_risk"],
        }
    }
    return supervisor, package.package_id, assessments


def _advance(supervisor: Supervisor, signal_id: str, execution_id: str) -> None:
    supervisor.accept_signal(signal_id, execution_id, CORRELATION_ID, accepted_at=ACCEPTED_AT)
    fetch_id = supervisor.record_function(
        signal_id=signal_id,
        execution_id=execution_id,
        function_name="FetchArticle",
        status="SUCCEEDED",
        correlation_id=CORRELATION_ID,
    )
    supervisor.request_transition(
        signal_id,
        execution_id,
        requested_state="FETCHED",
        transition_type="ADVANCE",
        trigger_type="FUNCTION_EXECUTION",
        trigger_record_id=fetch_id,
        correlation_id=CORRELATION_ID,
        accepted_at=ACCEPTED_AT,
    )
    fingerprint_id = supervisor.record_function(
        signal_id=signal_id,
        execution_id=execution_id,
        function_name="FingerprintArticle",
        status="SUCCEEDED",
        correlation_id=CORRELATION_ID,
    )
    supervisor.request_transition(
        signal_id,
        execution_id,
        requested_state="DEDUPED",
        transition_type="ADVANCE",
        trigger_type="FUNCTION_EXECUTION",
        trigger_record_id=fingerprint_id,
        correlation_id=CORRELATION_ID,
        accepted_at=ACCEPTED_AT,
    )
    assessment_id = supervisor.record_agent(
        signal_id=signal_id,
        execution_id=execution_id,
        agent_name="Assessment",
        status="SUCCEEDED",
        correlation_id=CORRELATION_ID,
        proposal={"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/story"]},
    )
    supervisor.request_transition(
        signal_id,
        execution_id,
        requested_state="ASSESSED",
        transition_type="ADVANCE",
        trigger_type="AGENT_EXECUTION",
        trigger_record_id=assessment_id,
        correlation_id=CORRELATION_ID,
        accepted_at=ACCEPTED_AT,
    )
    critic_id = supervisor.record_agent(
        signal_id=signal_id,
        execution_id=execution_id,
        agent_name="Critic",
        status="SUCCEEDED",
        correlation_id=CORRELATION_ID,
        proposal={
            "cited_entity_ids": ["ENT-KMRT"],
            "evidence_references": ["https://www.reuters.com/story"],
            "final_score_available": True,
        },
    )
    supervisor.request_transition(
        signal_id,
        execution_id,
        requested_state="CRITIQUED",
        transition_type="ADVANCE",
        trigger_type="AGENT_EXECUTION",
        trigger_record_id=critic_id,
        correlation_id=CORRELATION_ID,
        accepted_at=ACCEPTED_AT,
    )
