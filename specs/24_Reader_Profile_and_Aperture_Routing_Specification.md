# GRIT-Scan Phase 1: Reader Profile and Aperture Routing Specification

**Function:** `ApertureRouting()`  
**Type:** Deterministic routing function  
**System of record:** Dataverse `GRIT_ReaderProfile`

**Weekly extension:** [Spec 45: Weekly agent reasoning and critique](45_Weekly_Agent_Reasoning_and_Critique.md) adds
versioned editorial inclusion before this function. Routing and priority remain
deterministic. Excluded candidates, including P1 and RED records, remain
available in the review audit. Exclusion does not cancel a RED alert draft.

## 1. Purpose

`ApertureRouting()` selects the required assessment and reporting routes from a governed Reader Profile and configuration. It changes what a reader sees. It does not change shared theme, event, or signal facts. The same validated evidence population can therefore generate different Group and LOB views without modifying source facts.

The Supervisor selects the Reader Profile and invokes `ApertureRouting()`. The Supervisor logs the deterministic result and persists only permitted typed writes. The function does no reasoning and does not write state. It does not approve or publish.

## 2. Apertures

| Aperture | Purpose | Routing rule |
| --- | --- | --- |
| `WIDE_APERTURE` | Group horizon scan and strategic management awareness. | Retain governed weak/early signals across sector and geography, labeled with evidence confidence and uncertainty, even without demonstrated direct LOB impact. |
| `MATERIAL_APERTURE` | BA/LOB/BU operational assessment. | Require a defensible governed exposure path or material relevance to business context before an operational impact assessment/report route is selected. |

`MATERIAL_APERTURE` can suppress a reader view for insufficient materiality. It must not delete or modify the shared signal/event/theme. A routing test must demonstrate different output for at least one governed profile pair. Without that difference, personalization is not demonstrated.

## 3. Inputs and outputs

Inputs: `reader_profile_id`, aperture type, active flag, business/geography/risk-theme scopes, source-policy version, weak-signal tolerance, materiality-policy version, reporting template, review queue, governed business context, active management-request references in scope, subject references, evidence confidence, source class, and configuration version.

Outputs: selected aperture, assessment route, report section route, review queue, inclusion decision, suppression reason where not included, assigned `NewsPriority`, every priority condition that matched, matched management-request reference where applicable, required evidence policy, and deterministic input/output hashes. Results contain no generated timestamp or random identifier.

### 3.1 News priority assignment

`ApertureRouting()` assigns `GRIT_NewsPriorityType` deterministically. It writes the priority to the reader-scoped `GRIT_ImpactAssessment`, never to a shared record.

| Priority | Condition |
| --- | --- |
| `P1` | An active, in-scope `GRIT_ManagementRequest` matches the subject. |
| `P2` | A governed exposure path to the reader's business context exists. |
| `P3` | Competitor, peer, industry, or sector scope matches without a direct exposure path. |
| `P4` | Customer scope matches without a direct exposure path. |

Where several conditions match, the lowest-numbered priority wins. The function records all matched conditions so the outcome is explainable. Agents can propose relevance. Only this function assigns priority. Spec 29 has the full rules.

## 4. Materiality and shared-fact rules

A Material route requires a configured exposure path to an entity, country, customer, supplier, product, programme, technology, annual risk, KRI, watchpoint, or management focus topic. The resulting `GRIT_ImpactAssessment` is reader-specific. It can contain proposed likelihood, impact, action, KRI, and watchpoint. Final controlled score/band remains the result of `ScoreRisk()`.

Wide routes can create a strategic significance proposal. They cannot convert it into an approved fact or published record. Both apertures preserve citations, contradictory evidence, and the original source signal.

## 5. Governance and audit

R&A centrally governs reader profiles. LOB/BA/BU master data is inherited through `GRIT_LOBProfiles` and governed relationships. Log every routing decision in `GRIT_FunctionExecutionLog` with profile/configuration versions, subject references, input/output hashes, and suppression/inclusion reason. Agents can propose relevance. Only deterministic routing selects the route.
