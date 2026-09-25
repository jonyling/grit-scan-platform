from dataclasses import FrozenInstanceError

from supervisor.service import Supervisor


def _supervisor() -> Supervisor:
    return Supervisor(actor_id="supervisor-01", known_entity_ids=frozenset({"ENT-KMRT"}))


def _to_pending(supervisor: Supervisor, signal_id: str, execution_id: str, package_id: str | None = None) -> str:
    accepted_at = "2026-09-25T00:00:00Z"
    correlation_id = "corr-1"
    supervisor.accept_signal(signal_id, execution_id, correlation_id, accepted_at=accepted_at)
    fetch_id = supervisor.record_function(signal_id=signal_id, execution_id=execution_id, function_name="FetchArticle", status="SUCCEEDED", correlation_id=correlation_id)
    supervisor.request_transition(signal_id, execution_id, requested_state="FETCHED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fetch_id, correlation_id=correlation_id, accepted_at=accepted_at)
    fingerprint_id = supervisor.record_function(signal_id=signal_id, execution_id=execution_id, function_name="FingerprintArticle", status="SUCCEEDED", correlation_id=correlation_id)
    supervisor.request_transition(signal_id, execution_id, requested_state="DEDUPED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fingerprint_id, correlation_id=correlation_id, accepted_at=accepted_at)
    assessment_id = supervisor.record_agent(
        signal_id=signal_id,
        execution_id=execution_id,
        agent_name="Assessment",
        status="SUCCEEDED",
        correlation_id=correlation_id,
        proposal={"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/story"]},
    )
    supervisor.request_transition(signal_id, execution_id, requested_state="ASSESSED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=assessment_id, correlation_id=correlation_id, accepted_at=accepted_at)
    critic_id = supervisor.record_agent(
        signal_id=signal_id,
        execution_id=execution_id,
        agent_name="Critic",
        status="SUCCEEDED",
        correlation_id=correlation_id,
        proposal={"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/story"], "final_score_available": True},
    )
    supervisor.request_transition(signal_id, execution_id, requested_state="CRITIQUED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=critic_id, correlation_id=correlation_id, accepted_at=accepted_at)
    if package_id is None:
        package = supervisor.create_package([signal_id], package_id=f"pkg-{signal_id}", version=1, content={"signal": signal_id})
        package_id = package.package_id
    supervisor.request_transition(signal_id, execution_id, requested_state="PENDING_REVIEW", transition_type="ADVANCE", trigger_type="OUTPUT_PACKAGE", trigger_record_id=package_id, correlation_id=correlation_id, accepted_at=accepted_at)
    return package_id


def test_one_signal_reaches_approved_with_a_guard_on_every_advance() -> None:
    supervisor = _supervisor()
    package_id = _to_pending(supervisor, "SIG-1", "EXEC-1")
    decision = supervisor.submit_review(
        package_id,
        reviewer_id="reviewer-01",
        reviewer_intent="APPROVE",
        findings=[],
        correlation_id="corr-1",
        accepted_at="2026-09-25T01:00:00Z",
    )
    states = [row.state for row in supervisor.ledger.rows() if row.signal_id == "SIG-1"]
    assert states == ["NEW", "FETCHED", "DEDUPED", "ASSESSED", "CRITIQUED", "PENDING_REVIEW", "APPROVED"]
    assert decision["review_outcome"] == "APPROVE"
    advances = [row for row in supervisor.ledger.rows() if row.transition_type != "START"]
    assert advances
    assert all(row.guard_decision == "ALLOW" and row.guard_result_hash for row in advances)
    assert supervisor.ledger.rows()[0].guard_decision is None
    approval = supervisor.approvals[0]
    assert approval["package_id"] == package_id
    assert approval["package_hash"] == supervisor.package(package_id).content_hash


def test_denied_transition_does_not_append_a_row() -> None:
    supervisor = _supervisor()
    supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-25T00:00:00Z")
    before = len(supervisor.ledger.rows())
    denied = supervisor.request_transition(
        "SIG-1",
        "EXEC-1",
        requested_state="APPROVED",
        transition_type="ADVANCE",
        trigger_type="HUMAN_APPROVAL",
        trigger_record_id="approval-missing",
        correlation_id="corr-1",
        accepted_at="2026-09-25T00:01:00Z",
    )
    assert denied["decision"] == "DENY"
    assert len(supervisor.ledger.rows()) == before
    assert supervisor.current_state("SIG-1", "EXEC-1") == "NEW"


def test_stale_sequence_and_idempotency_do_not_overwrite_the_ledger() -> None:
    supervisor = _supervisor()
    supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-25T00:00:00Z")
    fetch_id = supervisor.record_function(signal_id="SIG-1", execution_id="EXEC-1", function_name="FetchArticle", status="SUCCEEDED", correlation_id="corr-1")
    stale = supervisor.request_transition(
        "SIG-1",
        "EXEC-1",
        requested_state="FETCHED",
        transition_type="ADVANCE",
        trigger_type="FUNCTION_EXECUTION",
        trigger_record_id=fetch_id,
        correlation_id="corr-1",
        accepted_at="2026-09-25T00:01:00Z",
        expected_sequence=9,
    )
    assert stale["reason_code"] == "STALE_SEQUENCE"
    assert supervisor.current_state("SIG-1", "EXEC-1") == "NEW"
    first = supervisor.request_transition(
        "SIG-1",
        "EXEC-1",
        requested_state="FETCHED",
        transition_type="ADVANCE",
        trigger_type="FUNCTION_EXECUTION",
        trigger_record_id=fetch_id,
        correlation_id="corr-1",
        accepted_at="2026-09-25T00:01:00Z",
        idempotency_key="fetch-1",
    )
    second = supervisor.request_transition(
        "SIG-1",
        "EXEC-1",
        requested_state="FETCHED",
        transition_type="ADVANCE",
        trigger_type="FUNCTION_EXECUTION",
        trigger_record_id=fetch_id,
        correlation_id="corr-1",
        accepted_at="2026-09-25T00:01:00Z",
        idempotency_key="fetch-1",
    )
    assert first["decision"] == "ALLOW"
    assert second["decision"] == "ALREADY_APPLIED"
    assert [row.sequence for row in supervisor.ledger.rows()] == [0, 1]
    other = supervisor.record_function(signal_id="SIG-1", execution_id="EXEC-1", function_name="FetchArticle", status="SUCCEEDED", correlation_id="corr-1")
    conflict = supervisor.request_transition(
        "SIG-1",
        "EXEC-1",
        requested_state="FETCHED",
        transition_type="ADVANCE",
        trigger_type="FUNCTION_EXECUTION",
        trigger_record_id=other,
        correlation_id="corr-1",
        accepted_at="2026-09-25T00:02:00Z",
        idempotency_key="fetch-1",
        expected_sequence=1,
    )
    assert conflict["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert [row.sequence for row in supervisor.ledger.rows()] == [0, 1]


def test_failed_row_is_preserved_when_restored() -> None:
    supervisor = _supervisor()
    supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-25T00:00:00Z")
    fetch_id = supervisor.record_function(signal_id="SIG-1", execution_id="EXEC-1", function_name="FetchArticle", status="SUCCEEDED", correlation_id="corr-1")
    fetched = supervisor.request_transition("SIG-1", "EXEC-1", requested_state="FETCHED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fetch_id, correlation_id="corr-1", accepted_at="2026-09-25T00:01:00Z")
    failed = supervisor.fail("SIG-1", "EXEC-1", reason_code="CREDIBILITY_LOOKUP_TIMEOUT", correlation_id="corr-1", accepted_at="2026-09-25T00:02:00Z")
    restored = supervisor.restore("SIG-1", "EXEC-1", source_row_id=fetched["row"].row_id, correlation_id="corr-1", accepted_at="2026-09-25T00:03:00Z")
    states = [row.state for row in supervisor.ledger.rows()]
    assert states == ["NEW", "FETCHED", "FAILED", "FETCHED"]
    assert failed["decision"] == "ALLOW"
    assert restored["decision"] == "ALLOW"
    assert restored["row"].restoration_source_row_id == fetched["row"].row_id
    assert supervisor.ledger.rows()[2].state == "FAILED"


def test_rework_returns_every_package_member_and_reject_does_not() -> None:
    clean = _supervisor()
    _advance_to_critiqued(clean, "SIG-A", "EXEC-A")
    _advance_to_critiqued(clean, "SIG-B", "EXEC-B")
    package = clean.create_package(["SIG-A", "SIG-B"], package_id="RP-2", version=1, content={"members": ["SIG-A", "SIG-B"]})
    for signal_id, execution_id in (("SIG-A", "EXEC-A"), ("SIG-B", "EXEC-B")):
        clean.request_transition(signal_id, execution_id, requested_state="PENDING_REVIEW", transition_type="ADVANCE", trigger_type="OUTPUT_PACKAGE", trigger_record_id=package.package_id, correlation_id="corr-1", accepted_at="2026-09-25T00:05:00Z")
    rejected = clean.submit_review(package.package_id, reviewer_id="reviewer-01", reviewer_intent="REJECT", findings=[], correlation_id="corr-1", accepted_at="2026-09-25T00:06:00Z")
    assert rejected["signal_transition_instruction"] == "NONE"
    assert clean.current_state("SIG-A", "EXEC-A") == "PENDING_REVIEW"
    assert clean.current_state("SIG-B", "EXEC-B") == "PENDING_REVIEW"
    assert clean.package(package.package_id).status == "REJECTED"
    assert clean.approvals == []

    rework = _supervisor()
    _advance_to_critiqued(rework, "SIG-A", "EXEC-A")
    _advance_to_critiqued(rework, "SIG-B", "EXEC-B")
    package = rework.create_package(["SIG-A", "SIG-B"], package_id="RP-3", version=1, content={"members": ["SIG-A", "SIG-B"]})
    for signal_id, execution_id in (("SIG-A", "EXEC-A"), ("SIG-B", "EXEC-B")):
        rework.request_transition(signal_id, execution_id, requested_state="PENDING_REVIEW", transition_type="ADVANCE", trigger_type="OUTPUT_PACKAGE", trigger_record_id=package.package_id, correlation_id="corr-1", accepted_at="2026-09-25T00:05:00Z")
    decision = rework.submit_review(
        package.package_id,
        reviewer_id="reviewer-01",
        reviewer_intent="REQUEST_REWORK",
        findings=[{"finding_id": "F-1", "finding_type": "MISSING_EVIDENCE", "severity": "MATERIAL", "description": "Missing source", "resolution_required": True, "affected_signal_id": "SIG-A"}],
        correlation_id="corr-1",
        accepted_at="2026-09-25T00:06:00Z",
    )
    assert decision["review_outcome"] == "RETURN_FOR_REWORK"
    assert rework.current_state("SIG-A", "EXEC-A") == "ASSESSED"
    assert rework.current_state("SIG-B", "EXEC-B") == "ASSESSED"
    assert rework.package(package.package_id).status == "RETURNED_FOR_REWORK"
    assert rework.approvals == []


def test_unknown_entity_and_score_risk_alone_do_not_persist() -> None:
    supervisor = _supervisor()
    supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-25T00:00:00Z")
    fetch_id = supervisor.record_function(signal_id="SIG-1", execution_id="EXEC-1", function_name="FetchArticle", status="SUCCEEDED", correlation_id="corr-1")
    supervisor.request_transition("SIG-1", "EXEC-1", requested_state="FETCHED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fetch_id, correlation_id="corr-1", accepted_at="2026-09-25T00:01:00Z")
    fingerprint_id = supervisor.record_function(signal_id="SIG-1", execution_id="EXEC-1", function_name="FingerprintArticle", status="SUCCEEDED", correlation_id="corr-1")
    supervisor.request_transition("SIG-1", "EXEC-1", requested_state="DEDUPED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fingerprint_id, correlation_id="corr-1", accepted_at="2026-09-25T00:02:00Z")
    bad_agent = supervisor.record_agent(
        signal_id="SIG-1",
        execution_id="EXEC-1",
        agent_name="Assessment",
        status="SUCCEEDED",
        correlation_id="corr-1",
        proposal={"cited_entity_ids": ["ENT-DOES-NOT-EXIST"], "evidence_references": ["https://www.reuters.com/story"]},
    )
    denied = supervisor.request_transition("SIG-1", "EXEC-1", requested_state="ASSESSED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=bad_agent, correlation_id="corr-1", accepted_at="2026-09-25T00:03:00Z")
    assert denied["decision"] == "DENY"
    assert supervisor.current_state("SIG-1", "EXEC-1") == "DEDUPED"
    assert supervisor.agent_log[-1]["unknown_entity_ids"] == ["ENT-DOES-NOT-EXIST"]
    assessment_id = supervisor.record_agent(
        signal_id="SIG-1",
        execution_id="EXEC-1",
        agent_name="Assessment",
        status="SUCCEEDED",
        correlation_id="corr-1",
        proposal={"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/story"]},
    )
    supervisor.request_transition("SIG-1", "EXEC-1", requested_state="ASSESSED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=assessment_id, correlation_id="corr-1", accepted_at="2026-09-25T00:04:00Z")
    score_id = supervisor.record_function(signal_id="SIG-1", execution_id="EXEC-1", function_name="ScoreRisk", status="SUCCEEDED", correlation_id="corr-1")
    score_only = supervisor.request_transition("SIG-1", "EXEC-1", requested_state="CRITIQUED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=score_id, correlation_id="corr-1", accepted_at="2026-09-25T00:05:00Z")
    assert score_only["reason_code"] == "EVIDENCE_NOT_COMPLETE"
    assert supervisor.current_state("SIG-1", "EXEC-1") == "ASSESSED"


def test_only_the_supervisor_appends_and_history_is_immutable() -> None:
    supervisor = _supervisor()
    row = supervisor.accept_signal("SIG-1", "EXEC-1", "corr-1", accepted_at="2026-09-25T00:00:00Z")
    denied = False
    try:
        supervisor.ledger.append(row, token=None)
    except PermissionError:
        denied = True
    assert denied
    frozen = False
    try:
        row.state = "APPROVED"
    except FrozenInstanceError:
        frozen = True
    assert frozen


def _advance_to_critiqued(supervisor: Supervisor, signal_id: str, execution_id: str) -> None:
    accepted_at = "2026-09-25T00:00:00Z"
    correlation_id = "corr-1"
    supervisor.accept_signal(signal_id, execution_id, correlation_id, accepted_at=accepted_at)
    fetch_id = supervisor.record_function(signal_id=signal_id, execution_id=execution_id, function_name="FetchArticle", status="SUCCEEDED", correlation_id=correlation_id)
    supervisor.request_transition(signal_id, execution_id, requested_state="FETCHED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fetch_id, correlation_id=correlation_id, accepted_at=accepted_at)
    fingerprint_id = supervisor.record_function(signal_id=signal_id, execution_id=execution_id, function_name="FingerprintArticle", status="SUCCEEDED", correlation_id=correlation_id)
    supervisor.request_transition(signal_id, execution_id, requested_state="DEDUPED", transition_type="ADVANCE", trigger_type="FUNCTION_EXECUTION", trigger_record_id=fingerprint_id, correlation_id=correlation_id, accepted_at=accepted_at)
    assessment_id = supervisor.record_agent(signal_id=signal_id, execution_id=execution_id, agent_name="Assessment", status="SUCCEEDED", correlation_id=correlation_id, proposal={"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/story"]})
    assessed = supervisor.request_transition(signal_id, execution_id, requested_state="ASSESSED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=assessment_id, correlation_id=correlation_id, accepted_at=accepted_at)
    assert assessed["decision"] == "ALLOW", assessed
    critic_id = supervisor.record_agent(signal_id=signal_id, execution_id=execution_id, agent_name="Critic", status="SUCCEEDED", correlation_id=correlation_id, proposal={"cited_entity_ids": ["ENT-KMRT"], "evidence_references": ["https://www.reuters.com/story"], "final_score_available": True})
    critiqued = supervisor.request_transition(signal_id, execution_id, requested_state="CRITIQUED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=critic_id, correlation_id=correlation_id, accepted_at=accepted_at)
    assert critiqued["decision"] == "ALLOW", critiqued
