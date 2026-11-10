"""Reader packages through the governed workflow, persisted in the pool.

  submit   each member signal runs NEW, FETCHED, DEDUPED, ASSESSED, CRITIQUED, then PENDING_REVIEW with the package, and every
           advance passes TransitionGuard(). The triggers are the logged acquisition, dedupe, assessment and critic calls.
  review   a named reviewer's decision goes through ReviewGate(). APPROVE moves every member to APPROVED and records the
           approval against the exact package version and hash. RETURN_FOR_REWORK moves every member back to ASSESSED.
  airis    signals the BA head approved, inside an approved package, move to AIRIS_HANDOFF_READY and are exported.

The Supervisor is rebuilt from the pool for each action, so the ledger survives between sessions. The pool table
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
    for row in pool.rows("SELECT * FROM packages ORDER BY package_id, version"):
        supervisor.restore_package(_package(row), row["membership"].get("approval") if row["status"] == "APPROVED" else None)
    return supervisor


def _package(row: Mapping[str, Any]) -> Package:
    membership = row["membership"]
    return Package(
            package_id=row["package_id"], version=row["version"], content_hash=row["content_hash"], membership_hash=membership["membership_hash"],
        member_signal_ids=tuple(membership["members"]), status=row["status"], execution_id=membership.get("execution_id") or execution_for(row["run_id"], row["profile_id"]),
    )


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
    snapshot = None
    if ((pool.run(run_id) or {}).get("config") or {}).get("weekly_mode") == "reasoned":
        from grit.pipeline.weekly import frozen_report
        snapshot = frozen_report(pool, run_id, profile.profile_id)
        if snapshot["content_hash"] != brief_hash or set(snapshot["brief"]["membership"]) != set(members):
            raise ValueError("Submission does not match the independently critiqued report.")
        stage = pool.one("SELECT * FROM weekly_runs WHERE run_id = ?", (run_id,))
        if stage["status"] != "READY" or pool.run(run_id)["status"] != "COMPLETE":
            raise ValueError("The weekly analysis must complete before human review.")
        rejected = pool.one("SELECT * FROM packages WHERE package_id = ? AND status IN ('RETURNED_FOR_REWORK', 'REJECTED') ORDER BY version DESC LIMIT 1", (package_id,))
        if rejected and rejected["membership"].get("weekly_report_id") == snapshot["report_id"]:
            raise ValueError("A returned or rejected report needs a new report snapshot and critique.")
    supervisor = supervisor_for(pool)
    execution_id = execution_for(run_id, profile.profile_id) + (f"-v{version}" if snapshot else "")
    at = utc_now()
    correlation = f"{package_id}-v{version}"
    urls = {row["signal_id"]: row["url"] for row in pool.rows(f"SELECT signal_id, url FROM signals WHERE signal_id IN ({','.join('?' * len(members))})", tuple(members))} if members else {}
    usable = [signal_id for signal_id in members if signal_id in urls and supervisor.current_state(signal_id, execution_id) in {None, "CRITIQUED", "ASSESSED"}]
    for signal_id in usable:
        if snapshot:
            _advance_reasoned(pool, supervisor, snapshot, signal_id, execution_id, correlation, at)
        else:
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
        "membership_json": {"members": list(package.member_signal_ids), "membership_hash": package.membership_hash, "brief_hash": brief_hash,
                            "execution_id": execution_id, **({"weekly_report_id": snapshot["report_id"]} if snapshot else {"assurance": "LEGACY_RECONSTRUCTED"})},
        "files_json": dict(files), "created_at": at,
    })
    pool.log(actor_type="FUNCTION", actor="PackAssembly", status="SUCCEEDED", run_id=run_id, subject=package_id, output_hash=package.content_hash, detail={"version": version, "members": len(usable)})
    return {"package_id": package_id, "version": version, "content_hash": package.content_hash, "members": list(package.member_signal_ids)}


def _advance_reasoned(pool: Pool, supervisor: Supervisor, snapshot: Mapping[str, Any], signal_id: str,
                      execution_id: str, correlation: str, at: str) -> None:
    """Reference actual persisted reasoning and perform deterministic snapshot checks."""
    from grit.core.canonical import canonical_hash

    run_id = snapshot["run_id"]
    row = next((row for row in pool.assessments(run_id) if row["signal_id"] == signal_id), None)
    calls = pool.rows("SELECT * FROM weekly_calls WHERE run_id = ? AND version = ? AND status = 'VALIDATED' ORDER BY call_id DESC", (run_id, snapshot["version"]))
    assessment = next((call for call in calls if call["role"] == "Assessment" and any(item["signal_id"] == signal_id for item in call["output"].get("selections", []))), None)
    pool_critic = next((call for call in calls if call["role"] == "Critic" and signal_id in call["output"].get("reviewed_ids", [])), None)
    final_critic = next((call for call in calls if call["call_id"] == snapshot["critique_call_id"]), None)
    if row is None or assessment is None or pool_critic is None or final_critic is None:
        raise ValueError(f"{signal_id}: genuine assessment and critique references are missing.")
    if (row.get("link") or {}).get("duplicate_of"):
        raise ValueError(f"{signal_id}: a duplicate cannot enter review as a separate finding.")
    outcome = (row.get("harm") or {}).get("status")
    if outcome not in {"SCORED", "NOT_RUN", "NOT_ASSESSED", "INSUFFICIENT_DATA"}:
        raise ValueError(f"{signal_id}: missing controlled assessment outcome.")
    supervisor.accept_signal(signal_id, execution_id, correlation, accepted_at=at)
    for state, function in (("FETCHED", "PoolEvidenceRead"), ("DEDUPED", "RecordedDuplicateCheck")):
        log_id = pool.log(actor_type="FUNCTION", actor=function, status="SUCCEEDED", run_id=run_id,
                          subject=signal_id, output_hash=canonical_hash(row), detail={"weekly_report_id": snapshot["report_id"]})
        record = supervisor.record_function(signal_id=signal_id, execution_id=execution_id, function_name=function,
                                            status="SUCCEEDED", correlation_id=correlation, source_record_id=f"execution-log:{log_id}")
        result = supervisor.request_transition(signal_id, execution_id, requested_state=state, transition_type="ADVANCE",
                                              trigger_type="FUNCTION_EXECUTION", trigger_record_id=record, correlation_id=correlation, accepted_at=at)
        if result["decision"] != "ALLOW":
            raise RuntimeError(f"TransitionGuard denied {state}: {result.get('reason_code')}")
    for state, agent, call in (("ASSESSED", "Assessment", assessment), ("CRITIQUED", "Critic", final_critic)):
        proposal = {"cited_entity_ids": [], "evidence_references": [row["url"]],
                    "invocation_id": call["call_id"], "input_hash": call["input_hash"], "output_hash": call["output_hash"],
                    "pool_critique_id": pool_critic["call_id"],
                    "controlled_assessment_status": outcome, "controlled_assessment_hash": canonical_hash(row.get("harm"))}
        record = supervisor.record_agent(signal_id=signal_id, execution_id=execution_id, agent_name=agent,
                                         status="SUCCEEDED", correlation_id=correlation, proposal=proposal, source_record_id=f"weekly-call:{call['call_id']}:{signal_id}")
        result = supervisor.request_transition(signal_id, execution_id, requested_state=state, transition_type="ADVANCE",
                                              trigger_type="AGENT_EXECUTION", trigger_record_id=record, correlation_id=correlation, accepted_at=at)
        if result["decision"] != "ALLOW":
            raise RuntimeError(f"TransitionGuard denied {state}: {result.get('reason_code')}")


def review_package(pool: Pool, *, package_id: str, version: int, reviewer: Profile, intent: str, findings: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """intent is APPROVE, REJECT, HOLD, or REQUEST_REWORK. A READER profile cannot review. This function refuses it
    before the gate, because `ReviewGate()` puts the package on hold when an ineligible reviewer tries."""
    if reviewer.review_role not in {"REVIEWER", "BA_HEAD"}:
        raise PermissionError(f"{reviewer.label} is a reader, not a reviewer.")
    row = pool.one("SELECT * FROM packages WHERE package_id = ? AND version = ?", (package_id, version))
    if row is None:
        raise KeyError(f"No package {package_id} v{version}")
    if row["status"] != "PENDING_REVIEW":
        raise ValueError(f"{package_id} v{version} is {row['status']}. Only a pending version can be reviewed.")
    supervisor = supervisor_for(pool)
    supervisor.restore_package(_package(row))  # the exact version under review, not whichever version was loaded last
    at = utc_now()
    decision = supervisor.submit_review(
        package_id, reviewer_id=reviewer.profile_id, reviewer_intent=intent, findings=[dict(item) for item in findings], correlation_id=f"{package_id}-v{version}-review",
        accepted_at=at, reviewer_is_eligible=True, separation_of_duties_passed=True,
    )
    package = supervisor.package(package_id)
    expected = {"APPROVE": "APPROVED", "RETURN_FOR_REWORK": "RETURNED_FOR_REWORK", "REJECT": "REJECTED", "HOLD": "HELD"}.get(decision["review_outcome"])
    if expected and package.status != expected:
        denied = [item["decision"] for item in supervisor.guard_log if item["decision"].get("decision") != "ALLOW"]
        reason = denied[-1].get("reason_code") if denied else "UNKNOWN"
        raise RuntimeError(f"ReviewGate returned {decision['review_outcome']}, but TransitionGuard refused to move the members ({reason}). Nothing was written.")
    membership = dict(row["membership"])
    membership["review"] = {"intent": intent, "findings": [dict(item) for item in findings],
                            "reviewer": reviewer.profile_id, "at": at}
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


REGISTER_RUNGS = {"DIRECT", "PERIMETER", "COUNTERPARTY"}


def airis_ready(pool: Pool, ba_head: Profile) -> list[dict[str, Any]]:
    """Members of a package the BA head approved that are tier 1 or 2 and linked to a register (a named entity, site,
    customer or supplier), not yet handed off. The approval of the package is the decision. No signal-by-signal approval
    is needed (spec 39)."""
    if ba_head.review_role != "BA_HEAD":
        raise PermissionError(f"{ba_head.label} is not a BA head. Only packages a BA head approved go to AIRIS.")
    states = pool.current_states()
    # A signal goes to AIRIS once, whichever package carried it.
    seen = {signal_id for (signal_id, _execution), state in states.items() if state == "AIRIS_HANDOFF_READY"}
    ready = []
    rows = pool.rows("SELECT * FROM packages WHERE status = 'APPROVED' AND decided_by = ? ORDER BY created_at", (ba_head.profile_id,))
    for row in rows:
        execution_id = row["membership"].get("execution_id") or execution_for(row["run_id"], row["profile_id"])
        snapshot = pool.one("SELECT * FROM weekly_reports WHERE report_id = ?", (row["membership"].get("weekly_report_id"),))
        assessed = {item["signal_id"]: item for item in (snapshot["routed"] if snapshot else pool.assessments(row["run_id"]))}
        for signal_id in row["membership"]["members"]:
            item = assessed.get(signal_id) or {}
            eligible = (item.get("tier") or 9) <= 2 and item.get("rung") in REGISTER_RUNGS
            if eligible and signal_id not in seen and states.get((signal_id, execution_id)) == "APPROVED":
                seen.add(signal_id)
                ready.append({"signal_id": signal_id, "package_id": row["package_id"], "version": row["version"], "run_id": row["run_id"], "profile_id": row["profile_id"], "execution_id": execution_id})
    return ready


def hand_off(pool: Pool, ba_head: Profile, items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    supervisor = supervisor_for(pool)
    at = utc_now()
    done = []
    for item in items:
        row = pool.one("SELECT * FROM packages WHERE package_id = ? AND version = ?", (item["package_id"], item["version"]))
        if row is None:
            raise ValueError("AIRIS handoff requires the exact package version.")
        supervisor.restore_package(_package(row), row["membership"].get("approval"))
        execution_id = row["membership"].get("execution_id") or execution_for(item["run_id"], item["profile_id"])
        result = supervisor.handoff_to_airis(item["signal_id"], execution_id, package_id=item["package_id"], version=item["version"], payload_valid=True, correlation_id=f"airis-{item['package_id']}", accepted_at=at)
        done.append({**item, "decision": result.get("decision"), "reason_code": result.get("reason_code")})
    return done
