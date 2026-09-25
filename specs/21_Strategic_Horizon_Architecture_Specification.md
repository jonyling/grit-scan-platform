# GRIT-Scan Phase 1: Strategic Horizon Architecture Specification

**Status:** Proposed  
**Authority:** Wilson-based GRIT-Scan architecture  
**System of record:** Microsoft Dataverse

## 1. Purpose and Scope

Strategic Horizon extends GRIT-Scan Phase 1 from individual, auditable signals to a governed intelligence model that connects shared external facts to both Group-level horizon awareness and LOB-level operational materiality.

It introduces four layers:

```text
Strategic Theme -> Event -> Signal -> LOB Impact Assessment
```

A Strategic Theme is a broad, persistent or emerging external development. An Event is a distinct development or continuing incident. A Signal is an individual source item. A LOB Impact Assessment is a reader-specific assessment of possible effect on governed business context.

The model supports multiple active LOBs without producing separate factual realities. Themes, events, and signals are shared facts. Reader profiles and impact assessments determine what is surfaced and how it is explained to a reader; they do not overwrite shared facts.

## 2. Boundaries

This specification does not add signal workflow states. The existing controlled signal workflow remains:

```text
NEW -> FETCHED -> DEDUPED -> ASSESSED -> CRITIQUED -> PENDING_REVIEW -> APPROVED
APPROVED -> PUBLICATION_CONFIRMED
APPROVED -> AIRIS_HANDOFF_READY
```

`PENDING_REVIEW -> ASSESSED` is permitted only for package-wide `ReviewGate() = RETURN_FOR_REWORK`. `REJECT` and `HOLD` are package-only outcomes and create no signal transition. Every actual signal transition requires `TransitionGuard() = ALLOW` before the Supervisor persists it.

Theme discovery, event matching, theme matching, clustering, reader routing, and impact assessment are processing activities recorded in proposal, relationship, function, and agent execution records. They are not workflow states.

## 3. Use Cases

### 3.1 Group strategic horizon

A `WIDE_APERTURE` reader receives cross-sector and cross-geography awareness, including early or weak signals where evidence and uncertainty are visible. The strategic use case identifies evolving themes, changes in direction, cross-LOB relevance, contradictory evidence, and matters requiring human attention.

### 3.2 LOB operational intelligence

A `MATERIAL_APERTURE` reader receives detailed assessment only where a governed exposure path or defensible material relevance exists. It uses entity, country, customer, supplier, product, programme, technology, annual-risk, KRI, watchpoint, and management-focus configuration without deleting weak shared signals from the Group view.

## 4. Processing Sequence

```text
Approved source policy
  -> Management Request intake, where a leadership request initiates the scan
  -> Researcher / Wildcard discovery
  -> FingerprintArticle and CredibilityLookup
  -> EventMatch or proposed event
  -> ThemeMatch or proposed theme
  -> ApertureRouting, including deterministic news-priority assignment
  -> Group strategic assessment and/or LOB impact assessment
  -> Critic challenge
  -> ThemeMetricCalculation and ThemeEscalationAssessment where a theme lifecycle proposal is required
  -> RegisterWriteGate for typed versioned writes
  -> Immutable output package
  -> ReviewGate
  -> TransitionGuard for actual signal transitions
  -> Controlled publication and reporting branches
  -> Post-publication LOB tracking feedback, as governance input to the next cycle
```

The stages are distinct:

- Discovery finds a source signal.
- Event matching compares that signal with the governed event register.
- A new event is a proposal, not an authoritative identity.
- Theme matching compares an event with the governed theme register.
- A new theme is a proposal, not a published Group fact.
- Assessment proposes strategic significance or operational materiality.
- ReviewGate approves an immutable package; it does not approve a theme/event record outside the package policy.
- Controlled publication integration records publication confirmation only after a named human commitment and verified delivery.

## 5. Wilson Role Mapping

| Role | Strategic Horizon responsibility | Prohibited responsibility |
| --- | --- | --- |
| Supervisor | Select reader profile/path, invoke routing/register functions, coordinate evidence, persist only accepted signal transitions. | Inventing themes, controlled scores, approvals, or publication decisions through reasoning. |
| Researcher / Wildcard capability | Search approved sources and propose signals, event relationships, and theme relationships. | Inventing sources, hashes, IDs, publication status, credibility, or routing results. |
| Assessment | Propose Group significance and reader-specific LOB impact. | Overwriting shared facts or calculating controlled scores. |
| Critic | Challenge theme scope, event boundaries, corroboration, duplicate claims, and impact paths; use deterministic checks. | Approving, publishing, or changing workflow state. |
| Memory | Propose typed theme/event/relationship/assessment writes. | Writing directly to Dataverse. |
| Reporting | Assemble strategic and operational views with citations and drill-down. | Representing a draft/proposal as a published fact. |

## 6. Deterministic and Reasoning Responsibilities

| Operation | Authority |
| --- | --- |
| Semantic discovery, proposed event/theme relationships, significance and impact narrative | Agents reason. |
| Fingerprinting, credibility, scoring, KRI calculation, event/theme register comparison, routing, theme metrics/escalation assessment, typed-write validation, pack assembly, review, transition validation, retry and technical concurrency | Deterministic functions execute. |
| Dataverse record and state persistence | Supervisor only, after applicable deterministic gates allow it. |
| Approval | Named human through ReviewGate package flow. |
| Publication confirmation | Controlled integration, after human commitment; never agent assertion. |

## 7. Governance and Human Interrupt

All record mutations are typed, validated, versioned, audited, and retained. `RegisterWriteGate()` validates theme/event/relationship/impact proposals; it is not a workflow-state writer. Human review is required where the active policy requires promotion, escalation, merge, split, closure, or management-facing package approval.

Draft, proposed, held, rejected, and returned content must retain visible status. Reporting may treat only policy-permitted approved and publication-confirmed records as published facts. Every management-facing assertion must retain citations to supporting events and signals.

## 8. Outputs

A single immutable output package may contain independently identifiable Strategic Horizon and Operational Risk sections when both derive from the same approved evidence population. Separate packages are permitted where audience, approval policy, or evidence population differs.

- Strategic Horizon Pack: Group themes, events, movement, uncertainty, cross-LOB relevance, and drill-down.
- Operational Risk Pack: audited signal and LOB impact detail, controlled ratings, actions, and sign-off.

## 9. Failure and Reconciliation

Missing references, conflicting hashes, unverified source/evidence, register-write validation failure, stale version, or cross-table persistence uncertainty fails closed. The Supervisor logs the failure, stops the affected action, and reconciles before retry or reprocessing. Replays use retained versioned records, relationship snapshots, configuration versions, input/output hashes, and execution logs.

## 10. Audit Requirements

Every candidate relationship, deterministic match, routing result, proposal, critic challenge, register write, package inclusion, review decision, transition guard result, and publication/AIRIS handoff is correlated to a Dataverse execution and configuration version. Shared facts and reader-specific assessments must remain traceable to the signals and evidence from which they were derived.

## 11. Acceptance Criteria

- A shared signal or event can appear in both wide and material views without duplication or mutation of shared facts.
- Different apertures can produce different outputs from the same evidence population.
- A management request changes priority and prominence only; it never bypasses evidence, critique, review, or publication control.
- Post-publication LOB feedback is governance input and never modifies a signal, event, or theme directly.
- Themes/events remain proposals until validated, versioned, and approved where policy requires.
- No theme/event processing activity becomes a signal workflow state.
- Every actual signal transition retains `TransitionGuard() = ALLOW` evidence.
- ReviewGate remains the sole business approval gate; PublicationConcurrencyLock is technical only.
- A named human commitment and verified controlled publication are required before reporting calls content published.
