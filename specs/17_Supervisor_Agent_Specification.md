# GRIT-Scan Phase 1: Supervisor Agent Architecture Specification

**Document status:** Proposed
**Owner:** Group Risk & Assurance
**System of record:** Microsoft Dataverse
**Workflow authority:** Supervisor Agent only
**Publication authority:** Human approver only

**Weekly implementation:** [Spec 45: Weekly agent reasoning and critique](45_Weekly_Agent_Reasoning_and_Critique.md)
defines the opt-in six-role coordinator. Its model Supervisor chooses the next
permitted work. Deterministic services keep workflow and release authority.
Editorial inclusion is an agent decision, separate from controlled scores.

## 1. Mission

The Supervisor Agent turns a scan request into an approval-ready risk-monitoring package. It is the authoritative workflow orchestrator for GRIT-Scan Phase 1.

For each scan request, the Supervisor Agent coordinates the Phase 1 deterministic function set and the connected reasoning agents. It persists every state transition and execution event to Dataverse.

The Supervisor Agent can sequence work, validate preconditions, and invoke approved capabilities. It can also aggregate returned results, request human decisions, and recover retryable failures. It must not publish or approve its own output. It must not change a controlled risk score by reasoning alone. It must not allow a connected agent to control workflow state.

## 2. Responsibilities

The Supervisor Agent shall:

1. Create or resume a uniquely identified scan execution and establish its correlation and idempotency keys.
2. Own the workflow state and be the only component permitted to request and persist a state transition after `TransitionGuard()` returns `ALLOW`.
3. Validate request completeness, authorization, source scope, configuration version, and required Dataverse records before execution.
4. Plan and sequence work using the Phase 1 workflow defined in Section 5.
5. Invoke only functions listed in the Phase 1 Function Catalog for controlled operations.
6. Invoke connected agents for bounded reasoning tasks. The Assessment Agent can propose category, rationale, likelihood, and impact. It must not generate the final score or final risk band.
7. Treat agent responses as untrusted proposals. Validate their schema, provenance, confidence, and permitted action before accepting them.
8. Persist the request, current state, transition history, `TransitionGuard()` decision, function calls, agent calls, decisions, errors, evidence references, and output versions to Dataverse.
9. Enforce human approval before any publication request can be released to a publishing process.
10. Support resume, retry, rejection, cancellation, and controlled failure without losing the audit trail.
11. Return a traceable execution result containing the scan identifier, current state, output version, approval status, and unresolved exceptions.
12. Select the governed `GRIT_ReaderProfile` and invoke `ApertureRouting()`. Coordinate shared theme/event register searches with reader-specific impact assessment, without overwriting shared facts.
13. Accept governed `GRIT_ManagementRequest` intake and post-publication `GRIT_TrackingFeedback` as inputs only. Neither input approves, publishes, or mutates a shared signal, event, or theme.

The Supervisor Agent shall not:

- delegate workflow-state control to another agent or function;
- directly publish, distribute, or mark an output as published;
- treat an Assessment Agent proposal as the final risk score or final risk band;
- overwrite historical records;
- continue after an authorization, evidence-integrity, approval, or Dataverse persistence failure;
- mutate source evidence or approved output in place.

## 3. Inputs

### 3.1 Scan request

| Input | Required | Description |
| --- | --- | --- |
| `request_id` | Yes | Caller-supplied request identifier; used for idempotency where available. |
| `scan_type` | Yes | Phase 1 scan operation, for example `entity`, `supplier`, or `monitoring_review`. |
| `requestor` | Yes | Dataverse user or service principal identity. |
| `business_scope` | Yes | Group, BA, LOB, entity, supplier, country, or other approved scope. |
| `source_scope` | Yes | Approved documents, feeds, repositories, or source identifiers to inspect. |
| `requested_period` | Yes | Reporting or monitoring period and timezone. |
| `configuration_version` | Yes | Version of taxonomy, keyword library, risk definitions, and scoring configuration. |
| `requested_output` | Yes | Output type and intended audience. |
| `priority` | No | Normal, high, or urgent, subject to authorization. |
| `resume_execution_id` | No | Existing execution to resume after interruption or human action. |

### 3.2 Runtime and control inputs

- Identity and authorization context.
- Active Supervisor configuration and connected-agent allowlist.
- Deterministic function versions and checksums.
- Dataverse environment and table references.
- Retry policy and execution time limits.
- Human approval policy, including required approver role and separation-of-duties rules.
- Phase 1 Function Catalog entries and enabled function versions.

### 3.3 Connected-agent response contract

Every connected-agent response must include:

- `invocation_id` and `execution_id`;
- requested task and response schema version;
- proposed facts, classifications, narratives, or recommendations;
- evidence references for each material claim;
- confidence and uncertainty indicators;
- model/agent version and completion timestamp;
- a statement that the response is a proposal and not a workflow transition;
- explicit theme/event relationship proposals and reader-specific impact proposals where applicable. These proposals are not authoritative register writes.

## 4. Outputs

### 4.1 Successful execution output

The Supervisor Agent returns an execution envelope containing:

- `scan_id`, `execution_id`, and `correlation_id`;
- final workflow state;
- output package identifier and immutable output version;
- risk signals, mapped taxonomy, severity, likelihood, calculated rating, and rationale;
- source evidence references and evidence-quality status;
- control gaps, watchpoints, action plans, owners, and due dates where applicable;
- approval status, approver identity, decision timestamp, and decision comments;
- execution summary, warnings, retry count, and unresolved exceptions;
- Dataverse audit references for every action taken.

### 4.2 Non-success output

For a stopped or incomplete execution, the Supervisor Agent returns the current business state and stop reason. It also returns safe remediation guidance and the relevant function or agent execution identifiers. The Supervisor Agent must mark a partial output `DRAFT` or `INCOMPLETE`. A partial output must never be represented as published.

## 5. State transition rules

The state machine represents the business flow. The execution logs record technical activity such as validation, retries, function execution, and agent execution. Technical activity does not create additional workflow states.

### 5.1 Controlled states

| State | Meaning | Exit condition |
| --- | --- | --- |
| `NEW` | Signal accepted and awaiting the first controlled workflow action. | Successful approved-source fetch -> `FETCHED`; blocking failure -> `FAILED`. |
| `FETCHED` | Approved source material has been retrieved for the scan. | Fetch complete -> `DEDUPED`; blocking fetch failure is recorded in the logs and the execution stops. |
| `DEDUPED` | Duplicate or materially identical items have been identified and wire-merged using deterministic functions. | Deduplication complete -> `ASSESSED`. |
| `ASSESSED` | The Assessment Agent has produced proposals and the Supervisor has validated their structure and evidence references. | Assessment complete -> `CRITIQUED`. |
| `CRITIQUED` | Critic Agent execution or deterministic critique-validation is complete; deterministic scores and risk bands are available but `ScoreRisk()` alone is insufficient. | Critique complete -> `PENDING_REVIEW`. |
| `PENDING_REVIEW` | An immutable output package is awaiting human review. | Approve -> `APPROVED`; return for rework -> `ASSESSED` with a new package version; reject -> no signal transition, package status `REJECTED`, new package version required for future review; hold -> no signal transition, package status `HELD`. |
| `APPROVED` | A human has approved the exact output package version through `ReviewGate()`. | Can independently proceed to `PUBLICATION_CONFIRMED` and `AIRIS_HANDOFF_READY` through controlled integrations. |
| `PUBLICATION_CONFIRMED` | Controlled publication integration has confirmed delivery of an approved package. | Post-approval branch terminal. |
| `AIRIS_HANDOFF_READY` | The approved package is ready for controlled downstream AIRIS handoff. | Post-approval branch terminal. |
| `FAILED` | A blocking, durably recorded error prevents continuation. | Can restore only through a trusted source and `TransitionType = RESTORE`. |

### 5.2 Transition invariants

1. A transition must be issued by the Supervisor Agent, receive `ALLOW` from `TransitionGuard()`, and be persisted before the next business action begins.
2. Every transition records `from_state`, `to_state`, reason, actor, timestamp, correlation ID, configuration version, execution version, and outcome.
3. Transitions are append-only. The current state is a projection of the latest successful transition, not a replacement for history.
4. Only enumerated transitions are valid. No component can write an arbitrary state value.
5. A transition and its required audit event must succeed together. If persistence is uncertain, the Supervisor must stop and reconcile before continuing.
6. `PUBLICATION_CONFIRMED` requires human approval through `ReviewGate()` for the exact immutable output version and verified controlled-publication confirmation.
7. `PublicationConcurrencyLock()` can prevent duplicate technical delivery after approval. It is not an approval gate. No agent can publish or mark an output as published.
8. Any material change to evidence, assessment proposals, scoring configuration, or output after approval invalidates that approval and returns the execution to `ASSESSED` with a new output package version.
9. Reprocessing creates a new execution version linked to the original. Historical state transitions and output packages remain unchanged.

### 5.3 Required execution sequence

`NEW -> FETCHED -> DEDUPED -> ASSESSED -> CRITIQUED -> PENDING_REVIEW -> APPROVED`

From `APPROVED`, controlled publication and controlled AIRIS handoff are independent branches to `PUBLICATION_CONFIRMED` and `AIRIS_HANDOFF_READY`.

An execution can enter `FAILED` from any non-terminal state, but only for a blocking, durably recorded error. A `FAILED` execution can be restored only under the `TransitionGuard()` trusted-restoration rule.

The sequence can pause while the Supervisor handles a retryable operation. The business state does not change only because a retry occurs. A non-retryable error stops the execution, and the Supervisor records it in the execution log. The Supervisor records `PUBLICATION_CONFIRMED` only after a verified controlled-publication confirmation. An agent's reasoning response never sets `PUBLICATION_CONFIRMED`.

### 5.4 Assessment and scoring boundary

The Assessment Agent is a reasoning component. It generates only:

- category;
- rationale;
- proposed likelihood;
- proposed impact.

The Supervisor validates the Assessment Agent response and records it in `GRIT_AgentExecutionLog`. The Supervisor then passes the approved proposal and required configuration to `ScoreRisk()`.

`ScoreRisk()` is deterministic and generates only:

- final score;
- final risk band.

The final score and final risk band used in the output package must come from `ScoreRisk()`. The Assessment Agent, Supervisor prompt, or any other connected agent cannot replace or edit those values.

### 5.5 Phase 1 Function Catalog and deterministic function set

These services are first-class deterministic services in Phase 1. Each service has a versioned catalog entry, a defined input/output contract, an idempotency key, and an execution record.

| Function | Phase 1 responsibility |
| --- | --- |
| `FingerprintArticle` | Generate a stable content fingerprint and deterministically wire-merge duplicate or materially identical source items. |
| `CredibilityLookup` | Return the configured credibility result for a source or publisher. |
| `KRICalculation` | Calculate configured key risk indicator values from validated inputs. |
| `QueueRouting` | Select the configured processing or review queue from scope, category, priority, and routing rules. |
| `PublicationConcurrencyLock` | Atomically prevent duplicate technical delivery of an approved output version; this is not an approval gate. |
| `RetryBudget` | Return whether an operation can retry and record consumption of the configured retry budget. |
| `ScoreRisk` | Calculate the final risk score and final risk band from validated inputs and the approved scoring configuration. |
| `TransitionGuard` | Purely validate requested workflow transitions, including graph, actor, trigger, evidence, sequence, parent, restoration, and idempotency rules; it never writes state. |
| `EventMatch` | Compare a validated signal against the governed event register and return candidate matches only. |
| `ThemeMatch` | Compare an event against the governed theme register and return controlled candidate matches only. |
| `RegisterWriteGate` | Validate typed theme, event, relationship, and impact-assessment write proposals without reasoning or workflow-state mutation. |
| `ApertureRouting` | Deterministically select assessment and reporting routes from the governed reader profile and configuration. |
| `ThemeMetricCalculation` | Calculate controlled evidence coverage, source diversity, movement, and other approved theme metrics. |
| `ThemeEscalationAssessment` | Purely evaluate controlled theme metrics and return direction, velocity, escalation recommendation, and lifecycle-state proposal; it never writes or changes signal workflow state. |
| `PackAssembly` | Assemble policy-permitted strategic and operational package sections while preserving citations and drill-down links. |

`FingerprintArticle` provides the Phase 1 fingerprint/wire-merge service capability.

The following functions are independently callable and auditable: `CredibilityLookup`, `QueueRouting`, `PublicationConcurrencyLock`, `RetryBudget`, `TransitionGuard`, `EventMatch`, `ThemeMatch`, `RegisterWriteGate`, `ApertureRouting`, `ThemeMetricCalculation`, `ThemeEscalationAssessment`, and `PackAssembly`. No agent can implement or emulate these functions through reasoning.

## 6. Retry rules

1. Retry only transient failures: throttling, temporary network failure, service unavailability, optimistic-concurrency conflict, or an explicitly classified timeout.
2. Do not retry authorization failures, invalid input, schema violations, missing mandatory evidence, policy violations, human rejection, or deterministic function errors until the input/configuration is corrected.
3. Use bounded exponential backoff with jitter. The Phase 1 default is a maximum of 3 attempts per operation, with delays of 30 seconds, 2 minutes, and 10 minutes. Configuration can lower these limits.
4. Persist each attempt before retrying, including attempt number, operation key, error category, delay, and next-attempt time.
5. All function and agent operations must be idempotent using `execution_id`, operation name, input hash, and configuration version. A repeated request must return or reuse the existing result.
6. Never retry a connected agent by silently changing the task, evidence set, or prompt. Any changed request is a new invocation and must be audited.
7. After retry exhaustion, persist the failure in `GRIT_FunctionExecutionLog` or `GRIT_AgentExecutionLog`. Stop the current operation. Require Supervisor-controlled remediation or a new execution.
8. Human approval and publication confirmation are never automatically retried or synthesized.

## 7. Error handling rules

### 7.1 Classification

| Error class | Supervisor action |
| --- | --- |
| `VALIDATION_ERROR` | Persist details, do not invoke downstream work, and stop the current execution. |
| `AUTHORIZATION_ERROR` | Persist security event and stop execution; do not invoke downstream work. |
| `SOURCE_ERROR` | Preserve source status; retry only if transient, otherwise stop the execution. |
| `SCHEMA_ERROR` | Reject the response, record the offending invocation, and retry only with the same contract if transient infrastructure caused it. |
| `DETERMINISTIC_FUNCTION_ERROR` | Fail closed, persist function version and input hash, and never substitute model judgment. |
| `AGENT_ERROR` | Record invocation failure; retry if transient, otherwise stop the current operation. |
| `PERSISTENCE_ERROR` | Stop all business actions immediately and attempt reconciliation. No state can advance without durable audit records. |
| `APPROVAL_ERROR` | Do not advance; require a valid human approval for the exact output version. |
| `PUBLICATION_ERROR` | Do not advance to `PUBLICATION_CONFIRMED`; preserve the approved package and record the publication failure. |
| `POLICY_VIOLATION` | Fail closed, alert the designated owner, and block publication. |

### 7.2 General rules

- Errors are structured, severity-classified, correlation-linked, and append-only.
- Logs must not contain secrets or unnecessary personal data. Logs reference sensitive payloads through secured Dataverse records.
- Partial work is retained with explicit completeness and validity flags.
- The Supervisor emits an operator-visible exception containing the action required, owner, deadline, and affected execution.
- The Supervisor treats any unhandled error as blocking. It persists what is known, stops, and requires reconciliation before resumption or reprocessing.
- The Supervisor raises alerts for repeated failures, policy violations, stuck executions, approval timeout, and any attempted unauthorized transition or publication.

## 8. Dataverse tables required

The Phase 1 Dataverse model starts with the following tables. The table shows logical names as requested. Implement them with the solution publisher prefix where required. Each table must have Dataverse auditing enabled, created/modified metadata, and row-level security appropriate to its contents.

| Table | Purpose | Minimum required columns |
| --- | --- | --- |
| `GRIT_WorkflowState` | Current business state and append-only state transition history. | Workflow state ID, scan/execution ID, state, previous state, transition reason, actor, timestamp, sequence number, correlation ID. |
| `GRIT_FunctionExecutionLog` | Execution history for every deterministic function, including retries and results. | Function execution ID, scan/execution ID, function name, function version, input hash, output hash, status, attempt, retry budget reference, timestamp, error details. |
| `GRIT_AgentExecutionLog` | Execution history for connected agents and their bounded proposals. | Agent execution ID, scan/execution ID, agent name/version, task, input/evidence reference, response reference, status, confidence, timestamp, Supervisor validation result. |
| `GRIT_OutputPackage` | Immutable versioned draft, approved package, and publication/AIRIS handoff record. | Output package ID, scan/execution ID, version, content reference, content hash, package status, final score, final risk band, created timestamp, supersedes reference. |
| `GRIT_OutputPackageSignal` | Immutable ordered membership snapshot linking a package version to its signal versions. | OutputPackageId, SignalId, SignalVersion, SignalContentHash, MembershipSequence, IncludedAtUtc, InclusionReason. |
| `GRIT_Theme` | Versioned shared strategic theme register. | ThemeId, ThemeName, ThemeStatus, evidence coverage, approved significance, configuration/version lineage, ContentHash. |
| `GRIT_Event` | Versioned shared real-world event register. | EventId, EventTitle, EventStatus, geography, evidence coverage, version lineage, ContentHash. |
| `GRIT_EventSignal` | Immutable relationship from an event to its supporting signal version. | EventId, SignalId, SignalVersion, RelationshipType, MatchMethod, MatchConfidence, IncludedAtUtc, ContentHash. |
| `GRIT_ThemeEvent` | Versioned relationship from a theme to a supporting event. | ThemeId, EventId, RelationshipType, MatchMethod, MatchConfidence, IncludedAtUtc, ContentHash. |
| `GRIT_ImpactAssessment` | Reader-specific assessment of a shared theme, event, or signal. | ImpactAssessmentId, ReaderProfileId, subject lookups, business scope, exposure path, proposals, controlled score/band, status, ContentHash. |
| `GRIT_ReaderProfile` | Governed reader aperture, scope, materiality, and reporting configuration. | ReaderProfileId, ProfileName, ApertureType, business/geography/risk scope, policy versions, template, review queue, Active. |
| `GRIT_ManagementRequest` | Governed leadership monitoring request; the only basis for `P1` news priority. | ManagementRequestId, RequestTitle, RequestDescription, RequestedBy, RequestDate, Priority, Country, Market, LOB, Theme, Status, Active, ReviewDate, Notes. |
| `GRIT_TrackingFeedback` | Post-publication LOB feedback captured as governance input only. | FeedbackId, Theme/Event/Signal lookups, LOB, SubmittedBy, FeedbackDate, FeedbackType, Rationale, RecommendedAction. |
| `GRIT_SourceRegister` | Governed source class, credibility, ownership, and review controls read by `CredibilityLookup()`. | SourceRegisterId, SourceName, SourceDomain, SourceClass, CredibilityRating, SourceOwner, ApprovalAuthority, ReviewFrequency, NextReviewDateUtc, Active. |
| `GRIT_Approval` | Human approval or rejection tied to one exact output package version. | Approval ID, output package ID, approver, approver role, decision, decision timestamp, comments, approved content hash, separation-of-duties result. |
| `GRIT_FunctionCatalog` | Controlled Phase 1 catalog of deterministic services and their contracts. | Function name, function version, purpose, input contract, output contract, enabled status, checksum, retry policy, effective dates, owner. |

### 8.1 Dataverse persistence requirements

- Use alternate keys for scan/execution identifiers and operation idempotency keys where appropriate.
- Use optimistic concurrency and reject stale updates. Reconcile the current `GRIT_WorkflowState` against its append-only transition history.
- Relate function and agent logs, output packages, and approvals to the relevant scan/execution identifier.
- Enable auditing for create, update, delete, and state/status changes. Restrict delete rights. The Supervisor must not delete historical records.
- Store large prompts, responses, and documents in secured Dataverse file columns or an approved linked store, with hashes and references in the Phase 1 tables.
- The Dataverse transaction boundary must cover each state transition and its mandatory `TransitionGuard()` execution evidence. If cross-service atomicity is unavailable, use an outbox/reconciliation pattern and block further workflow progress until the write is confirmed.

## Phase 1 acceptance criteria

- A connected agent cannot change workflow state or publish an output.
- A deterministic function, not an agent, owns each controlled classification and score calculation.
- Every function execution, agent execution, transition, retry, approval, and output version is traceable through the Phase 1 tables.
- Every persisted workflow transition is supported by an auditable `TransitionGuard()` decision of `ALLOW`.
- Theme/event matching, register writes, reader routing, and reporting are recorded as function/agent activity and do not create new signal workflow states.
- The system cannot reach `PUBLICATION_CONFIRMED` without a valid human approval for the exact output hash and verified controlled-publication confirmation.
- The system cannot claim `PUBLICATION_CONFIRMED` based on Supervisor or agent output alone.
- An interrupted execution can be resumed without duplicating actions or losing history.
