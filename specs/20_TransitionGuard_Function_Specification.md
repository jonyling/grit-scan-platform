# GRIT-Scan Phase 1: TransitionGuard() Function Specification

**Function:** `TransitionGuard()`  
**Function type:** Pure deterministic workflow validation service  
**System of record:** Microsoft Dataverse  
**Workflow authority:** Supervisor Agent only  
**State ledger:** `GRIT_WorkflowState`

**Weekly extension:** In the opt-in path described by
[spec 45](45_Weekly_Agent_Reasoning_and_Critique.md), a genuine Critic invocation
references the recorded controlled assessment status and hash. Explicit
`NOT_RUN`, `NOT_ASSESSED`, and `INSUFFICIENT_DATA` outcomes do not require an
invented numeric score or `final_score_available` flag. Exact-report readiness
is checked before submitting the transition. Legacy envelopes retain their
existing behavior.

## 1. Function Purpose and Authority

`TransitionGuard()` determines whether one requested GRIT signal workflow transition is legally permitted. It evaluates only the authoritative transition envelope supplied by the Supervisor and returns a deterministic `TransitionDecisionResult`.

```text
Supervisor requests.
TransitionGuard validates.
Supervisor persists.
WorkflowState records.
```

The function is pure. It performs no Dataverse queries or hidden reads, writes no records, creates no `GRIT_WorkflowState` row, updates no `GRIT_Signals.CurrentWorkflowState` projection, invokes no agent, performs no publication or AIRIS delivery, and does not approve a package. It does not invent missing evidence or substitute reasoning for it.

`ReviewGate()` remains the sole business approval gate. `PublicationConcurrencyLock()` is technical mutual exclusion only and is never approval evidence.

TransitionGuard applies only to the controlled signal workflow. Theme/event matching, versioning, and typed register writes are validated by `RegisterWriteGate()` and do not create or require a `GRIT_WorkflowState` transition.

## 2. Input Contract

### 2.1 Authoritative transition envelope

| Input | Required | Deterministic use |
| --- | --- | --- |
| `signal_id` | Yes | Signal being transitioned. |
| `execution_id` | Yes | Authoritative execution/run identity. |
| `execution_version` | Yes | Execution version. |
| `correlation_id` | Yes | End-to-end correlation identifier. |
| `current_state` | Yes | State from the authoritative latest transition. |
| `requested_state` | Yes | Requested destination state. |
| `transition_type` | Yes | `START`, `ADVANCE`, `REWORK`, `RESTORE`, or `FAIL`. |
| `transition_reason_code` | Yes | Controlled business reason code. |
| `actor_type` | Yes | Requested transition actor type. |
| `actor_id` | Yes | Requested transition actor identity. |
| `trigger_type` | Yes | Controlled trigger type. |
| `trigger_record_id` | Conditional | Trigger lookup identity; required unless the explicit system-event rule allows null. |
| `trigger_record_exists` | Yes | Authoritative existence validation result. |
| `trigger_record_status` | Yes | Supporting record completion/status result. |
| `trigger_record_execution_id` | Conditional | Execution on supporting function or agent record. |
| `trigger_record_signal_id` | Conditional | Signal on supporting function or agent record. |
| `trigger_package_id` | Conditional | Package identity on package-related evidence. |
| `trigger_package_version` | Conditional | Exact package version. |
| `trigger_package_status` | Conditional | Authoritative package status. |
| `trigger_package_hash` | Conditional | Exact package content hash. |
| `trigger_package_membership_hash` | Conditional | Exact immutable package membership hash. |
| `trigger_approval_id` | Conditional | Approval identity where applicable. |
| `trigger_approval_package_id` | Conditional | Package identity referenced by approval. |
| `trigger_approval_package_version` | Conditional | Package version referenced by approval. |
| `trigger_approval_package_hash` | Conditional | Package hash referenced by approval. |
| `trigger_approval_membership_hash` | Conditional | Membership hash referenced by approval. |
| `current_package_membership_contains_signal` | Conditional | Whether the signal belongs to the exact immutable package. |
| `all_package_members_rework_requested` | Conditional | Package-wide rework validation result. |
| `reviewgate_outcome` | Conditional | Deterministic ReviewGate outcome. |
| `reviewgate_package_status_instruction` | Conditional | ReviewGate package instruction. |
| `reviewgate_signal_transition_instruction` | Conditional | ReviewGate signal instruction. |
| `reviewgate_new_package_required` | Conditional | ReviewGate new-package instruction. |
| `publication_confirmation_verified` | Conditional | Verified controlled publication confirmation. |
| `publication_confirmation_package_id` | Conditional | Package identity in publication confirmation. |
| `publication_confirmation_package_version` | Conditional | Package version in publication confirmation. |
| `publication_confirmation_package_hash` | Conditional | Package hash in publication confirmation. |
| `airis_handoff_payload_valid` | Conditional | Validated AIRIS handoff payload result. |
| `airis_handoff_package_id` | Conditional | Package identity in AIRIS payload. |
| `airis_handoff_package_version` | Conditional | Package version in AIRIS payload. |
| `airis_handoff_package_hash` | Conditional | Package hash in AIRIS payload. |
| `blocking_error_durably_recorded` | Conditional | Blocking error has a durable execution-log record. |
| `restoration_source_row_id` | Conditional | Trusted state row selected for restoration. |
| `restoration_source_state` | Conditional | State contained in the trusted restoration source. |
| `restoration_source_is_authoritative` | Conditional | Supervisor reconciliation result for the trusted source. |
| `expected_transition_sequence` | Yes | Sequence the Supervisor intends to persist. |
| `authoritative_latest_transition_sequence` | Yes | Latest persisted sequence for this signal/execution. |
| `authoritative_current_state_row_id` | Conditional | Latest transition row; null only when creating `NEW`. |
| `supplied_parent_transition_row_id` | Conditional | Parent row supplied for the requested transition. |
| `current_state_projection_matches_ledger` | Yes | Whether `GRIT_Signals.CurrentWorkflowState` agrees with the ledger. |
| `configuration_version` | Yes | Controlled configuration version. |
| `supervisor_version` | Yes | Supervisor version. |
| `state_graph_version` | Yes | Controlled graph version. |
| `function_version` | Yes | `TransitionGuard()` version. |
| `idempotency_key` | Yes | Transition request identity key. |
| `existing_transition_found` | Yes | Whether a persisted transition matches the idempotency key. |
| `existing_transition_input_hash` | Conditional | Canonical input hash of that persisted transition. |
| `existing_transition_row_id` | Conditional | Existing transition reference. |

### 2.2 Invocation metadata excluded from decision inputs

`invocation_id`, received/evaluated timestamps, retry attempt, duplicate/original invocation reference, retry information, signature, signature algorithm, signing key ID, and signed payload hash are recorded in `GRIT_FunctionExecutionLog`. They are not inputs to `TransitionDecisionResult` and cannot affect the decision or canonical result hash.

## 3. Controlled State Graph

| From state | Permitted destination | Required transition type |
| --- | --- | --- |
| `NEW` | `FETCHED` | `ADVANCE` |
| `FETCHED` | `DEDUPED` | `ADVANCE` |
| `DEDUPED` | `ASSESSED` | `ADVANCE` |
| `ASSESSED` | `CRITIQUED` | `ADVANCE` |
| `CRITIQUED` | `PENDING_REVIEW` | `ADVANCE` |
| `PENDING_REVIEW` | `APPROVED` | `ADVANCE` |
| `PENDING_REVIEW` | `ASSESSED` | `REWORK` only |
| `APPROVED` | `PUBLICATION_CONFIRMED` | `ADVANCE` |
| `APPROVED` | `AIRIS_HANDOFF_READY` | `ADVANCE` |
| Any non-terminal state | `FAILED` | `FAIL` |
| `FAILED` | Trusted restoration-source state | `RESTORE` |

`PUBLICATION_CONFIRMED` and `AIRIS_HANDOFF_READY` are independent post-approval terminal branches. No workflow states exist for validation, function/agent execution, retries, waiting, package `HELD`, package `REJECTED`, publication in progress, or AIRIS delivery in progress.

`REJECT` and `HOLD` are package status outcomes from `ReviewGate()`. They create no signal transition. `REJECT` sets package status `REJECTED` and requires a new package version for future review. `HOLD` sets package status `HELD` and requires human resolution and a new review submission against the unchanged package version.

## 4. Transition Matrix and Evidence Rules

All allowed transitions require `actor_type = SUPERVISOR`, a non-empty `actor_id`, and a matching signal/execution on the supporting evidence unless marked otherwise. Functions and connected agents are evidence providers, never transition actors.

| Requested transition | Required trigger type / target | Required evidence conditions |
| --- | --- | --- |
| `NEW -> FETCHED` | `FUNCTION_EXECUTION` -> `GRIT_FunctionExecutionLog` | Fetch-complete execution exists, status is successful, and record signal/execution match. |
| `FETCHED -> DEDUPED` | `FUNCTION_EXECUTION` -> `GRIT_FunctionExecutionLog` | Successful `FingerprintArticle` wire-merge execution; record signal/execution match. |
| `DEDUPED -> ASSESSED` | `AGENT_EXECUTION` -> `GRIT_AgentExecutionLog` | Assessment Agent execution completed; Supervisor validation of response schema and evidence references passed; signal/execution match. |
| `ASSESSED -> CRITIQUED` | `AGENT_EXECUTION` -> `GRIT_AgentExecutionLog`, or `FUNCTION_EXECUTION` -> critique-validation record | Completed Critic Agent execution or approved deterministic critique-validation; final deterministic score and risk band are available. `ScoreRisk()` alone is denied. |
| `CRITIQUED -> PENDING_REVIEW` | `OUTPUT_PACKAGE` -> `GRIT_OutputPackage` | Exact immutable package exists; membership contains signal through `GRIT_OutputPackageSignal`; package is `PENDING_REVIEW`; content and membership hashes exist; package is not superseded, rejected, or returned for rework. |
| `PENDING_REVIEW -> APPROVED` | `HUMAN_APPROVAL` -> `GRIT_Approval` | `ReviewGate()` outcome `APPROVE`; package instruction `APPROVED`; signal instruction `APPROVED`; exact approval references matching package ID/version/content hash/membership hash; signal is a package member. |
| `PENDING_REVIEW -> ASSESSED` | `FUNCTION_EXECUTION` -> `GRIT_FunctionExecutionLog` | `ReviewGate()` outcome `RETURN_FOR_REWORK`; package instruction `RETURNED_FOR_REWORK`; signal instruction `ASSESSED`; package membership contains signal; all package members are being reworked; new package version required. |
| `APPROVED -> PUBLICATION_CONFIRMED` | `PUBLICATION_CONFIRMATION` -> verified controlled-publication entry in `GRIT_FunctionExecutionLog` | Exact approval remains valid; confirmation is verified and references exact approved package ID/version/hash. `PublicationConcurrencyLock()` is optional technical duplicate-delivery evidence only. |
| `APPROVED -> AIRIS_HANDOFF_READY` | `OUTPUT_PACKAGE` -> `GRIT_OutputPackage` or verified AIRIS handoff record | Exact approval remains valid; AIRIS payload is valid and references exact approved package ID/version/hash. It must not depend on `PUBLICATION_CONFIRMED`. |
| Non-terminal state -> `FAILED` | Function/agent/system error record | Blocking error is durably recorded, applies to signal/execution, and reason code identifies the blocking condition. |
| `FAILED -> restoration_source_state` | `SYSTEM_EVENT`, null trigger record permitted | `RESTORE`; restoration source is authoritative, belongs to same signal/execution, is non-`FAILED`, is policy-supported, and is supplied as both parent lineage and restoration source. |

Any non-enumerated transition is denied.

## 5. Actor and Trigger Rules

### 5.1 Controlled values

**Actor types:** `SUPERVISOR`, `HUMAN`, `SYSTEM_INTEGRATION`, `PUBLISHER_CONFIRMATION`.

**Trigger types:** `FUNCTION_EXECUTION`, `AGENT_EXECUTION`, `HUMAN_APPROVAL`, `OUTPUT_PACKAGE`, `SYSTEM_EVENT`, `PUBLICATION_CONFIRMATION`.

The accepted ledger actor is always `SUPERVISOR`: it owns the persisted transition. `HUMAN`, `SYSTEM_INTEGRATION`, and `PUBLISHER_CONFIRMATION` identities are represented through the authoritative trigger record, not as substitutes for the Supervisor writer. A transition request with an agent or function as actor is denied.

`SYSTEM_EVENT` may omit `trigger_record_id` only for a `FAILED -> RESTORE` transition using reason code `SUPERVISOR_RECONCILIATION_COMPLETE`. All other legal transitions require the trigger type and target table shown in the transition matrix. Target, status, signal, execution, package, approval, and hash mismatches are denied.

## 6. Sequence, Parent, and Concurrency Rules

1. `expected_transition_sequence` must equal `authoritative_latest_transition_sequence + 1`.
2. `supplied_parent_transition_row_id` must equal `authoritative_current_state_row_id`; both are required except the initial `NEW` ledger event, which precedes this guard's `NEW -> FETCHED` transition.
3. `current_state` must equal the authoritative latest accepted state, and `current_state_projection_matches_ledger` must be true.
4. The requested transition and all mandatory evidence must match the same `signal_id` and `execution_id`.
5. Stale state, stale sequence, missing parent, or optimistic-concurrency disagreement is denied with `STOP_AND_RECONCILE`; it is never overwritten.
6. The ledger is append-only. TransitionGuard never permits modification or deletion of a historical transition.

## 7. Idempotency

The canonical transition identity includes `signal_id`, `execution_id`, `execution_version`, `current_state`, `requested_state`, `transition_type`, `trigger_type`, `trigger_record_id`, `expected_transition_sequence`, `configuration_version`, and `state_graph_version`.

- Same idempotency key and identical canonical input hash with an existing persisted transition -> `ALREADY_APPLIED` and `RETURN_EXISTING_TRANSITION`.
- Same idempotency key and a different canonical input hash -> `DENY` with `IDEMPOTENCY_CONFLICT`.
- A conflicting transition at the same sequence -> `DENY` and `STOP_AND_RECONCILE`.
- Invocation attempts remain separately auditable in `GRIT_FunctionExecutionLog`.

## 8. Output Contract

`TransitionDecisionResult` is deterministic and contains no generated timestamp, random identifier, retry count, or invocation-specific signature.

| Output | Description |
| --- | --- |
| `decision` | `ALLOW`, `DENY`, or `ALREADY_APPLIED`. |
| `transition_valid` | Boolean. |
| `from_state`, `requested_to_state`, `transition_type` | Evaluated transition. |
| `reason_code`, `reason_summary` | Stable result explanation. |
| `required_trigger_type`, `validated_trigger_record_id` | Trigger validation result. |
| `actor_valid`, `trigger_valid`, `sequence_valid`, `parent_valid`, `evidence_valid` | Individual validation outcomes. |
| `idempotency_status` | `NEW`, `ALREADY_APPLIED`, or `CONFLICT`. |
| `persistence_instruction` | `PERSIST_TRANSITION`, `RETURN_EXISTING_TRANSITION`, or `DO_NOT_PERSIST`. |
| `required_next_action` | `CONTINUE_WORKFLOW`, `STOP_AND_RECONCILE`, `CORRECT_INPUT`, or `RETURN_EXISTING_RESULT`. |
| `expected_transition_sequence` | Evaluated sequence. |
| `input_hash`, `canonical_result_hash` | Hashes of canonical input and result. |
| `function_name`, `function_version`, `state_graph_version`, `correlation_id` | Traceability values. |

## 9. Outcome Precedence

Evaluate in this order. A failure at a higher level cannot be overridden by a later passing check.

1. `INVALID_CONTRACT`
2. authorization or `INVALID_ACTOR`
3. `UNKNOWN_STATE`, `ILLEGAL_TRANSITION`, or `INVALID_TRANSITION_TYPE`
4. `CURRENT_STATE_MISMATCH`, `STALE_SEQUENCE`, `PARENT_TRANSITION_MISMATCH`, or projection disagreement
5. `INVALID_TRIGGER_TYPE`, `TRIGGER_RECORD_MISSING`, or `TRIGGER_RECORD_MISMATCH`
6. `EVIDENCE_NOT_COMPLETE`
7. package, membership, approval, ReviewGate, or hash mismatch
8. `IDEMPOTENCY_CONFLICT`
9. `ALREADY_APPLIED`
10. `ALLOW`

## 10. Pseudo-Code

```text
function TransitionGuard(envelope):
    deterministicInput = canonicalizeExcludingInvocationMetadata(envelope)
    inputHash = sha256(deterministicInput)

    if not validContract(deterministicInput):
        return deny(INVALID_CONTRACT, CORRECT_INPUT)

    if deterministicInput.actor_type != SUPERVISOR or empty(deterministicInput.actor_id):
        return deny(INVALID_ACTOR, CORRECT_INPUT)

    if not knownState(deterministicInput.current_state) or
       not knownState(deterministicInput.requested_state):
        return deny(UNKNOWN_STATE, CORRECT_INPUT)

    rule = stateGraphRule(deterministicInput.current_state,
                          deterministicInput.requested_state)
    if rule is absent:
        return deny(ILLEGAL_TRANSITION, CORRECT_INPUT)
    if deterministicInput.transition_type != rule.transition_type:
        return deny(INVALID_TRANSITION_TYPE, CORRECT_INPUT)

    if deterministicInput.expected_transition_sequence !=
       deterministicInput.authoritative_latest_transition_sequence + 1:
        return deny(STALE_SEQUENCE, STOP_AND_RECONCILE)
    if deterministicInput.supplied_parent_transition_row_id !=
       deterministicInput.authoritative_current_state_row_id:
        return deny(PARENT_TRANSITION_MISMATCH, STOP_AND_RECONCILE)
    if not deterministicInput.current_state_projection_matches_ledger:
        return deny(CURRENT_STATE_MISMATCH, STOP_AND_RECONCILE)

    if deterministicInput.existing_transition_found:
        if deterministicInput.existing_transition_input_hash == inputHash:
            return alreadyApplied(deterministicInput.existing_transition_row_id)
        return deny(IDEMPOTENCY_CONFLICT, STOP_AND_RECONCILE)

    if not triggerMatchesRule(deterministicInput, rule):
        return deny(INVALID_TRIGGER_TYPE, CORRECT_INPUT)
    if rule.requiresTriggerRecord and not deterministicInput.trigger_record_exists:
        return deny(TRIGGER_RECORD_MISSING, CORRECT_INPUT)
    if not triggerRecordMatchesSignalAndExecution(deterministicInput, rule):
        return deny(TRIGGER_RECORD_MISMATCH, CORRECT_INPUT)

    evidence = validateEvidenceForRule(deterministicInput, rule)
    if not evidence.complete:
        return deny(evidence.reason_code, CORRECT_INPUT)

    return allow(rule.required_trigger_type, inputHash)
```

`validateEvidenceForRule` implements the complete transition matrix in Section 4 and uses only fields supplied in the envelope.

## 11. Error and Reconciliation Rules

| Error code | Required result |
| --- | --- |
| `INVALID_CONTRACT` | `DENY`, `DO_NOT_PERSIST`, `CORRECT_INPUT`. |
| `UNKNOWN_STATE` / `ILLEGAL_TRANSITION` / `INVALID_TRANSITION_TYPE` | `DENY`, `DO_NOT_PERSIST`, `CORRECT_INPUT`. |
| `INVALID_ACTOR` / `INVALID_TRIGGER_TYPE` | `DENY`, `DO_NOT_PERSIST`, `CORRECT_INPUT`. |
| `TRIGGER_RECORD_MISSING` / `TRIGGER_RECORD_MISMATCH` / `EVIDENCE_NOT_COMPLETE` | `DENY`, `DO_NOT_PERSIST`, `CORRECT_INPUT`. |
| `CURRENT_STATE_MISMATCH` / `STALE_SEQUENCE` / `PARENT_TRANSITION_MISMATCH` | `DENY`, `DO_NOT_PERSIST`, `STOP_AND_RECONCILE`. |
| `PACKAGE_MEMBERSHIP_MISMATCH` / `APPROVAL_PACKAGE_MISMATCH` / `HASH_MISMATCH` / `REVIEWGATE_OUTCOME_MISMATCH` | `DENY`, `DO_NOT_PERSIST`, `CORRECT_INPUT`. |
| `PUBLICATION_CONFIRMATION_INVALID` / `AIRIS_PAYLOAD_INVALID` | `DENY`, `DO_NOT_PERSIST`, `CORRECT_INPUT`. |
| `RESTORATION_SOURCE_INVALID` | `DENY`, `DO_NOT_PERSIST`, `STOP_AND_RECONCILE`. |
| `IDEMPOTENCY_CONFLICT` | `DENY`, `DO_NOT_PERSIST`, `STOP_AND_RECONCILE`. |
| `ALREADY_APPLIED` | `ALREADY_APPLIED`, `RETURN_EXISTING_TRANSITION`, `RETURN_EXISTING_RESULT`. |

All ambiguous, missing, or contradictory authoritative inputs fail closed.

## 12. Acceptance Criteria and Test Matrix

Acceptance criteria:

- Identical canonical input produces an identical `TransitionDecisionResult` and result hash.
- No connected agent or deterministic function can authorize or persist workflow state.
- Every allowed transition has the evidence in Section 4; every other transition is denied.
- `HOLD` and `REJECT` create no signal transition.
- `RETURN_FOR_REWORK` requires package-wide transition of every member signal and a new package version.
- Publication and AIRIS transitions are independent.
- `PublicationConcurrencyLock()` cannot authorize publication.
- Stale and concurrent requests do not overwrite the ledger; exact duplicates are reusable.
- Historical ledger rows remain immutable.

| Test case | Expected decision | Key assertion |
| --- | --- | --- |
| `NEW -> FETCHED` with successful matching fetch log | `ALLOW` | Function trigger and signal/execution match. |
| `FETCHED -> DEDUPED` with successful fingerprint/wire merge | `ALLOW` | Function evidence is complete. |
| `DEDUPED -> ASSESSED` with validated Assessment Agent log | `ALLOW` | Agent trigger and validated evidence required. |
| `ASSESSED -> CRITIQUED` with Critic Agent and final score | `ALLOW` | Critic evidence required. |
| `ASSESSED -> CRITIQUED` with only ScoreRisk | `DENY` | `EVIDENCE_NOT_COMPLETE`. |
| `CRITIQUED -> PENDING_REVIEW` with immutable member package | `ALLOW` | Package hashes and membership required. |
| `PENDING_REVIEW -> APPROVED` with exact ReviewGate/approval | `ALLOW` | Approval, package, membership, and hashes match. |
| `PENDING_REVIEW -> ASSESSED` with package-wide ReviewGate rework | `ALLOW` | All-members rework and new package required. |
| `APPROVED -> PUBLICATION_CONFIRMED` with verified exact confirmation | `ALLOW` | Publication confirmation, not lock, proves delivery. |
| `APPROVED -> AIRIS_HANDOFF_READY` without publication confirmation | `ALLOW` | AIRIS branch is independent. |
| `PENDING_REVIEW -> ASSESSED` from reject or hold | `DENY` | No signal transition permitted. |
| Any non-enumerated state pair | `DENY` | `ILLEGAL_TRANSITION`. |
| Valid pair with agent actor | `DENY` | `INVALID_ACTOR`. |
| Valid pair with missing trigger record | `DENY` | `TRIGGER_RECORD_MISSING`. |
| Valid pair with wrong package/hash | `DENY` | Relevant package/hash mismatch. |
| Valid pair with stale current state or sequence | `DENY` | `STOP_AND_RECONCILE`. |
| Exact duplicate idempotency input | `ALREADY_APPLIED` | Existing transition returned. |
| Same idempotency key, different canonical input | `DENY` | `IDEMPOTENCY_CONFLICT`. |
| `FAILED -> FETCHED` with trusted source | `ALLOW` | Restore preserves failed row and references trusted source. |
| `FAILED -> APPROVED` without trusted approved source | `DENY` | `RESTORATION_SOURCE_INVALID`. |
