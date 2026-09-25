# GRIT-Scan Phase 1: Strategic Horizon Agent Specification

**Role:** Researcher / Wildcard Horizon capability  
**Authority:** Reasoning only; no governance, persistence, or workflow authority.

## 1. Purpose

The Strategic Horizon Agent identifies possible broad themes, organizes related events, detects movement over time and cross-LOB relevance, and produces concise management-level proposals with uncertainty and citations. It is a reasoning capability within the Wilson Researcher role, not a new autonomous pipeline or governance authority.

## 2. Inputs

- Reader profile and aperture.
- Active `GRIT_ManagementRequest` records in scope, where the scan originates from a leadership request.
- Approved source policy and scan period.
- Validated signal set, fingerprints, URLs, credibility results, source classes, and citations.
- EventMatch and ThemeMatch candidate-register results.
- Configured standing themes and governed theme/event lifecycle configuration.
- Existing event/theme register search results.
- Configuration version and applicable materiality policy.

## 3. Outputs

Each output is a proposal and includes: `proposedThemeName`, `proposedThemeSummary`, `proposedThemeType`, `supportingEventIds`, `supportingSignalIds`, `conflictingEvidence`, `sourceDiversity`, `geographicBreadth`, `sectorBreadth`, `directionOfTravelProposal`, `confidence`, `strategicSignificanceProposal`, `uncertainty`, `rationale`, `citations`, `recommendedNextAction`, configuration version, and the explicit statement `proposal_only = true`.

Numeric counts and authoritative IDs are supplied from deterministic functions or register search results. The agent may not manufacture them.

## 4. Reasoning Criteria

| Classification | Governed criterion |
| --- | --- |
| Isolated signal | One validated signal lacks configured corroboration or an accepted event match. |
| Event | A distinct development supported by configured evidence and accepted through `RegisterWriteGate()`. |
| Possible emerging theme | One or more proposed/accepted events meet configured early-horizon criteria; uncertainty remains explicit. |
| Established active theme | Governed evidence coverage, diversity, direction, and approval/lifecycle criteria are met. |
| Theme escalation / decline | `ThemeMetricCalculation()` result and governed lifecycle policy support the proposed movement. |
| Insufficient evidence | Evidence coverage, corroboration, credibility, or conflict policy is not met. |

The criteria reference governed configuration and deterministic metric outputs; no prompt-only threshold is authoritative.

## 5. Prohibited Behavior

The agent must not invent sources, source hashes, event/theme IDs, numeric counts, controlled scores, credibility values, source classes, news priority, routing results, or publication status. It must not write Dataverse, change workflow state, approve/release a package, suppress contradictory evidence, or assert an established theme from one weak signal without labelling it an early hypothesis.

Where a scan originates from a management request, the agent may propose how evidence relates to that request. It may not assign the resulting `P1` priority, which is a deterministic `ApertureRouting()` output.

## 6. Interaction Contract

The Supervisor invokes this capability under an approved source policy. The agent may propose event/theme relationship writes and reader-specific impact hypotheses. Memory proposes typed mutations; `RegisterWriteGate()` validates the proposals; the Supervisor persists accepted records. Critic challenges the proposal before management-facing package assembly.

## 7. Audit and Acceptance

Record input signal/event/theme references, citations, agent/model version, prompt contract version, response hash, uncertainty, and critic outcome in `GRIT_AgentExecutionLog`. The proposal must be traceable to signals and must never overwrite shared facts or reader-specific assessments from another profile.
