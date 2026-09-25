# GRIT-Scan MVP Build Plan for Wilson Demo

**Purpose:** Define the smallest physical, governed implementation that demonstrates Wilson's Phase 1 architecture end to end.  
**Scope:** Architecture and build plan only. No application code is specified here.

This demo plan deliberately brings forward a thin, seeded Strategic Horizon slice from the longer production roadmap in `27_GRIT_Dataverse_ERD_and_Phase_Plan.md`. It does not reclassify automated event intelligence, theme lifecycle automation, or multi-LOB rollout as Day 1 production capabilities.

## 1. Demo Outcome

Wilson should see one shared evidence chain rendered in two legitimate reader views, then see the human review and controlled state evidence behind it:

```text
One Signal -> One Event -> One Theme
                    |
                    +-> WIDE_APERTURE view: strategic horizon relevance
                    +-> MATERIAL_APERTURE view: LOB exposure path and risk detail
```

The demo must prove that the same signal, event, and theme facts are not copied or altered for different readers. Reader profiles control routing and presentation; `GRIT_ImpactAssessment` stores reader-specific assessment separately.

## 2. What Wilson Can Physically See

The first demo should be a simple model-driven Dataverse app, Power Apps canvas page, or equivalent read-only demo surface with these six screens or panels:

1. **Signal record:** headline, source, URL, publication date, fingerprint, evidence confidence, citations, and current workflow state.
2. **Evidence hierarchy:** the same signal linked to one event, then one theme, with links in both directions.
3. **Wide Aperture view:** Group strategic horizon card, including strategic significance proposal, uncertainty, source diversity, direction of travel, and citations. It retains the signal even where direct LOB impact is weak.
4. **Material Aperture view:** URS/selected LOB card, including governed exposure path, risk classification/rationale, likelihood/impact proposals, final controlled score/band where configured, action/watchpoint, and citations.
5. **Review Package:** immutable package version, package and membership hash, ordered signal membership, reviewer decision, ReviewGate result, and named human approval.
6. **Audit trail:** append-only `GRIT_WorkflowState` events plus function and agent execution log entries. The user can see `TransitionGuard() = ALLOW` associated with every persisted signal transition.

A seventh optional panel may show the two independent post-approval branches: `PUBLICATION_CONFIRMED` and `AIRIS_HANDOFF_READY`. This may use a validated mock AIRIS payload in the first demo.

## 3. Minimum Demo Dataset

Use a small, curated and approved evidence set rather than live crawling:

| Item | Minimum | Demo purpose |
| --- | ---: | --- |
| Shared signals | 1 primary, 1 optional corroborating signal | Proves source evidence and optional diversity. |
| Events | 1 seeded governed event | Proves event-to-signal linkage without building automatic event discovery. |
| Themes | 1 seeded governed theme | Proves theme-to-event linkage and strategic output. |
| Reader profiles | 2 | One `WIDE_APERTURE`; one `MATERIAL_APERTURE` for a selected LOB. |
| Impact assessments | 2 maximum | One Group strategic assessment and one LOB material assessment; neither changes shared facts. |
| Review packages | 1 approved package; 1 optional returned-for-rework package | Proves ReviewGate and package membership. |
| Named reviewer | 1 eligible user | Proves named human commitment. |

The event and theme may be seeded through a `RegisterWriteGate()`-validated, versioned import for the demo. Do not represent the seeded links as agent-created facts or claim that automated matching has been implemented.

## 4. Mandatory Dataverse Tables

### 4.1 Governance core

| Table | Why it is mandatory for Wilson's demo | Complexity |
| --- | --- | --- |
| `GRIT_Signals` | Shared factual source record and current-state projection. | Green |
| `GRIT_WorkflowState` | Append-only signal state ledger and replay proof. | Amber |
| `GRIT_FunctionExecutionLog` | Deterministic function evidence, including ReviewGate and TransitionGuard. | Amber |
| `GRIT_AgentExecutionLog` | Agent proposals and Critic evidence without granting agents authority. | Green |
| `GRIT_OutputPackage` | Immutable review subject, section content, content hash, and package status. | Amber |
| `GRIT_OutputPackageSignal` | Immutable, ordered package membership and exact signal version/hash. | Amber |
| `GRIT_Approval` | Named human approval for one exact package version. | Amber |

### 4.2 Minimum Strategic Horizon extension

| Table | Why it is mandatory for this demo | Complexity |
| --- | --- | --- |
| `GRIT_Event` | Represents the shared real-world development behind the signal. Seed one record. | Green |
| `GRIT_EventSignal` | Makes the event-to-signal evidence link explicit and auditable. | Green |
| `GRIT_Theme` | Represents the shared strategic theme. Seed one governed record. | Green |
| `GRIT_ThemeEvent` | Makes the theme-to-event relationship explicit and auditable. | Green |
| `GRIT_ReaderProfile` | Stores the two governed apertures and selected LOB scope. | Green |
| `GRIT_ImpactAssessment` | Separates Group strategic and LOB material assessments from shared facts. Also carries reader-scoped `NewsPriority`. | Amber |
| `GRIT_ManagementRequest` | Wilson's own ask. Enables the `P1` narrative: leadership requests a topic, and it appears above everything else. | Green |
| `GRIT_TrackingFeedback` | MVP-optional, and cheap. Closes the loop by letting an LOB Head mark a published item TRACK or DO_NOT_TRACK. | Green |

`GRIT_SourceRegister` is **Phase 2**. For the demo, seed two or three sources as configuration rather than building the register.

**Prerequisites:** an execution/run entity for `GRIT_Execution` lookups and the existing `GRIT_FunctionCatalog` with versioned entries. These are required dependencies but are outside the requested core table list.

## 5. Mandatory Functions

| Function | Minimum demo behavior | Complexity |
| --- | --- | --- |
| `FingerprintArticle()` | Generate stable fingerprint/content hash for the seeded signal. | Green |
| `CredibilityLookup()` | Return governed source credibility result. | Green |
| `ScoreRisk()` | Produce final controlled score/band for the Material assessment only. | Amber |
| `EventMatch()` | Return the seeded event as a controlled candidate match; no autonomous event creation. | Green |
| `ThemeMatch()` | Return the seeded theme as a controlled candidate match. | Green |
| `ApertureRouting()` | Produce demonstrably different Wide and Material routes from the same event/signal. | Amber |
| `RegisterWriteGate()` | Validate seeded/typed theme-event-signal and impact-assessment writes. | Amber |
| `ReviewGate()` | Evaluate the package-level named-human review decision. | Amber |
| `TransitionGuard()` | Return `ALLOW` before every persisted signal transition. | Red |
| `PackAssembly()` | Create one small immutable package with named strategic and operational sections. | Amber |

`ThemeMetricCalculation()`, `ThemeEscalationAssessment()`, `KRICalculation()`, `QueueRouting()`, `RetryBudget()`, and `PublicationConcurrencyLock()` are not needed to prove the MVP visual narrative. Keep their catalog entries, but defer live behavior except `RetryBudget()` if the platform requires it for basic execution resilience.

## 6. Mandatory Agents

| Component | Minimum demo responsibility | Complexity |
| --- | --- | --- |
| Supervisor | Orchestrates calls, supplies authoritative inputs, and is the only state/record persistence authority. | Red |
| Researcher / Wildcard | Proposes a signal-to-event/theme interpretation from the seeded evidence. May be replaced by a pre-recorded proposal in the demo if live model access is unavailable. | Amber |
| Assessment Agent | Proposes Group significance and LOB impact; does not calculate the final score/band. | Amber |
| Critic Agent | Challenges the proposed event/theme/impact and supplies the evidence needed for `CRITIQUED`. | Amber |

Memory and Reporting agents are not mandatory. Use deterministic `RegisterWriteGate()` and `PackAssembly()` for the demo. Connected agents must only create auditable proposal records in `GRIT_AgentExecutionLog`.

## 7. Mandatory Strategic Horizon Features

Only these features are mandatory before Wilson's first demo:

1. Shared Theme -> Event -> Signal relationships, with one seeded and versioned chain.
2. Two active reader profiles: one `WIDE_APERTURE` and one `MATERIAL_APERTURE`.
3. Deterministic `ApertureRouting()` producing different inclusion/detail results for the same shared evidence.
4. Two `GRIT_ImpactAssessment` records: Group strategic view and selected LOB material view.
5. Citation-preserving drill-down from theme to event to signal.
6. One immutable output package containing separate Strategic Horizon and Operational Risk sections.
7. Lifecycle status shown as `PROPOSED` or `ACTIVE`; theme escalation metrics are not mandatory for the first demo.
8. One seeded Standing Risk from Wilson's own Annual Report register, so the strategic pack opens on a risk he recognises.
9. One active Management Request producing a `P1` entry above all other content.

## 8. Smallest Demonstrable Implementation

### 8.1 Seeded vertical slice

1. Create an execution/run record and one `GRIT_Signals` record from an approved source.
2. Run `FingerprintArticle()` and `CredibilityLookup()`; log each result.
3. Create the normal signal state evidence: `NEW -> FETCHED -> DEDUPED -> ASSESSED -> CRITIQUED`, each persisted only after `TransitionGuard() = ALLOW`.
4. Seed one versioned `GRIT_Event` and one `GRIT_Theme`, then create `GRIT_EventSignal` and `GRIT_ThemeEvent` through `RegisterWriteGate()`.
5. Create a `WIDE_APERTURE` reader profile and a selected-LOB `MATERIAL_APERTURE` profile.
6. Run `ApertureRouting()` twice over the same evidence population. The wide route creates a strategic summary; the material route requires and displays a governed exposure path.
7. Run the Assessment Agent and Critic Agent. Run `ScoreRisk()` only for the Material assessment.
8. Assemble one package with two independently identifiable sections, then persist ordered membership in `GRIT_OutputPackageSignal`.
9. Transition the signal to `PENDING_REVIEW` only after `TransitionGuard() = ALLOW`.
10. Submit a named reviewer decision, run `ReviewGate()`, create `GRIT_Approval`, and transition the signal to `APPROVED` only after `TransitionGuard() = ALLOW`.
11. Demonstrate one mock or real controlled AIRIS payload to `AIRIS_HANDOFF_READY`; optional controlled publication confirmation may follow independently.

### 8.2 Required difference between apertures

Use an evidence item whose Group relevance exceeds direct LOB materiality. The outcome must visibly differ:

| Shared fact | Wide Aperture output | Material Aperture output |
| --- | --- | --- |
| Same signal/event/theme | Displays a strategic horizon summary, uncertainty, cross-sector/geography context, and reason to monitor. | Displays a selected-LOB exposure path, risk/action detail only when the governed materiality criteria are met; otherwise shows a reasoned suppression/no-material-impact result. |

The demo fails its personalisation objective if both profiles render the same content at the same detail level.

## 9. Do Not Build Before the First Wilson Demo

- Live broad-web crawling, news ingestion, or high-volume source connectors.
- Automatic event clustering, theme clustering, merge, split, or deduplication beyond the one controlled seeded chain.
- Theme lifecycle escalation calculation, trend analytics, or automatic promotion/closure.
- Multi-LOB rollout, complex row-level security matrices, or full `GRIT_LOBProfiles` migration.
- Production AIRIS delivery, report distribution, dashboard publishing, export orchestration, or concurrent-publication handling.
- Advanced report design, executive dashboard visualisation, natural-language query, or alerting.
- Full remediation workflow, exception console, automated retries, or historical-data migration.
- Autonomous agent writes, autonomous publication, or any shortcut around ReviewGate/TransitionGuard.

## 10. 30-Minute Wilson Demo Script

| Time | Presenter action | What Wilson should conclude |
| --- | --- | --- |
| 0:00-2:00 | State the governing rule: Dataverse holds the record; agents propose; deterministic functions decide controlled operations; Supervisor persists. | This is governed intelligence, not a loose multi-agent workflow. |
| 2:00-6:00 | Open the shared signal. Show source URL/date, fingerprint, credibility, content hash, and citations. | There is one traceable source fact. |
| 6:00-9:00 | Open the linked event and then the linked theme. Show `GRIT_EventSignal` and `GRIT_ThemeEvent` relationships. | The event and theme are not duplicate articles; they are governed shared records linked to evidence. |
| 9:00-13:00 | Select the `WIDE_APERTURE` profile. Show the strategic section: theme summary, uncertainty, strategic significance proposal, and citations. | Weak or early evidence can be retained for Group awareness without being overstated. |
| 13:00-17:00 | Select the `MATERIAL_APERTURE` profile for the chosen LOB. Show the same signal/event/theme, then the separate impact assessment: exposure path, classification proposal, controlled score/band, action/watchpoint. | Materiality adds operational relevance without changing shared facts. |
| 17:00-19:00 | Switch back to the source/event record and show it has not changed after either reader view. | Reader appetite changes what is surfaced, not what happened. |
| 19:00-23:00 | Open the agent/function logs. Show Researcher/Assessment/Critic proposals, `ScoreRisk()` result, and `ApertureRouting()` decisions. | Agents reason; deterministic functions own controlled outputs. |
| 23:00-26:00 | Open the immutable Review Package, membership list/hash, then submit the named reviewer approval. Show `ReviewGate()` result and exact `GRIT_Approval` record. | Approval is package-level, named, hash-bound, and auditable. |
| 26:00-28:00 | Open the signal workflow ledger. Show `TransitionGuard() = ALLOW` evidence for each persisted transition through `APPROVED`. | The Supervisor is the only state writer and transitions are replayable. |
| 28:00-30:00 | Show independent `AIRIS_HANDOFF_READY` and optional `PUBLICATION_CONFIRMED` records. Close by showing citations from report section back to signal. | Downstream consumption and publication are controlled branches, not agent actions. |

## 11. Complexity Rating

| Feature | Rating | Reason |
| --- | --- | --- |
| Seeded signal ingestion and fingerprint | Green | Single controlled source record and deterministic hash. |
| Source credibility lookup | Green | Seeded governed source-policy lookup. |
| Signal workflow ledger | Amber | Append-only rows, alternate keys, and projection rebuild are required. |
| TransitionGuard integration | Red | Requires exact graph/evidence/sequence/idempotency enforcement before every transition. |
| Agent execution audit log | Green | Bounded proposal capture with hashes/citations. |
| Assessment and Critic responses | Amber | Requires reliable contracts and clear separation from controlled scoring. |
| ScoreRisk for one Material assessment | Amber | Controlled configuration and score provenance required. |
| Seeded Event/EventSignal | Green | Small versioned record and explicit relationship; no live matching automation. |
| Seeded Theme/ThemeEvent | Green | Small versioned record and explicit relationship; no lifecycle automation. |
| Reader profiles | Green | Two governed records with fixed scopes. |
| ApertureRouting | Amber | Must visibly differ by profile without changing facts. |
| Impact assessment | Amber | Must preserve reader-specific scope and controlled score provenance. |
| RegisterWriteGate | Amber | Typed-write validation and version/hash checks are required. |
| Immutable package and package membership | Amber | Version, content hash, membership hash, and ordered relationship required. |
| ReviewGate and named approval | Amber | Exact package/hash and reviewer eligibility checks required. |
| AIRIS-ready mock branch | Green | Validated mock payload and transition evidence only. |
| Publication confirmation branch | Amber | Must prove named human commitment and verified delivery. |
| Full PackAssembly and report styling | Amber | A small two-section package is sufficient; polished template work can wait. |
| Automated event matching | Amber | Candidate matching is manageable, but autonomous creation/merge is deferred. |
| Theme lifecycle escalation | Red | Requires governed thresholds, evidence diversity, lifecycle policy, and human escalation controls. |
| Live multi-source ingestion | Red | Source policy, reliability, licensing, retries, and volume introduce substantial risk. |
| Multi-LOB security and production rollout | Red | Requires governed scope, row-level security, performance, and operating controls. |

## 12. Biggest Implementation Risks

1. **Governance erosion:** developers may let a flow or agent update state directly. Mitigation: make the Supervisor service identity the only writer and require logged `TransitionGuard() = ALLOW` evidence.
2. **Fake personalisation:** two apertures may render the same output. Mitigation: use a scenario with Group strategic relevance and conditional/no LOB materiality, then show the distinct routing outputs.
3. **Package integrity gaps:** missing immutable membership or hash checks undermines approval. Mitigation: implement `GRIT_OutputPackageSignal`, package/membership hashes, and ReviewGate before human review is shown.
4. **Theme/event treated as agent facts:** a proposed link may look authoritative. Mitigation: seed one visibly governed chain and label all generated proposals; gate future writes through RegisterWriteGate.
5. **Over-building Strategic Horizon:** automated clustering and lifecycle analytics can consume the demo schedule. Mitigation: seed one versioned event/theme and defer automation.
6. **Weak audit visibility:** controls may exist but not be visible. Mitigation: make FunctionExecutionLog, AgentExecutionLog, approval record, and WorkflowState a dedicated demo panel.
7. **Inconsistent master data:** LOB/entity/risk configuration can drift. Mitigation: use one selected LOB and a fixed governed profile for the demo; defer broad master-data migration.

## 12.1 Platform constraint: no inbound Request-triggered flows

The tenant's Data Loss Prevention policy (`GHQ R&A - Data Policy`) blocks the `HttpRequestReceived` API outright, for every trigger kind that uses it (`Http` and `Button`/child-flow both confirmed blocked). This is independent of Power Automate licensing and cannot be resolved by purchasing Premium.

**Consequence:** no flow in this environment may be invoked on demand by an external caller - not as a Power Automate child flow, not from Power Apps, and not as a Copilot Studio agent action. This rules out the standard "child flow" pattern for `TransitionGuard()` and any comparable deterministic function.

**Design rule going forward:**

- Every flow must start from a **Dataverse trigger** (row created/updated) or a **Recurrence/schedule trigger**. Never a Request/child-flow trigger.
- Flows may make **outbound** connector calls (Dataverse writes, calling a Copilot Studio agent) freely - the restriction is on inbound invocation, not outbound action.
- Deterministic pure functions such as `TransitionGuard()` are implemented as an inline `Scope` of actions inside the calling flow (the Supervisor), not as a separately callable flow. The logic, evidence rules, and test coverage remain identical; only the packaging changes.
- Any human-in-the-loop step (e.g. `ReviewGate()` approval) must be implemented as a write to a Dataverse table (e.g. `GRIT_Approval`) that a Dataverse trigger picks up, never as a direct on-demand flow call from a button or card.
- The Supervisor flow itself is triggered by a new row in `GRIT_Execution` (a scan/execution request), not by an external caller.

## 13. MVP Exit Criteria

The Wilson demo is ready when all of the following are observable in Dataverse and the demo surface:

- One shared signal is linked to one event and one theme with citations and versioned relationship records.
- `WIDE_APERTURE` and `MATERIAL_APERTURE` return visibly different, policy-explainable views of the same shared evidence.
- The Material view has a separate `GRIT_ImpactAssessment` and any final score/band comes from `ScoreRisk()`.
- Critic evidence exists before `CRITIQUED`.
- The package is immutable, hash-bound, and has ordered signal membership.
- A named human approval passes through `ReviewGate()`.
- Every persisted signal transition has a logged `TransitionGuard() = ALLOW` result.
- `REJECT` and `HOLD` are demonstrably package-only and create no signal transition.
- No agent publishes, approves, writes workflow state, or changes shared evidence.
