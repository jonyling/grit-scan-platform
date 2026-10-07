"""Reader packages through the governed workflow, persisted in the pool.

  submit   each member signal runs NEW, FETCHED, DEDUPED, ASSESSED, CRITIQUED, then PENDING_REVIEW with the package, and every
           advance passes TransitionGuard(). The triggers are the logged acquisition, dedupe, assessment and critic calls.
  review   a named reviewer's decision goes through ReviewGate(). APPROVE moves every member to APPROVED and records the
           approval against the exact package version and hash. RETURN_FOR_REWORK moves every member back to ASSESSED.
  airis    signals the BA head approved, inside an approved package, move to AIRIS_HANDOFF_READY and are exported.

The Supervisor is rebuilt from the pool for each action, so the ledger survives between sessions, and the pool table
refuses UPDATE and DELETE on workflow rows.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from grit.pool import Pool, utc_now
from grit.profiles import Profile
from grit.workflow.supervisor import Package, Supervisor, WorkflowRow

ACTOR = "grit-supervisor"


def supervisor_for(pool: Pool) -> Supervisor:
    rows = [WorkflowRow(**row) for row in pool.workflow_rows()]
    supervisor = Supervisor(actor_id=ACTOR, ledger_rows=rows, ledger_sink=pool.append_workflow_row)
    for row in pool.rows("SELECT * FROM packages ORDER BY created_at"):
        membership = row["membership"]
        package = Package(
            package_id=row["package_id"], version=row["version"], content_hash=row["content_hash"], membership_hash=membership["membership_hash"],
            member_signal_ids=tuple(membership["members"]), status=row["status"], execution_id=execution_for(row["run_id"], row["profile_id"]),
        )
        approval = membership.get("approval") if row["status"] == "APPROVED" else None
        supervisor.restore_package(package, approval)
    return supervisor


def execution_for(run_id: str, profile_id: str) -> str:
    """One workflow execution per run and reader. Spec 25: a different audience needs its own package, so a signal can be
    pending in Hwaimay's package and approved in Gareth's at the same time."""
    return f"EXEC-{run_id}-{profile_id}"


def _advance_to_critiqued(supervisor: Supervisor, signal_id: str, execution_id: str, *, evidence_url: str, correlation_id: str, at: str) -> None:
    if supervisor.current_state(signal_id, execution_id) is not None:
        return
    supervisor.accept_signal(signal_id, execution_id, correlation_id, accepted_at=at)
    steps = (
        ("FETCHED", "FUNCTION_EXECUTION", "BraveNewsSearch"),
        ("DEDUPED", "FUNCTION_EXECUTION", "FingerprintAndMerge"),
        ("ASSESSED", "AGENT_EXECUTION", "Assessor"),
        ("CRITIQUED", "AGENT_EXECUTION", "Critic"),
    )
    for state, trigger, name in steps:
        if trigger == "FUNCTION_EXECUTION":
            record_id = supervisor.record_function(signal_id=signal_id, execution_id=execution_id, function_name=name, status="SUCCEEDED", correlation_id=correlation_id)
        else:
            proposal = {"cited_entity_ids": [], "evidence_references": [evidence_url]}
            if name == "Critic":
                proposal["final_score_available"] = True
            record_id = supervisor.record_agent(signal_id=signal_id, execution_id=execution_id, agent_name="Assessment" if name == "Assessor" else name, status="SUCCEEDED", correlation_id=correlation_id, proposal=proposal)
        result = supervisor.request_transition(signal_id, execution_id, requested_state=state, transition_type="ADVANCE", trigger_type=trigger, trigger_record_id=record_id, correlation_id=correlation_id, accepted_at=at)
        if result.get("decision") not in {"ALLOW", "ALREADY_APPLIED"}:
            raise RuntimeError(f"TransitionGuard denied {signal_id} to {state}: {result.get('reason_code')}")


def submit_package(pool: Pool, *, run_id: str, profile: Profile, members: Sequence[str], brief_hash: str, files: Mapping[str, str]) -> dict[str, Any]:
    """Create the next version of this reader's package for the run and put every member in PENDING_REVIEW."""
    package_id = f"PKG-{run_id}-{profile.profile_id}"
    previous = pool.one("SELECT MAX(version) AS version FROM packages WHERE package_id = ?", (package_id,)) or {}
    version = int(previous.get("version") or 0) + 1
    supervisor = supervisor_for(pool)
    execution_id = execution_for(run_id, profile.profile_id)
    at = utc_now()
    correlation = f"{package_id}-v{version}"
    urls = {row["signal_id"]: row["url"] for row in pool.rows(f"SELECT signal_id, url FROM signals WHERE signal_id IN ({','.join('?' * len(members))})", tuple(members))} if members else {}
    usable = [signal_id for signal_id in members if signal_id in urls and supervisor.current_state(signal_id, execution_id) in {None, "CRITIQUED", "ASSESSED"}]
    for signal_id in usable:
        _advance_to_critiqued(supervisor, signal_id, execution_id, evidence_url=urls[signal_id], correlation_id=correlation, at=at)
        if supervisor.current_state(signal_id, execution_id) == "ASSESSED":
            critic = supervisor.record_agent(signal_id=signal_id, execution_id=execution_id, agent_name="Critic", status="SUCCEEDED", correlation_id=correlation,
                                             proposal={"cited_entity_ids": [], "evidence_references": [urls[signal_id]], "final_score_available": True})
            supervisor.request_transition(signal_id, execution_id, requested_state="CRITIQUED", transition_type="ADVANCE", trigger_type="AGENT_EXECUTION", trigger_record_id=critic, correlation_id=correlation, accepted_at=at)
    if not usable:
        raise ValueError("No member signal can enter review. Each is already pending, approved, or handed off.")
    package = supervisor.create_package(usable, package_id=package_id, version=version, content={"brief_hash": brief_hash, "profile": profile.profile_id, "run": run_id}, execution_id=execution_id)
    for signal_id in usable:
        result = supervisor.request_transition(signal_id, execution_id, requested_state="PENDING_REVIEW", transition_type="ADVANCE", trigger_type="OUTPUT_PACKAGE", trigger_record_id=package.package_id, correlation_id=correlation, accepted_at=at)
        if result.get("decision") not in {"ALLOW", "ALREADY_APPLIED"}:
            raise RuntimeError(f"TransitionGuard denied {signal_id} to PENDING_REVIEW: {result.get('reason_code')}")
    pool.insert("packages", {
        "package_id": package_id, "version": version, "run_id": run_id, "profile_id": profile.profile_id, "status": "PENDING_REVIEW", "content_hash": package.content_hash,
        "membership_json": {"members": list(package.member_signal_ids), "membership_hash": package.membership_hash, "brief_hash": brief_hash}, "files_json": dict(files), "created_at": at,
    })
    pool.log(actor_type="FUNCTION", actor="PackAssembly", status="SUCCEEDED", run_id=run_id, subject=package_id, output_hash=package.content_hash, detail={"version": version, "members": len(usable)})
    return {"package_id": package_id, "version": version, "content_hash": package.content_hash, "members": list(package.member_signal_ids)}


def review_package(pool: Pool, *, package_id: str, version: int, reviewer: Profile, intent: str, findings: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """intent is APPROVE, REJECT, HOLD, or REQUEST_REWORK. A READER profile cannot review: refusing it here keeps a stray
    attempt from putting the package on hold, which is what ReviewGate() does with an ineligible reviewer."""
    if reviewer.review_role not in {"REVIEWER", "BA_HEAD"}:
        raise PermissionError(f"{reviewer.label} is a reader, not a reviewer.")
    row = pool.one("SELECT * FROM packages WHERE package_id = ? AND version = ?", (package_id, version))
    if row is None:
        raise KeyError(f"No package {package_id} v{version}")
    supervisor = supervisor_for(pool)
    at = utc_now()
    decision = supervisor.submit_review(
        package_id, reviewer_id=reviewer.profile_id, reviewer_intent=intent, findings=[dict(item) for item in findings], correlation_id=f"{package_id}-v{version}-review",
        accepted_at=at, reviewer_is_eligible=True, separation_of_duties_passed=True,
    )
    package = supervisor.package(package_id)
    membership = dict(row["membership"])
    approval = next((item for item in supervisor.approvals if item["package_id"] == package_id and item["package_version"] == version), None)
    if approval:
        membership["approval"] = {**approval, "member_signal_ids": list(approval["member_signal_ids"])}
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE packages SET status = ?, decided_by = ?, decided_at = ?, membership_json = ? WHERE package_id = ? AND version = ?",
            (package.status, reviewer.profile_id, at, json.dumps(membership, sort_keys=True, default=str), package_id, version),
        )
    pool.log(actor_type="FUNCTION", actor="ReviewGate", status=decision["review_outcome"], subject=package_id, run_id=row["run_id"], output_hash=decision["canonical_result_hash"],
             detail={"reviewer": reviewer.profile_id, "intent": intent, "reason_codes": decision.get("reason_codes")})
    return {"outcome": decision["review_outcome"], "status": package.status, "decision": decision}


def airis_ready(pool: Pool, ba_head: Profile) -> list[dict[str, Any]]:
    """Signals the BA head approved that sit in an approved package and are not yet handed off."""
    if ba_head.review_role != "BA_HEAD":
        raise PermissionError(f"{ba_head.label} is not a BA head. Only a BA head's approvals go to AIRIS.")
    approved = {signal_id for (profile_id, signal_id), decision in pool.latest_decisions(ba_head.profile_id).items() if decision == "APPROVE"}
    states = pool.current_states()
    ready, seen = [], set()
    for row in pool.rows("SELECT * FROM packages WHERE status = 'APPROVED' ORDER BY created_at"):
        execution_id = execution_for(row["run_id"], row["profile_id"])
        for signal_id in row["membership"]["members"]:
            if signal_id in approved and signal_id not in seen and states.get((signal_id, execution_id)) == "APPROVED":
                seen.add(signal_id)
                ready.append({"signal_id": signal_id, "package_id": row["package_id"], "version": row["version"], "run_id": row["run_id"], "profile_id": row["profile_id"]})
    return ready


def hand_off(pool: Pool, ba_head: Profile, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    supervisor = supervisor_for(pool)
    at = utc_now()
    done = []
    for item in items:
        result = supervisor.handoff_to_airis(item["signal_id"], execution_for(item["run_id"], item["profile_id"]), payload_valid=True, correlation_id=f"airis-{item['package_id']}", accepted_at=at)
        done.append({**item, "decision": result.get("decision"), "reason_code": result.get("reason_code")})
    return done

