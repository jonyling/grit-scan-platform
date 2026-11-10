# GRIT-Scan Phase 1: ReviewGate() Function Specification

**Function:** `ReviewGate()`  
**Function type:** Deterministic package-review decision service  
**System of record:** Microsoft Dataverse  
**Workflow authority:** Supervisor Agent only  
**Review scope:** Review Package, never an individual signal

## 1. Functional specification

`ReviewGate()` evaluates a human review submission for one immutable `GRIT_OutputPackage` version. It determines the package review outcome. It does not approve, release, or publish a package. It does not write Dataverse records or workflow state.

The Supervisor records the invocation. The Supervisor is the only component that creates `GRIT_Approval`, changes package status, or writes `GRIT_WorkflowState` transitions.

`ReviewGate()` is the sole business approval gate. It replaces `PublishLock()`. The publication integration can use a technical `PublicationConcurrencyLock()` to prevent concurrent delivery of the same approved package. That lock is not an approval gate. It cannot approve a package, and it does not affect review outcomes.

The function follows Architecture A. The Supervisor supplies all authoritative values from Dataverse. `ReviewGate()` performs no Dataverse queries, hidden reads, or time-dependent evaluation.

A Review Package can contain independently identifiable Strategic Horizon and Operational Risk sections when both use the same approved evidence population. `ReviewGate()` evaluates the immutable package version and its membership as a whole. A theme, event, or assessment proposal does not become publishable only because it appears in a package.

## 2. Inputs

### 2.1 Review submission

| Input | Required | Description |
| --- | --- | --- |
| `review_package_id` | Yes | Lookup identity of the exact `GRIT_OutputPackage` version. |
| `review_package_version` | Yes | Immutable package version. |
| `review_package_hash` | Yes | Hash presented to the reviewer. |
| `reviewer_id` | Yes | Dataverse user identity of the human reviewer. |
| `reviewer_intent` | Yes | `APPROVE`, `REJECT`, `HOLD`, or `REQUEST_REWORK`. |
| `review_findings` | Yes | Canonically ordered reviewer findings. |
| `review_timestamp_utc` | Yes | Submitted UTC timestamp; retained as invocation metadata, not decision input. |
| `correlation_id` | Yes | End-to-end correlation identifier. |
| `idempotency_key` | Yes | Stable key for the package version and review submission. |

### 2.2 Authoritative context supplied by Supervisor

| Input | Required | Description |
| --- | --- | --- |
| `authoritative_package_hash` | Yes | Hash read by Supervisor from the immutable package version. |
| `authoritative_membership_hash` | Yes | Hash of the immutable `GRIT_OutputPackageSignal` membership snapshot. |
| `submitted_membership_hash` | Yes | Membership hash displayed to and acknowledged by the reviewer. |
| `package_current_status` | Yes | Package status from Dataverse; must be `PENDING_REVIEW` for an approval decision. |
| `package_completeness_known` | Yes | Whether required package membership can be determined. |
| `missing_signal_ids` | Yes | Canonically ordered list of package signals known to be absent or incomplete. |
| `reviewer_is_human` | Yes | Supervisor's authoritative identity validation result. |
| `reviewer_is_eligible` | Yes | Supervisor's authorization and role validation result. |
| `separation_of_duties_passed` | Yes | Supervisor's policy evaluation result. |
| `policy_is_active` | Yes | Whether the supplied approval policy is active. |
| `approval_policy_version` | Yes | Policy version used for eligibility and decision rules. |
| `configuration_version` | Yes | Configuration version used to create the package. |
| `function_version` | Yes | Version of `ReviewGate()` being invoked. |

### 2.3 Review findings

Each finding contains `finding_id`, `finding_type`, `severity`, `affected_signal_id` where applicable, `description`, `evidence_reference` where policy requires it, and `resolution_required`.

`finding_type` values are `MISSING_SIGNAL`, `INCORRECT_CLASSIFICATION`, `MISSING_EVIDENCE`, `MATERIAL_OMISSION`, and `OTHER`. The first four types always require rework, regardless of severity or reviewer intent. `OTHER` requires rework only when `resolution_required = true` or `severity = MATERIAL`.

## 3. Outputs

### 3.1 ReviewDecisionResult

The deterministic result contains no runtime timestamp, random identifier, retry count, or invocation-specific signature.

| Output | Description |
| --- | --- |
| `review_outcome` | `APPROVE`, `REJECT`, `HOLD`, or `RETURN_FOR_REWORK`. |
| `package_status_instruction` | `APPROVED`, `REJECTED`, `HELD`, or `RETURNED_FOR_REWORK`. |
| `signal_transition_instruction` | `APPROVED`, `ASSESSED`, or `NONE`. |
| `required_next_action` | `PERSIST_APPROVAL`, `PERSIST_REJECTION`, `RESOLVE_HOLD`, `CREATE_NEW_PACKAGE_VERSION`, or `STOP_AND_RECONCILE`. |
| `approval_eligible` | True only for `APPROVE`. |
| `new_package_required` | True for `REJECT` and `RETURN_FOR_REWORK`; otherwise false. |
| `missing_signal_ids` | Canonical list supplied/validated by the Supervisor. |
| `material_finding_ids` | Canonically sorted rework-causing findings. |
| `reason_code` | Controlled deterministic reason code. |
| `reason_summary` | Stable explanation generated from canonical inputs. |
| `package_id`, `package_version`, `package_hash` | Exact review subject. |
| `input_hash` | Hash of the canonical function input excluding invocation metadata. |
| `canonical_result_hash` | Hash of the canonical `ReviewDecisionResult`. |
| `function_name`, `function_version`, `correlation_id` | Traceability values. |

### 3.2 ReviewGateInvocation

Invocation data is execution metadata, not part of the deterministic decision result. `GRIT_FunctionExecutionLog` records `invocation_id`, first evaluation timestamp, attempt number, duplicate/original invocation reference, retry metadata, `signed_payload_hash`, `signature`, `signature_algorithm`, and `signing_key_id`.

The signed payload is the canonical `ReviewDecisionResult`. `signed_payload_hash` equals `canonical_result_hash`. Signing occurs after deterministic evaluation and does not change the result hash.

### 3.3 Outcome precedence

1. Invalid contract, package/hash/membership mismatch, inactive policy, unknown completeness, non-human/ineligible reviewer, or failed separation of duties -> `HOLD` / `HELD` / `NONE` / `STOP_AND_RECONCILE`.
2. A missing signal, incorrect classification, missing evidence, material omission, material `OTHER` finding, or `REQUEST_REWORK` intent -> `RETURN_FOR_REWORK` / `RETURNED_FOR_REWORK` / `ASSESSED` / `CREATE_NEW_PACKAGE_VERSION`.
3. `REJECT` intent -> `REJECT` / `REJECTED` / `NONE` / `PERSIST_REJECTION`.
4. `HOLD` intent -> `HOLD` / `HELD` / `NONE` / `RESOLVE_HOLD`.
5. `APPROVE` intent with all checks passing -> `APPROVE` / `APPROVED` / `APPROVED` / `PERSIST_APPROVAL`.
6. Any remaining contradictory input -> `HOLD` / `HELD` / `NONE` / `STOP_AND_RECONCILE`.

## 4. Review package data model

`GRIT_OutputPackage` is the immutable review subject and approval anchor. `GRIT_OutputPackage` and `GRIT_Signals` each have a one-to-many relationship with `GRIT_OutputPackageSignal`. This membership entity preserves exact version membership, signal version, signal content hash, and ordering.

### 4.1 GRIT_OutputPackageSignal

| Field | Type | Requirement |
| --- | --- | --- |
| `OutputPackageId` | Lookup to `GRIT_OutputPackage` | Required. |
| `SignalId` | Lookup to `GRIT_Signals` | Required. |
| `SignalVersion` | Text/whole number per signal version model | Required. |
| `SignalContentHash` | Text, 128 characters | Required. |
| `MembershipSequence` | Whole number | Required; unique per package version. |
| `IncludedAtUtc` | Date/time, time zone independent, UTC | Required. |
| `InclusionReason` | Controlled text/choice | Required. |

The package content and membership become immutable at `PENDING_REVIEW`. A package version can be reused as historical evidence. It cannot be altered or approved after it is superseded, rejected, or returned for rework.

## 5. Package versioning rules

1. A package version is immutable once submitted to `PENDING_REVIEW`.
2. The package ID, version, content hash, and membership hash identify the review subject.
3. `APPROVE` applies only to the exact immutable package version.
4. `HOLD` changes package status to `HELD`. No signal transitions occur, and no new version is required. A later review submission against the unchanged version requires a new human review decision.
5. `REJECT` changes package status to `REJECTED`. No signal transitions occur. The version is permanently ineligible for release. Any future review requires a new package version.
6. `RETURN_FOR_REWORK` changes package status to `RETURNED_FOR_REWORK`. The entire package version becomes invalid for release. A new package version is mandatory.
7. The replacement package must have a new version, content hash, and membership hash. It must link to the old package through `supersedes`.
8. A material change to membership, signal content, classification, evidence, scoring result, or configuration requires a new package version.
9. Identical canonical inputs for the same idempotency key return the original result. A different input for the same key is a `HOLD` idempotency violation.

## 6. Approval rules

1. Only an eligible human reviewer with passed separation of duties can request approval.
2. `ReviewGate()` is the only approval gate. Agents, functions, automated flows, and the Supervisor cannot approve or release a package.
3. The Supervisor creates `GRIT_Approval` only after an `APPROVE` result. It must look up the exact `GRIT_OutputPackage` version and store the approved package and membership hashes.
4. The Supervisor invokes `TransitionGuard()` for each `PENDING_REVIEW -> APPROVED` request and writes the transition only after `ALLOW`. Each transition references the same `GRIT_Approval` record.
5. Approval permits controlled publication integration and controlled AIRIS handoff. It does not itself deliver either branch.
6. `PublicationConcurrencyLock()` can prevent duplicate technical delivery after approval. It cannot provide approval, override `ReviewGate()`, or write workflow state.

## 7. ReturnForRework rules

`RETURN_FOR_REWORK` is mandatory for missing signals, incorrect classification, missing evidence, material omissions, material other findings, or `REQUEST_REWORK` intent.

Rework applies to the whole package. The current package version becomes invalid for release, and all member signals leave `PENDING_REVIEW`. For every member signal, the Supervisor invokes `TransitionGuard()`. It then writes `PENDING_REVIEW -> ASSESSED` with `TransitionType = REWORK`. `affected_signal_id` identifies remediation scope only. It must never leave other signals active in the old review package.

The Supervisor records the `ReviewGate()` execution and marks the package `RETURNED_FOR_REWORK`. It creates no approval. After assessment and critique remediation, it creates a new package version. The replacement version requires a complete new review cycle.

## 8. Workflow state transitions

```text
PENDING_REVIEW -- ReviewGate APPROVE --> APPROVED

PENDING_REVIEW -- ReviewGate RETURN_FOR_REWORK --> ASSESSED

APPROVED --> CONTROLLED_PUBLICATION_INTEGRATION --> PUBLICATION_CONFIRMED
APPROVED --> CONTROLLED_AIRIS_HANDOFF -----------> AIRIS_HANDOFF_READY
```

`REJECT` and `HOLD` do not create signal transitions. The Supervisor does not invoke `TransitionGuard()` for them. They update package status only. `REJECT` requires a new package for any future review. `HOLD` retains the same immutable package version pending human resolution and a new review submission.

`CRITIQUED` requires a completed `GRIT_AgentExecutionLog` record for the Critic Agent or a completed deterministic critique-validation package. `ScoreRisk()` completion alone is insufficient evidence for the `ASSESSED -> CRITIQUED` transition.

## 9. Dataverse tables and relationships

| Table | Use |
| --- | --- |
| `GRIT_OutputPackage` | Immutable package version, package status, content hash, membership hash, and supersedes lookup. |
| `GRIT_OutputPackageSignal` | Immutable package-to-signal membership snapshot. |
| `GRIT_Signals` | Signal records and `CurrentWorkflowState` projection. |
| `GRIT_Approval` | Human decision tied to exactly one output package version. |
| `GRIT_WorkflowState` | Append-only transition ledger. Only the Supervisor writes it, after `TransitionGuard()` returns `ALLOW`. |
| `GRIT_FunctionExecutionLog` | `ReviewGate()` and `TransitionGuard()` invocation metadata, deterministic result, signature, retries, and idempotency record. |
| `GRIT_AgentExecutionLog` | Critic/other reasoning evidence only; never approval or release authority. |

## 10. Audit trail requirements

For every invocation, record these values in Dataverse:

- the canonical input and `ReviewDecisionResult`;
- input/result hashes, and package and membership hashes;
- function/policy/configuration versions;
- reviewer identity, package lookups, and findings;
- invocation metadata and signature fields.

Preserve duplicate, held, rejected, and returned decisions. Do not overwrite them.

For an approval, the `GRIT_Approval` lookup must prove the exact package version/hashes. For rework, every package-member signal transition must reference the `ReviewGate()` function execution. It must also reference an auditable `TransitionGuard()` decision of `ALLOW`. All timestamps are UTC. Decision determinism depends on canonical payloads that exclude invocation timestamps.

## 11. Error handling

| Condition | Outcome / instruction |
| --- | --- |
| Package, hash, membership, reviewer, policy, or contract cannot be verified | `HOLD` / `HELD` / `NONE` / `STOP_AND_RECONCILE`. |
| Package completeness known and signals are missing | `RETURN_FOR_REWORK` / `RETURNED_FOR_REWORK` / `ASSESSED`. |
| Required finding attributes missing or contradictory | `HOLD` / `HELD` / `NONE` / `STOP_AND_RECONCILE`. |
| Duplicate idempotency key, same canonical input | Return the original `ReviewDecisionResult`. |
| Duplicate idempotency key, different input | `HOLD` / `HELD` / `NONE` / `STOP_AND_RECONCILE`. |
| Dataverse persistence fails | Result is not effective; Supervisor stops and reconciles. |
| Function timeout or throttling | Retry under `RetryBudget`; log attempts. No decision is synthesized. |
| Approval write succeeds but transitions fail | Preserve approval, block all release branches, and reconcile. |

## 12. Example approve flow

1. The Supervisor reads `RP-2026-00041-V1` with its immutable membership, package hash, and membership hash. It also reads reviewer eligibility and the active policy. It builds the canonical input.
2. The eligible human reviewer submits `APPROVE` with no rework-causing findings.
3. `ReviewGate()` returns `APPROVE`, `package_status_instruction = APPROVED`, `signal_transition_instruction = APPROVED`, and `PERSIST_APPROVAL`.
4. The Supervisor logs the signed result. It creates one `GRIT_Approval` for the exact package version and marks the package `APPROVED`. It invokes `TransitionGuard()` for every `GRIT_OutputPackageSignal` member. It writes a `PENDING_REVIEW -> APPROVED` transition only after each `ALLOW` result.
5. The independently controlled publication and AIRIS handoff integrations can now operate. `PublicationConcurrencyLock()` only prevents duplicate delivery.

## 13. Example ReturnForRework flow

1. `RP-2026-00041-V2` contains signals A and B. The reviewer submits `REQUEST_REWORK` with `MISSING_EVIDENCE` for A and `MISSING_SIGNAL` for B.
2. The Supervisor supplies the authoritative package and membership hashes plus the canonical findings to `ReviewGate()`.
3. `ReviewGate()` returns `RETURN_FOR_REWORK`, `package_status_instruction = RETURNED_FOR_REWORK`, `signal_transition_instruction = ASSESSED`, and `CREATE_NEW_PACKAGE_VERSION`.
4. The Supervisor logs the signed result and invalidates V2 for release. It invokes `TransitionGuard()` for both A and B. It writes `PENDING_REVIEW -> ASSESSED` only after each `ALLOW` result. It creates no approval.
5. Remediation produces V3 with a new immutable membership snapshot and hashes. V3 must repeat critique and package-level review.

## 14. Pseudo-code

```text
function ReviewGate(input):
    canonicalInput = canonicalizeExcludingInvocationMetadata(input)
    inputHash = sha256(canonicalInput)

    if not validContract(canonicalInput) or
       not canonicalInput.policy_is_active or
       not canonicalInput.package_completeness_known or
       canonicalInput.review_package_hash != canonicalInput.authoritative_package_hash or
       canonicalInput.submitted_membership_hash != canonicalInput.authoritative_membership_hash or
       not canonicalInput.reviewer_is_human or
       not canonicalInput.reviewer_is_eligible or
       not canonicalInput.separation_of_duties_passed:
        return decision(HOLD, HELD, NONE, STOP_AND_RECONCILE)

    reworkFindings = findingsThatRequireRework(canonicalInput.review_findings)
    if canonicalInput.reviewer_intent == REQUEST_REWORK or
       canonicalInput.missing_signal_ids is not empty or
       reworkFindings is not empty:
        return decision(RETURN_FOR_REWORK, RETURNED_FOR_REWORK,
                        ASSESSED, CREATE_NEW_PACKAGE_VERSION)

    if canonicalInput.reviewer_intent == REJECT:
        return decision(REJECT, REJECTED, NONE, PERSIST_REJECTION)

    if canonicalInput.reviewer_intent == HOLD:
        return decision(HOLD, HELD, NONE, RESOLVE_HOLD)

    if canonicalInput.reviewer_intent == APPROVE and
       canonicalInput.package_current_status == PENDING_REVIEW:
        return decision(APPROVE, APPROVED, APPROVED, PERSIST_APPROVAL)

    return decision(HOLD, HELD, NONE, STOP_AND_RECONCILE)
```

## 15. Implementation Acceptance Criteria

- `ReviewGate()` is the only approval gate and has no hidden reads.
- Every signal transition caused by a ReviewGate result requires `TransitionGuard() = ALLOW` before the Supervisor persists it.
- Reviewer intent `REQUEST_REWORK` is explicitly supported.
- Rework conditions override all reviewer intents and invalidate the whole package version.
- `HOLD` preserves the package version without signal transitions; `REJECT` permanently closes that version and requires a new version for a future review.
- `ReviewDecisionResult` is deterministic; invocation timestamps, retries, and signatures are separate metadata.
- `GRIT_OutputPackageSignal` provides immutable, ordered membership for package versions.
- `CRITIQUED` has Critic Agent or critique-validation evidence, not merely `ScoreRisk()` evidence.
- Publication and AIRIS are independent post-approval branches using `PUBLICATION_CONFIRMED` and `AIRIS_HANDOFF_READY`.
- Strategic and operational package sections use the same ReviewGate approval model; no unreviewed strategic-pack shortcut exists.
