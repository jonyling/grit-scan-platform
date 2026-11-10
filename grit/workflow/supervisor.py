"""Supervisor: call the guards, then persist only on ALLOW.

The ledger lives in memory. A sink, such as the signal pool, receives each row as it is appended. Earlier rows can be
reloaded from the sink. The append-only rule therefore stays in this one class. Dataverse remains the system of record. This service does
not open a connection or hold a credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from grit.core.canonical import canonical_hash
from grit.core.review_gate import FUNCTION_VERSION as REVIEW_GATE_VERSION
from grit.core.review_gate import review_gate
from grit.core.transition_guard import FUNCTION_VERSION as GUARD_VERSION
from grit.core.transition_guard import transition_guard
from grit.dataverse.signals import execution_body, ranked_finding_body, ranked_finding_content_hash, ranked_workflow_body, signal_body, workflow_body
from grit.workflow.theme_register import ThemeRegister


@dataclass(frozen=True)
class WorkflowRow:
    row_id: str
    signal_id: str
    execution_id: str
    sequence: int
    previous_state: str | None
    state: str
    transition_type: str
    reason_code: str
    accepted_at: str
    actor_type: str
    actor_id: str
    trigger_type: str
    trigger_record_id: str | None
    correlation_id: str
    input_hash: str | None
    parent_row_id: str | None
    restoration_source_row_id: str | None
    guard_decision: str | None
    guard_result_hash: str | None
    idempotency_key: str


@dataclass(frozen=True)
class Package:
    package_id: str
    version: int
    content_hash: str
    membership_hash: str
    member_signal_ids: tuple[str, ...]
    status: str
    release_blocked: bool = False
    execution_id: str | None = None  # the execution every member runs in; one per reader package


class Ledger:
    def __init__(self, token: object, *, rows: Iterable[WorkflowRow] = (), sink: Callable[[WorkflowRow], None] | None = None) -> None:
        self._token = token
        self._rows: list[WorkflowRow] = list(rows)
        self._sink = sink

    def rows(self) -> tuple[WorkflowRow, ...]:
        return tuple(self._rows)

    def append(self, row: WorkflowRow, token: object) -> None:
        if token is not self._token:
            raise PermissionError("Only the Supervisor may append GRIT_WorkflowState")
        if any(
            existing.signal_id == row.signal_id and existing.execution_id == row.execution_id and existing.sequence == row.sequence
            for existing in self._rows
        ):
            raise RuntimeError("Duplicate workflow sequence")
        if self._sink is not None:
            self._sink(row)
        self._rows.append(row)


class Supervisor:
    def __init__(
        self,
        *,
        actor_id: str,
        configuration_version: str = "v1",
        supervisor_version: str = "v1",
        state_graph_version: str = "v1",
        known_entity_ids: frozenset[str] = frozenset(),
        ledger_rows: Iterable[WorkflowRow] = (),
        ledger_sink: Callable[[WorkflowRow], None] | None = None,
    ) -> None:
        self.actor_id = actor_id
        self.configuration_version = configuration_version
        self.supervisor_version = supervisor_version
        self.state_graph_version = state_graph_version
        self.known_entity_ids = set(known_entity_ids)
        self._token = object()
        self.ledger = Ledger(self._token, rows=ledger_rows, sink=ledger_sink)
        self.function_log: list[dict[str, Any]] = []
        self.agent_log: list[dict[str, Any]] = []
        self.guard_log: list[dict[str, Any]] = []
        self.review_log: list[dict[str, Any]] = []
        self.approvals: list[dict[str, Any]] = []
        self.accepted_proposals: list[dict[str, Any]] = []
        self._packages: dict[str, Package] = {}
        self._projection: dict[tuple[str, str], str] = {}
        for row in sorted(self.ledger.rows(), key=lambda item: item.sequence):
            self._projection[(row.signal_id, row.execution_id)] = row.row_id
        self.theme_register = ThemeRegister(self._token)

    def package(self, package_id: str) -> Package:
        return self._packages[package_id]

    def current_state(self, signal_id: str, execution_id: str) -> str | None:
        row = self._latest(signal_id, execution_id)
        return None if row is None else row.state

    def accept_signal(self, signal_id: str, execution_id: str, correlation_id: str, *, accepted_at: str) -> WorkflowRow:
        if self._latest(signal_id, execution_id) is not None:
            raise RuntimeError("Signal execution already started")
        row = WorkflowRow(
            row_id=f"{signal_id}-{execution_id}-000",
            signal_id=signal_id,
            execution_id=execution_id,
            sequence=0,
            previous_state=None,
            state="NEW",
            transition_type="START",
            reason_code="EXECUTION_STARTED",
            accepted_at=accepted_at,
            actor_type="SUPERVISOR",
            actor_id=self.actor_id,
            trigger_type="SYSTEM_EVENT",
            trigger_record_id=None,
            correlation_id=correlation_id,
            input_hash=None,
            parent_row_id=None,
            restoration_source_row_id=None,
            guard_decision=None,
            guard_result_hash=None,
            idempotency_key=f"start:{signal_id}:{execution_id}",
        )
        self.ledger.append(row, self._token)
        self._projection[(signal_id, execution_id)] = row.row_id
        self.function_log.append(
            {
                "record_id": f"start-exemption:{signal_id}:{execution_id}",
                "function_name": "InitialNewExemption",
                "status": "RECORDED",
                "reason_code": "INITIAL_NEW_EXEMPT",
                "signal_id": signal_id,
                "execution_id": execution_id,
            }
        )
        return row

    def record_function(
        self,
        *,
        signal_id: str,
        execution_id: str,
        function_name: str,
        status: str,
        correlation_id: str,
        source_record_id: str | None = None,
    ) -> str:
        record_id = source_record_id or f"fn-{len(self.function_log) + 1:03d}"
        self.function_log.append(
            {
                "record_id": record_id,
                "signal_id": signal_id,
                "execution_id": execution_id,
                "function_name": function_name,
                "status": status,
                "correlation_id": correlation_id,
            }
        )
        return record_id

    def record_agent(
        self,
        *,
        signal_id: str,
        execution_id: str,
        agent_name: str,
        status: str,
        correlation_id: str,
        proposal: dict[str, Any],
        source_record_id: str | None = None,
    ) -> str:
        cited = [str(item) for item in proposal.get("cited_entity_ids") or []]
        references = list(proposal.get("evidence_references") or [])
        unknown = [item for item in cited if item not in self.known_entity_ids]
        validation_passed = status == "SUCCEEDED" and not unknown and bool(references)
        record_id = source_record_id or f"ag-{len(self.agent_log) + 1:03d}"
        self.agent_log.append(
            {
                "record_id": record_id,
                "signal_id": signal_id,
                "execution_id": execution_id,
                "agent_name": agent_name,
                "status": status,
                "correlation_id": correlation_id,
                "proposal": proposal,
                "unknown_entity_ids": unknown,
                "supervisor_validation_passed": validation_passed,
            }
        )
        return record_id

    def submit_agent_proposal(
        self,
        proposal: dict[str, Any],
        *,
        signal_id: str,
        execution_id: str,
        correlation_id: str,
        permitted_urls: set[str] | None = None,
    ) -> dict[str, Any]:
        from grit.agents.validate import validate_proposal

        reasons = validate_proposal(proposal, known_entity_ids=self.known_entity_ids, permitted_urls=permitted_urls)
        accepted = not reasons
        record_id = f"ag-{len(self.agent_log) + 1:03d}"
        record = {
            "record_id": record_id,
            "signal_id": signal_id,
            "execution_id": execution_id,
            "agent_name": proposal.get("agent_name"),
            "status": "SUCCEEDED" if accepted else "REJECTED",
            "accepted": accepted,
            "reason_codes": reasons,
            "correlation_id": correlation_id,
            "prompt_version": proposal.get("prompt_version"),
            "response_hash": proposal.get("response_hash"),
            "proposal": proposal,
            "unknown_entity_ids": [item for item in proposal.get("cited_entity_ids") or [] if item not in self.known_entity_ids],
            "supervisor_validation_passed": accepted,
        }
        self.agent_log.append(record)
        if accepted:
            self.accepted_proposals.append(record)
        return record

    def create_package(self, signal_ids: list[str], *, package_id: str, version: int, content: dict[str, Any], execution_id: str | None = None) -> Package:
        members = tuple(sorted(signal_ids))
        package = Package(
            package_id=package_id,
            version=version,
            content_hash=canonical_hash(content),
            membership_hash=canonical_hash({"members": list(members)}),
            member_signal_ids=members,
            status="PENDING_REVIEW",
            execution_id=execution_id,
        )
        self._packages[package_id] = package
        return package

    def request_transition(
        self,
        signal_id: str,
        execution_id: str,
        *,
        requested_state: str,
        transition_type: str,
        trigger_type: str,
        trigger_record_id: str | None,
        correlation_id: str,
        accepted_at: str,
        reason_code: str = "RC_OK",
        idempotency_key: str | None = None,
        expected_sequence: int | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest = self._latest(signal_id, execution_id)
        if latest is None:
            raise RuntimeError("Signal has no NEW row")
        key = idempotency_key or f"{signal_id}:{execution_id}:{latest.sequence + 1}:{requested_state}:{trigger_record_id}"
        existing = self._row_for_key(signal_id, execution_id, key)
        base = latest
        sequence = latest.sequence + 1 if expected_sequence is None else expected_sequence
        if existing is not None and existing.parent_row_id is not None:
            base = next(row for row in self.ledger.rows() if row.row_id == existing.parent_row_id)
            if expected_sequence is None:
                sequence = existing.sequence
        envelope = self._envelope(
            base,
            requested_state=requested_state,
            transition_type=transition_type,
            trigger_type=trigger_type,
            trigger_record_id=trigger_record_id,
            correlation_id=correlation_id,
            reason_code=reason_code,
            idempotency_key=key,
            expected_sequence=sequence,
            evidence=evidence or {},
        )
        if existing is not None:
            # The projection has already moved to the persisted row. Reconstruct
            # the original decision input so the guard can return ALREADY_APPLIED
            # or IDEMPOTENCY_CONFLICT instead of a false projection mismatch.
            envelope["current_state_projection_matches_ledger"] = True
        decision = transition_guard(envelope)
        self.guard_log.append({"signal_id": signal_id, "execution_id": execution_id, "decision": decision})
        if decision["decision"] == "ALREADY_APPLIED":
            return decision | {"row": self._row_for_key(signal_id, execution_id, key)}
        if decision["decision"] != "ALLOW":
            return decision | {"row": None}
        row = WorkflowRow(
            row_id=f"{signal_id}-{execution_id}-{latest.sequence + 1:03d}",
            signal_id=signal_id,
            execution_id=execution_id,
            sequence=latest.sequence + 1,
            previous_state=latest.state,
            state=requested_state,
            transition_type=transition_type,
            reason_code=reason_code,
            accepted_at=accepted_at,
            actor_type="SUPERVISOR",
            actor_id=self.actor_id,
            trigger_type=trigger_type,
            trigger_record_id=trigger_record_id,
            correlation_id=correlation_id,
            input_hash=decision["input_hash"],
            parent_row_id=latest.row_id,
            restoration_source_row_id=envelope.get("restoration_source_row_id"),
            guard_decision=decision["decision"],
            guard_result_hash=decision["canonical_result_hash"],
            idempotency_key=key,
        )
        self.ledger.append(row, self._token)
        self._projection[(signal_id, execution_id)] = row.row_id
        return decision | {"row": row}

    def review_context(
        self,
        package_id: str,
        *,
        reviewer_id: str,
        reviewer_intent: str,
        findings: list[dict[str, Any]],
        correlation_id: str,
        reviewer_is_human: bool = True,
        reviewer_is_eligible: bool = True,
        separation_of_duties_passed: bool = True,
        policy_is_active: bool = True,
        missing_signal_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        package = self._packages[package_id]
        return {
            "review_package_id": package.package_id,
            "review_package_version": package.version,
            "review_package_hash": package.content_hash,
            "reviewer_id": reviewer_id,
            "reviewer_intent": reviewer_intent,
            "review_findings": findings,
            "correlation_id": correlation_id,
            "idempotency_key": f"review:{package.package_id}:{package.version}:{reviewer_id}:{reviewer_intent}",
            "authoritative_package_hash": package.content_hash,
            "authoritative_membership_hash": package.membership_hash,
            "submitted_membership_hash": package.membership_hash,
            "package_current_status": package.status,
            "package_completeness_known": True,
            "missing_signal_ids": list(missing_signal_ids or []),
            "reviewer_is_human": reviewer_is_human,
            "reviewer_is_eligible": reviewer_is_eligible,
            "separation_of_duties_passed": separation_of_duties_passed,
            "policy_is_active": policy_is_active,
            "approval_policy_version": "v1",
            "configuration_version": self.configuration_version,
            "function_version": REVIEW_GATE_VERSION,
        }

    def submit_review(
        self,
        package_id: str,
        *,
        reviewer_id: str,
        reviewer_intent: str,
        findings: list[dict[str, Any]],
        correlation_id: str,
        accepted_at: str,
        reviewer_is_human: bool = True,
        reviewer_is_eligible: bool = True,
        separation_of_duties_passed: bool = True,
        policy_is_active: bool = True,
        missing_signal_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        context = self.review_context(
            package_id,
            reviewer_id=reviewer_id,
            reviewer_intent=reviewer_intent,
            findings=findings,
            correlation_id=correlation_id,
            reviewer_is_human=reviewer_is_human,
            reviewer_is_eligible=reviewer_is_eligible,
            separation_of_duties_passed=separation_of_duties_passed,
            policy_is_active=policy_is_active,
            missing_signal_ids=missing_signal_ids,
        )
        package = self._packages[package_id]
        decision = review_gate(context)
        self.review_log.append(decision)
        outcome = decision["review_outcome"]
        if outcome == "APPROVE":
            approval = self._approval(package, reviewer_id, decision)
            persisted = self._transition_members(
                package,
                requested_state="APPROVED",
                transition_type="ADVANCE",
                trigger_type="HUMAN_APPROVAL",
                trigger_record_id=approval["approval_id"],
                correlation_id=correlation_id,
                accepted_at=accepted_at,
                evidence=self._review_evidence(package, decision),
            )
            if not persisted:
                self._packages[package_id] = Package(**{**package.__dict__, "release_blocked": True})
            else:
                self._packages[package_id] = Package(**{**package.__dict__, "status": "APPROVED"})
                self.approvals.append(approval)
        elif outcome == "RETURN_FOR_REWORK":
            review_id = self.record_function(
                signal_id=package.member_signal_ids[0],
                execution_id=package.execution_id or self._execution_for(package.member_signal_ids[0]),
                function_name="ReviewGate",
                status="SUCCEEDED",
                correlation_id=correlation_id,
            )
            persisted = self._transition_members(
                package,
                requested_state="ASSESSED",
                transition_type="REWORK",
                trigger_type="FUNCTION_EXECUTION",
                trigger_record_id=review_id,
                correlation_id=correlation_id,
                accepted_at=accepted_at,
                evidence=self._review_evidence(package, decision) | {"trigger_record_status": "SUCCEEDED"},
            )
            if persisted:
                self._packages[package_id] = Package(**{**package.__dict__, "status": "RETURNED_FOR_REWORK"})
        elif outcome == "REJECT":
            self._packages[package_id] = Package(**{**package.__dict__, "status": "REJECTED"})
        elif outcome == "HOLD":
            self._packages[package_id] = Package(**{**package.__dict__, "status": "HELD"})
        return decision

    def fail(
        self,
        signal_id: str,
        execution_id: str,
        *,
        reason_code: str,
        correlation_id: str,
        accepted_at: str,
    ) -> dict[str, Any]:
        record_id = self.record_function(
            signal_id=signal_id,
            execution_id=execution_id,
            function_name="BlockingError",
            status="FAILED",
            correlation_id=correlation_id,
        )
        return self.request_transition(
            signal_id,
            execution_id,
            requested_state="FAILED",
            transition_type="FAIL",
            trigger_type="FUNCTION_EXECUTION",
            trigger_record_id=record_id,
            correlation_id=correlation_id,
            accepted_at=accepted_at,
            reason_code=reason_code,
            evidence={
                "blocking_error_durably_recorded": True,
                "trigger_record_signal_id": signal_id,
                "trigger_record_execution_id": execution_id,
            },
        )

    def restore(
        self,
        signal_id: str,
        execution_id: str,
        *,
        source_row_id: str,
        correlation_id: str,
        accepted_at: str,
    ) -> dict[str, Any]:
        source = next((row for row in self.ledger.rows() if row.row_id == source_row_id), None)
        authoritative = (
            source is not None
            and source.signal_id == signal_id
            and source.execution_id == execution_id
            and source.state != "FAILED"
        )
        return self.request_transition(
            signal_id,
            execution_id,
            requested_state=source.state if source is not None else "NEW",
            transition_type="RESTORE",
            trigger_type="SYSTEM_EVENT",
            trigger_record_id=None,
            correlation_id=correlation_id,
            accepted_at=accepted_at,
            reason_code="SUPERVISOR_RECONCILIATION_COMPLETE",
            evidence={
                "restoration_source_is_authoritative": authoritative,
                "restoration_source_state": None if source is None else source.state,
                "restoration_source_row_id": source_row_id,
            },
        )

    def confirm_publication(
        self,
        signal_id: str,
        execution_id: str,
        *,
        verified: bool,
        correlation_id: str,
        accepted_at: str,
        lock_held: bool = False,
    ) -> dict[str, Any]:
        approval = next((item for item in self.approvals if signal_id in item["member_signal_ids"]), None)
        record_id = self.record_function(
            signal_id=signal_id,
            execution_id=execution_id,
            function_name="PublicationConfirmation",
            status="SUCCEEDED" if verified else "FAILED",
            correlation_id=correlation_id,
        )
        evidence: dict[str, Any] = {
            "publication_concurrency_lock_held": lock_held,
            "publication_confirmation_verified": verified,
        }
        if approval is not None:
            evidence |= {
                "trigger_approval_package_id": approval["package_id"],
                "trigger_approval_package_version": approval["package_version"],
                "trigger_approval_package_hash": approval["package_hash"],
                "publication_confirmation_package_id": approval["package_id"],
                "publication_confirmation_package_version": approval["package_version"],
                "publication_confirmation_package_hash": approval["package_hash"],
            }
        return self.request_transition(
            signal_id,
            execution_id,
            requested_state="PUBLICATION_CONFIRMED",
            transition_type="ADVANCE",
            trigger_type="PUBLICATION_CONFIRMATION",
            trigger_record_id=record_id,
            correlation_id=correlation_id,
            accepted_at=accepted_at,
            evidence=evidence,
        )

    def restore_package(self, package: Package, approval: dict[str, Any] | None = None) -> None:
        """Reload a package, and its approval when one exists, from the pool in a later session. The ledger rows come in through the constructor."""
        self._packages[package.package_id] = package
        if approval is not None and not any(item["approval_id"] == approval["approval_id"] for item in self.approvals):
            self.approvals.append(approval)

    def handoff_to_airis(
        self, signal_id: str, execution_id: str, *, payload_valid: bool, correlation_id: str, accepted_at: str, package_id: str | None = None, version: int | None = None
    ) -> dict[str, Any]:
        """APPROVED to AIRIS_HANDOFF_READY. The handoff must name the exact approved package version and hash."""
        approval = next(
            (
                item for item in self.approvals
                if signal_id in item["member_signal_ids"] and (package_id is None or item["package_id"] == package_id) and (version is None or item["package_version"] == version)
            ),
            None,
        )
        evidence: dict[str, Any] = {"airis_handoff_payload_valid": payload_valid}
        if approval is not None:
            evidence |= {
                "trigger_approval_package_id": approval["package_id"],
                "trigger_approval_package_version": approval["package_version"],
                "trigger_approval_package_hash": approval["package_hash"],
                "airis_handoff_package_id": approval["package_id"],
                "airis_handoff_package_version": approval["package_version"],
                "airis_handoff_package_hash": approval["package_hash"],
            }
        return self.request_transition(
            signal_id,
            execution_id,
            requested_state="AIRIS_HANDOFF_READY",
            transition_type="ADVANCE",
            trigger_type="OUTPUT_PACKAGE",
            trigger_record_id=approval["package_id"] if approval else None,
            correlation_id=correlation_id,
            accepted_at=accepted_at,
            evidence=evidence,
        )

    def _transition_members(
        self,
        package: Package,
        *,
        requested_state: str,
        transition_type: str,
        trigger_type: str,
        trigger_record_id: str,
        correlation_id: str,
        accepted_at: str,
        evidence: dict[str, Any],
    ) -> bool:
        for signal_id in package.member_signal_ids:
            execution_id = package.execution_id or self._execution_for(signal_id)
            latest = self._latest(signal_id, execution_id)
            if latest is None:
                return False
            envelope = self._envelope(
                latest,
                requested_state=requested_state,
                transition_type=transition_type,
                trigger_type=trigger_type,
                trigger_record_id=trigger_record_id,
                correlation_id=correlation_id,
                reason_code="REVIEW_DECISION",
                idempotency_key=f"{package.package_id}:v{package.version}:{signal_id}:{requested_state}",
                expected_sequence=latest.sequence + 1,
                evidence=evidence | {"current_package_membership_contains_signal": signal_id in package.member_signal_ids},
            )
            decision = transition_guard(envelope)
            self.guard_log.append({"signal_id": signal_id, "execution_id": execution_id, "decision": decision})
            if decision["decision"] != "ALLOW":
                return False
        for signal_id in package.member_signal_ids:
            execution_id = package.execution_id or self._execution_for(signal_id)
            persisted = self.request_transition(
                signal_id,
                execution_id,
                requested_state=requested_state,
                transition_type=transition_type,
                trigger_type=trigger_type,
                trigger_record_id=trigger_record_id,
                correlation_id=correlation_id,
                accepted_at=accepted_at,
                reason_code="REVIEW_DECISION",
                idempotency_key=f"{package.package_id}:v{package.version}:{signal_id}:{requested_state}",
                evidence=evidence | {"current_package_membership_contains_signal": True},
            )
            if persisted["decision"] != "ALLOW":
                raise RuntimeError("Package member transition diverged after a prior ALLOW")
        return True

    def _approval(self, package: Package, reviewer_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": f"approval:{package.package_id}:{package.version}",
            "package_id": package.package_id,
            "package_version": package.version,
            "package_hash": package.content_hash,
            "membership_hash": package.membership_hash,
            "reviewer_id": reviewer_id,
            "review_result_hash": decision["canonical_result_hash"],
            "member_signal_ids": package.member_signal_ids,
        }

    def _review_evidence(self, package: Package, decision: dict[str, Any]) -> dict[str, Any]:
        return {
            "trigger_package_id": package.package_id,
            "trigger_package_version": package.version,
            "trigger_package_status": "PENDING_REVIEW",
            "trigger_package_hash": package.content_hash,
            "trigger_package_membership_hash": package.membership_hash,
            "trigger_approval_package_id": package.package_id,
            "trigger_approval_package_version": package.version,
            "trigger_approval_package_hash": package.content_hash,
            "trigger_approval_membership_hash": package.membership_hash,
            "current_package_membership_contains_signal": True,
            "all_package_members_rework_requested": decision["review_outcome"] == "RETURN_FOR_REWORK",
            "reviewgate_outcome": decision["review_outcome"],
            "reviewgate_package_status_instruction": decision["package_status_instruction"],
            "reviewgate_signal_transition_instruction": decision["signal_transition_instruction"],
            "reviewgate_new_package_required": decision["new_package_required"],
            "trigger_record_status": "SUCCEEDED",
            "trigger_record_exists": True,
        }

    def _envelope(
        self,
        latest: WorkflowRow,
        *,
        requested_state: str,
        transition_type: str,
        trigger_type: str,
        trigger_record_id: str | None,
        correlation_id: str,
        reason_code: str,
        idempotency_key: str,
        expected_sequence: int,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        record = self._trigger_record(trigger_type, trigger_record_id)
        existing = self._row_for_key(latest.signal_id, latest.execution_id, idempotency_key)
        envelope: dict[str, Any] = {
            "signal_id": latest.signal_id,
            "execution_id": latest.execution_id,
            "execution_version": 1,
            "correlation_id": correlation_id,
            "current_state": latest.state,
            "requested_state": requested_state,
            "transition_type": transition_type,
            "transition_reason_code": reason_code,
            "actor_type": "SUPERVISOR",
            "actor_id": self.actor_id,
            "trigger_type": trigger_type,
            "trigger_record_id": trigger_record_id,
            "trigger_record_exists": record is not None or evidence.get("trigger_record_exists") is True,
            "trigger_record_status": None if record is None else record.get("status"),
            "trigger_record_execution_id": latest.execution_id if record is not None else evidence.get("trigger_record_execution_id"),
            "trigger_record_signal_id": latest.signal_id if record is not None else evidence.get("trigger_record_signal_id"),
            "trigger_function_name": None if record is None else record.get("function_name"),
            "expected_transition_sequence": expected_sequence,
            "authoritative_latest_transition_sequence": latest.sequence,
            "authoritative_current_state_row_id": latest.row_id,
            "supplied_parent_transition_row_id": latest.row_id,
            "current_state_projection_matches_ledger": self._projection.get((latest.signal_id, latest.execution_id)) == latest.row_id,
            "configuration_version": self.configuration_version,
            "supervisor_version": self.supervisor_version,
            "state_graph_version": self.state_graph_version,
            "function_version": GUARD_VERSION,
            "idempotency_key": idempotency_key,
            "existing_transition_found": existing is not None,
            "existing_transition_input_hash": None if existing is None else existing.input_hash,
            "existing_transition_row_id": None if existing is None else existing.row_id,
        }
        filled = dict(evidence)
        package = self._packages.get(trigger_record_id or "")
        if package is not None and trigger_type == "OUTPUT_PACKAGE":
            filled.setdefault("trigger_record_exists", True)
            filled.setdefault("trigger_package_id", package.package_id)
            filled.setdefault("trigger_package_version", package.version)
            filled.setdefault("trigger_package_status", package.status)
            filled.setdefault("trigger_package_hash", package.content_hash)
            filled.setdefault("trigger_package_membership_hash", package.membership_hash)
            filled.setdefault("current_package_membership_contains_signal", latest.signal_id in package.member_signal_ids)
        if record is not None and record.get("supervisor_validation_passed") is not None:
            filled["supervisor_validation_passed"] = record["supervisor_validation_passed"]
        if record is not None and record.get("function_name") == "ScoreRisk":
            filled["score_risk_only"] = True
        if record is not None and record.get("agent_name") == "Critic":
            proposal = record.get("proposal", {})
            if "controlled_assessment_status" in proposal:
                filled["controlled_assessment_status"] = proposal["controlled_assessment_status"]
                filled["controlled_assessment_hash"] = proposal.get("controlled_assessment_hash")
            else:
                filled["final_score_available"] = bool(proposal.get("final_score_available", True))
            filled["score_risk_only"] = False
        envelope.update(filled)
        if filled.get("trigger_record_exists") is True:
            envelope["trigger_record_exists"] = True
        envelope["actor_type"] = "SUPERVISOR"
        envelope["actor_id"] = self.actor_id
        envelope["current_state"] = latest.state
        envelope["authoritative_latest_transition_sequence"] = latest.sequence
        envelope["authoritative_current_state_row_id"] = latest.row_id
        envelope["supplied_parent_transition_row_id"] = latest.row_id
        return envelope

    def _trigger_record(self, trigger_type: str, trigger_record_id: str | None) -> dict[str, Any] | None:
        if not trigger_record_id:
            return None
        if trigger_type == "FUNCTION_EXECUTION":
            logs = self.function_log
        elif trigger_type == "AGENT_EXECUTION":
            logs = self.agent_log
        else:
            logs = []
        return next((item for item in logs if item.get("record_id") == trigger_record_id), None)

    def _latest(self, signal_id: str, execution_id: str) -> WorkflowRow | None:
        rows = [row for row in self.ledger.rows() if row.signal_id == signal_id and row.execution_id == execution_id]
        if not rows:
            return None
        return max(rows, key=lambda row: row.sequence)

    def _row_for_key(self, signal_id: str, execution_id: str, key: str) -> WorkflowRow | None:
        return next(
            (
                row
                for row in self.ledger.rows()
                if row.signal_id == signal_id and row.execution_id == execution_id and row.idempotency_key == key
            ),
            None,
        )

    def persist_acquired_signals(
        self,
        candidates: list[Any],
        client: Any,
        *,
        correlation_id: str,
        accepted_at: str,
        reason_code: str = "GDELT_ARTLIST_CANDIDATE",
        reason: str = "GDELT artlist candidate. Article body was not fetched. Outlet credibility is not established.",
        configuration_version: str = "gdelt-wild-1",
    ) -> dict[str, Any]:
        """Write new acquisition rows. A repeated content hash is not created again."""
        execution_id = client.create(
            "grit_executions",
            execution_body(
                name=correlation_id,
                correlation_id=correlation_id,
                started_at=accepted_at,
                configuration_version=configuration_version,
            ),
        )
        known = dict(client.content_hashes())
        created: list[str] = []
        reused: list[str] = []
        skipped: list[str] = []
        for candidate in candidates:
            if not getattr(candidate, "title", None) or not getattr(candidate, "url", None):
                skipped.append(getattr(candidate, "content_hash", ""))
                continue
            body = signal_body(candidate, execution_id=execution_id, correlation_id=correlation_id)
            existing = known.get(candidate.content_hash)
            if existing:
                # Re-scanning the same article: refresh theme/risk/basis to the
                # current STE theme register, never touch approval fields.
                client.patch(
                    "grit_signals",
                    existing,
                    {
                        "grit_headline": body["grit_headline"],
                        "grit_trend": body["grit_trend"],
                        "grit_risklevel": body["grit_risklevel"],
                        "grit_businessrelevancebasis": body["grit_businessrelevancebasis"],
                    },
                )
                reused.append(existing)
                continue
            signal_id = client.create("grit_signals", body)
            state_id = client.create(
                "grit_workflowstates",
                workflow_body(
                    candidate,
                    signal_id=signal_id,
                    execution_id=execution_id,
                    correlation_id=correlation_id,
                    accepted_at=accepted_at,
                    actor_id=self.actor_id,
                    reason_code=reason_code,
                    reason=reason,
                    configuration_version=configuration_version,
                ),
            )
            client.patch(
                "grit_signals",
                signal_id,
                {"grit_CurrentWorkflowStateId@odata.bind": f"/grit_workflowstates({state_id})"},
            )
            self.accept_signal(signal_id, execution_id, correlation_id, accepted_at=accepted_at)
            known[candidate.content_hash] = signal_id
            created.append(signal_id)
        return {"execution_id": execution_id, "created": created, "reused": reused, "skipped": skipped}

    def persist_ranked_findings(
        self,
        items: list[dict[str, Any]],
        client: Any,
        *,
        correlation_id: str,
        accepted_at: str,
        country_market: str | None = None,
        line_of_business: str | None = None,
        configuration_version: str = "urs-weekly-1",
    ) -> dict[str, Any]:
        """Write ``weekly_pipeline.rank()`` output: Trend, Risk Level, and Basis of
        Relevance to Business Area, generalized for any reader pack.

        A repeated content hash refreshes those three fields (the finding may
        have moved since the last cycle) but never creates a second workflow
        row and never touches a human's approval fields, matching the
        acquired-signal path above.
        """
        execution_id = client.create(
            "grit_executions",
            execution_body(
                name=correlation_id,
                correlation_id=correlation_id,
                started_at=accepted_at,
                configuration_version=configuration_version,
            ),
        )
        known = dict(client.content_hashes())
        created: list[str] = []
        updated: list[str] = []
        skipped: list[str] = []
        for item in items:
            if not item.get("condition") and not item.get("conditionId"):
                skipped.append(str(item.get("developmentId") or ""))
                continue
            content_hash = ranked_finding_content_hash(item)
            body = ranked_finding_body(
                item,
                execution_id=execution_id,
                correlation_id=correlation_id,
                country_market=country_market,
                line_of_business=line_of_business,
            )
            existing = known.get(content_hash)
            if existing:
                client.patch(
                    "grit_signals",
                    existing,
                    {
                        "grit_trend": body["grit_trend"],
                        "grit_risklevel": body["grit_risklevel"],
                        "grit_businessrelevancebasis": body["grit_businessrelevancebasis"],
                        "grit_evidenceconfidence": body["grit_evidenceconfidence"],
                    },
                )
                updated.append(existing)
                continue
            signal_id = client.create("grit_signals", body)
            state_id = client.create(
                "grit_workflowstates",
                ranked_workflow_body(
                    content_hash,
                    signal_id=signal_id,
                    execution_id=execution_id,
                    correlation_id=correlation_id,
                    accepted_at=accepted_at,
                    actor_id=self.actor_id,
                    configuration_version=configuration_version,
                ),
            )
            client.patch(
                "grit_signals",
                signal_id,
                {"grit_CurrentWorkflowStateId@odata.bind": f"/grit_workflowstates({state_id})"},
            )
            self.accept_signal(signal_id, execution_id, correlation_id, accepted_at=accepted_at)
            known[content_hash] = signal_id
            created.append(signal_id)
        return {"execution_id": execution_id, "created": created, "updated": updated, "skipped": skipped}

    def apply_register_mutation(self, mutation: dict[str, Any]) -> dict[str, Any]:
        before = len(self.ledger.rows())
        decision = self.theme_register.apply(mutation, self._token)
        if len(self.ledger.rows()) != before:
            raise RuntimeError("A theme mutation wrote a workflow row")
        return decision

    def _execution_for(self, signal_id: str) -> str:
        rows = [row for row in self.ledger.rows() if row.signal_id == signal_id]
        if not rows:
            raise RuntimeError(f"No execution for {signal_id}")
        return max(rows, key=lambda row: row.sequence).execution_id
