# GRIT-Scan Phase 1: Theme Lifecycle and Escalation Specification

**Function:** `ThemeEscalationAssessment()`  
**Function type:** Pure deterministic theme-metric evaluation service  
**System of record:** Microsoft Dataverse  
**Scope:** `GRIT_Theme` lifecycle only; not a signal workflow mechanism

## 1. Purpose

This specification formalises how a strategic theme evolves as governed event evidence changes over time. It separates an agent's interpretation of why a theme matters from deterministic measurement of whether its evidence footprint is expanding, stable, or declining.

`ThemeEscalationAssessment()` evaluates a canonical, Supervisor-supplied metric snapshot for one theme version. It returns direction, velocity, escalation recommendation, and a lifecycle-state proposal. It performs no Dataverse reads or writes, invokes no agent, creates no theme/event relationship, does not approve or publish, and does not change `GRIT_WorkflowState`.

```text
Supervisor supplies authoritative metrics.
ThemeEscalationAssessment evaluates.
RegisterWriteGate validates typed theme mutation.
Supervisor persists accepted versioned record.
```

## 2. Theme Lifecycle States

| State | Meaning | Permitted lifecycle movement |
| --- | --- | --- |
| `PROPOSED` | Candidate theme supported by a proposal; not an approved shared fact. | `ACTIVE`, `CLOSED`, or remain `PROPOSED` after governed review. |
| `ACTIVE` | Theme has sufficient governed evidence and is being actively monitored. | `ESCALATING`, `STABLE`, `DECLINING`, or `CLOSED`. |
| `ESCALATING` | Deterministic metrics show increasing relevance or evidence footprint according to active configuration. | `STABLE`, `DECLINING`, or remain `ESCALATING`. |
| `STABLE` | Evidence remains materially consistent within governed movement thresholds. | `ESCALATING`, `DECLINING`, or remain `STABLE`. |
| `DECLINING` | Deterministic metrics show decreasing relevance, activity, or evidence footprint according to active configuration. | `STABLE`, `ESCALATING`, or `CLOSED`. |
| `CLOSED` | Theme is no longer actively monitored under the current lifecycle policy; historical versions remain available. | `REOPENED` only. |
| `REOPENED` | Previously closed theme has new governed evidence requiring renewed assessment. | `ACTIVE`, `ESCALATING`, `STABLE`, `DECLINING`, or `CLOSED`. |

`PROPOSED` and `REOPENED` describe lifecycle status, not publication status. A lifecycle transition is a versioned `GRIT_Theme` mutation; it is not a `GRIT_WorkflowState` transition.

## 3. ThemeEscalationAssessment() Functional Specification

### 3.1 Deterministic contract

The same canonical metric snapshot and configuration version must always produce the same result. Thresholds, weighting, minimum evidence requirements, and recommendation mapping are defined in governed configuration, never in an agent prompt.

The Supervisor obtains metrics from `ThemeMetricCalculation()`, validates the configuration version, and supplies them to this function. `ThemeEscalationAssessment()` must not retrieve a newer theme row, calculate missing values, infer evidence, or replace configured thresholds.

### 3.2 Inputs

| Input | Required | Description |
| --- | --- | --- |
| `theme_id` | Yes | Stable theme identity. |
| `theme_version` | Yes | Theme version evaluated. |
| `theme_type` | Yes | Governed `ThemeType`, so standing-risk rules apply correctly. See spec 31. |
| `current_lifecycle_state` | Yes | Authoritative current lifecycle state. |
| `supporting_event_count` | Yes | Controlled count of accepted supporting events. |
| `event_growth_rate` | Yes | Controlled change rate over the configured comparison period. |
| `standing_risk_activity` | Conditional | Controlled activity metric; required when `theme_type = STANDING_RISK`. |
| `affected_lob_count` | Yes | Controlled count of governed affected LOBs. |
| `geography_count` | Yes | Controlled count of governed geographies. |
| `source_diversity` | Yes | Controlled diversity metric, not an agent estimate. |
| `regulatory_activity` | Yes | Controlled regulatory-activity metric or governed ordinal. |
| `metric_period_start_utc` | Yes | Start of the comparison period. |
| `metric_period_end_utc` | Yes | End of the comparison period. |
| `configuration_version` | Yes | Exact lifecycle threshold/weight configuration. |
| `theme_metric_calculation_version` | Yes | Version that produced the supplied metrics. |
| `function_version` | Yes | `ThemeEscalationAssessment()` version. |
| `idempotency_key` | Yes | Stable evaluation identity. |

Invocation ID, runtime timestamps, retry attempt, signature, signing key, and execution-log identifiers are invocation metadata. They are excluded from canonical decision inputs and cannot change a deterministic result.

### 3.3 Outputs

| Output | Description |
| --- | --- |
| `direction_of_travel` | `INCREASING`, `STABLE`, `DECREASING`, or `INSUFFICIENT_EVIDENCE`. |
| `velocity` | Governed band such as `HIGH`, `MODERATE`, `LOW`, or `NONE`. |
| `escalation_recommendation` | `ESCALATE`, `MAINTAIN_ACTIVE_MONITORING`, `DE_ESCALATE`, `CLOSE_CANDIDATE`, `REOPEN_CANDIDATE`, or `INSUFFICIENT_EVIDENCE`. |
| `lifecycle_state_proposal` | One lifecycle state from Section 2. |
| `reason_code` / `reason_summary` | Stable configured explanation of the result. |
| `metric_snapshot_hash` | Hash of canonical supplied metrics and period. |
| `input_hash` / `canonical_result_hash` | Deterministic integrity hashes. |
| `theme_id`, `theme_version`, `configuration_version`, `function_version` | Traceability values. |

The result is a proposal until `RegisterWriteGate()` validates the typed lifecycle mutation and the Supervisor persists a new version. Policy may require named human approval for escalation, closure, or reopening before the version becomes active for management reporting.

## 4. Decision Rules

1. Validate the complete metric contract, numeric ranges, period ordering, configuration version, and allowed current lifecycle state. Invalid or missing input returns `INSUFFICIENT_EVIDENCE` with no lifecycle promotion.
2. Apply the configured minimum evidence gate. A theme without sufficient event coverage or source diversity cannot receive `ESCALATE`, even where the raw growth rate is high.
3. Calculate the controlled movement score from event growth, affected LOB breadth, geography breadth, source diversity, and regulatory activity using the weights in the exact configuration version.
4. Map the movement score and current state to direction, velocity, recommendation, and lifecycle proposal using configured thresholds.
5. A closed theme may only receive `REOPEN_CANDIDATE`/`REOPENED` when configured reopening evidence is satisfied. A theme cannot be silently reopened because an agent text suggests renewed relevance.
6. A proposed theme may become `ACTIVE` only when configured evidence and lifecycle promotion policy are satisfied.
7. A `STANDING_RISK` never returns `CLOSE_CANDIDATE`. Sustained decline returns `DE_ESCALATE` with a `DECLINING` proposal; closure requires Annual Report governance. See spec 31.
8. `ESCALATE` tracking feedback from an LOB is a configured input contributor only. It cannot by itself escalate a theme, and never bypasses the evidence gates or required approval.
9. The function does not publish a theme, alter a package, or infer that a lifecycle proposal is approved.

## 5. Lifecycle Actions and Governance

| Action | Agent role | Deterministic control | Persistence rule |
| --- | --- | --- | --- |
| Proposal | May propose a theme and rationale. | EventMatch, ThemeMatch, ThemeMetricCalculation, RegisterWriteGate. | Persist a versioned `PROPOSED` record only after validated typed write. |
| Activation | May propose strategic significance. | ThemeEscalationAssessment and lifecycle policy. | Supervisor writes new version; human approval where policy requires. |
| Escalation | May explain drivers and uncertainty. | ThemeEscalationAssessment returns `ESCALATE`; RegisterWriteGate validates lineage/policy. | New `ESCALATING` version; human approval where policy requires. |
| Stabilisation / decline | May explain observed movement. | ThemeEscalationAssessment deterministic result. | New version; retain prior version unchanged. |
| Closure | May recommend closure. | ThemeEscalationAssessment returns `CLOSE_CANDIDATE`; RegisterWriteGate confirms policy and evidence. | New `CLOSED` version; human approval where policy requires. |
| Reopening | May propose renewed monitoring. | ThemeEscalationAssessment returns `REOPEN_CANDIDATE`; RegisterWriteGate validates closed lineage and evidence. | New `REOPENED` version; human approval where policy requires. |
| Merge / split / correction | May propose. | RegisterWriteGate validates references, hashes, policy, and version lineage. | Append-only/superseding records; never merge solely on title similarity. |

## 6. Audit, Replay, and Error Handling

Log every call in `GRIT_FunctionExecutionLog`: function/version, theme/version references, supplied metrics, configuration/metric-function versions, canonical input/result hashes, idempotency key, invocation metadata, and result signature. Store source event and signal references in the metric snapshot or secured linked payload so results are replayable.

`RegisterWriteGate()` logs the typed mutation decision separately. The resulting `GRIT_Theme` version stores lifecycle state, direction, content hash, configuration version, source execution, and version lineage.

| Condition | Required response |
| --- | --- |
| Missing/invalid metric or configuration | `INSUFFICIENT_EVIDENCE`; do not promote/escalate lifecycle. |
| Unknown current lifecycle state | Fail closed; no write instruction. |
| Metric period mismatch or stale source version | Fail closed; recompute authoritative snapshot. |
| Duplicate idempotency key, identical input | Return original deterministic result. |
| Duplicate idempotency key, different input | Fail closed; log idempotency conflict and reconcile. |
| Function timeout/throttling | Retry through `RetryBudget()`; do not synthesize a result. |
| RegisterWriteGate denial/persistence failure | Preserve function result, stop mutation, reconcile before retry. |

## 7. Reporting Requirements

The Strategic Horizon Pack must include independently identifiable sections for:

1. **Top Emerging Themes:** `PROPOSED`, newly `ACTIVE`, and policy-permitted early themes, visibly labelled with evidence confidence and uncertainty.
2. **Fastest Escalating Themes:** `ESCALATING` themes ordered by governed velocity/movement result, not article count alone.
3. **Themes Requiring Management Attention:** policy-configured combination of escalation recommendation, strategic significance, cross-LOB exposure, regulatory activity, and evidence confidence.
4. **Declining Themes:** `DECLINING` themes with direction and rationale.
5. **Closed Themes:** `CLOSED` themes, closure rationale, date/version, and reopening watchpoint where applicable.

Reports may state lifecycle results as facts only when the relevant theme version and output package have the approval/publication status required by policy. Proposed results remain visibly labelled and retain citations to events and signals.

## 8. Acceptance Criteria

- Theme lifecycle values are limited to the seven controlled states in Section 2.
- No theme lifecycle action creates a new `GRIT_WorkflowState` value or signal transition.
- Identical canonical metric input and configuration produces the same result hashes and lifecycle proposal.
- The function performs no reads, writes, approvals, publication, or agent invocations.
- A high event count alone cannot escalate a theme where configured diversity/evidence gates fail.
- Escalation, decline, closure, and reopening retain immutable version lineage and evidence traceability.
- Strategic reports surface emerging, escalating, declining, closed, and management-attention themes with status and citations.
