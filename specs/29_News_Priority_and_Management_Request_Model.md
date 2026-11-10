# GRIT-Scan: News Priority and Management Request Model

**Status:** Proposed extension  
**Baseline:** Existing Phase 1 architecture (specs 17–28), unchanged  
**System of record:** Microsoft Dataverse

## 1. Purpose

This specification adds two things to the existing model:

- A governed priority order for surfaced intelligence.
- A formal intake path for topics that management explicitly asks GRIT-Scan to monitor.

The existing model stays in place. This extension does not alter the Supervisor, `GRIT_WorkflowState`, `ReviewGate()`, `TransitionGuard()`, the Theme/Event model, Reader Profiles, or theme lifecycle.

## 2. News priority type

`GRIT_NewsPriorityType` is a global choice with four values, in strict order:

| Priority | Value | Definition |
| --- | --- | --- |
| **P1** | `MANAGEMENT_REQUEST` | Management or leadership has explicitly requested monitoring of this topic. |
| **P2** | `DIRECT_LOB_IMPACT` | Direct impact on existing LOB operations, products, services, risks, customers, suppliers, or markets. |
| **P3** | `COMPETITOR_INDUSTRY` | Competitor, industry, peer, market, or sector developments. |
| **P4** | `CUSTOMER` | Customer-specific developments. |

### 2.1 Placement on the reader-scoped assessment

Priority lives on the reader's impact assessment. The field `GRIT_NewsPriorityType` is stored on **`GRIT_ImpactAssessment`**. It is never stored on `GRIT_Signals`, `GRIT_Event`, or `GRIT_Theme`.

Priority is a property of *this evidence assessed for this reader*. It is not a property of the news itself. The same supply-chain event is `P2` for a directly exposed LOB. It is `P3` for an LOB watching a competitor. If priority sat on the shared record, reader appetite could overwrite a shared fact. The aperture model exists to prevent that.

Consequences:

- Two reader profiles can hold different priorities for the same signal at the same time. This is legitimate.
- Reprioritizing a reader's view never mutates the underlying signal, event, or theme.
- Wide and Material apertures continue to read the same fact base.

### 2.2 Assignment is deterministic

`ApertureRouting()` assigns priority from governed inputs. Agents can propose relevance and rationale. They do not assign priority.

| Priority | Deterministic condition |
| --- | --- |
| `P1` | An active `GRIT_ManagementRequest` matches the subject within the reader's governed scope. |
| `P2` | A governed exposure path exists to the reader's entity, country, customer, supplier, product, program, technology, annual risk, KRI, or watchpoint. |
| `P3` | Subject matches configured competitor, peer, industry, or sector scope without a direct exposure path. |
| `P4` | Subject matches configured customer scope without a direct exposure path. |

When more than one condition is satisfied, the **lowest-numbered priority wins**. The routing result records every condition that matched, including the losing ones. This makes the decision explainable.

## 3. Governance rules

1. Priority never changes a shared fact. It qualifies a reader-specific assessment only.
2. Priority is a deterministic function output, recorded in `GRIT_FunctionExecutionLog` with input and output hashes.
3. An agent may not set, override, or infer a final priority.
4. A `P1` assignment requires a real, active, in-scope `GRIT_ManagementRequest` record. A free-text or asserted `P1` is not allowed.
5. Priority carries no approval authority. `ReviewGate()` remains the sole business approval gate.
6. Priority creates no workflow state and triggers no `TransitionGuard()` call.
7. Changing a reader profile or its priority configuration produces new assessments. It never rewrites historical ones.

## 4. Reporting behavior

**`P1` always appears above `P2`, `P3`, and `P4`**, without exception. `PackAssembly()` orders management-facing content by priority, ascending.

- Ordering is a presentation rule applied at assembly time. It does not mutate stored records.
- Each entry displays its priority and the reason it was assigned.
- A `P1` entry displays the originating Management Request title and requester.
- Priority never overrides lifecycle or approval status labelling. A `P1` proposal is still visibly a proposal.
- `PackAssembly()` omits an empty priority band instead of padding it.

## 5. Escalation behavior

- A `P1` item that stays unresolved past its configured review date appears under *Themes Requiring Management Attention*.
- Priority is an **input** to `ThemeEscalationAssessment()`. It never substitutes for it. A `P1` item does not automatically escalate a theme.
- Escalation continues to require deterministic metrics and `RegisterWriteGate()` validation. It also requires human approval where policy says so.
- Repeated `P2` assignments across multiple reader profiles feed cross-LOB exposure metrics, as a configured contributor.

## 6. Management request record

`GRIT_ManagementRequest` is the governed intake record for a leadership monitoring request.

| Field | Type | Notes |
| --- | --- | --- |
| `ManagementRequestId` | GUID primary key | System generated. |
| `RequestTitle` | Text | Primary name. |
| `RequestDescription` | Multiline text | What is to be monitored and why. |
| `RequestedBy` | Text / user reference | Named requester. Required — anonymous requests cannot create `P1`. |
| `RequestDate` | Date/time, time-zone independent | UTC. |
| `Priority` | Choice → `GRIT_NewsPriorityType` | Normally `P1`; retained explicitly for auditability. |
| `Country` | Text or governed lookup | Optional scope. |
| `Market` | Text or governed lookup | Optional scope. |
| `LOB` | Governed lookup | Optional scope; inherited through governed LOB configuration. |
| `Theme` | Lookup to `GRIT_Theme` | Optional link to an existing theme. |
| `Status` | Choice | `PROPOSED`, `ACTIVE`, `ON_HOLD`, `FULFILLED`, `CLOSED`. |
| `Active` | Yes/No | Only active requests can produce `P1`. |
| `ReviewDate` | Date/time, time-zone independent | Drives the stale-request check. |
| `Notes` | Multiline text | Governance commentary. |

### 6.1 Relationships

- `GRIT_ManagementRequest` → `GRIT_Theme` (optional lookup).
- `GRIT_ImpactAssessment` → `GRIT_ManagementRequest` (optional lookup, populated when priority is `P1`).
- The system records a match between a request and a signal or event as a relational, auditable fact. The resulting priority stays reader-scoped.

## 7. Intake flow

A management request moves through the following governed path:

```text
User enters request
  -> GRIT_ManagementRequest created (PROPOSED)
  -> Governance review sets Status = ACTIVE
  -> Strategic Horizon Agent (proposal only)
  -> Wildcard search under approved source policy
  -> FingerprintArticle / CredibilityLookup
  -> EventMatch()
  -> ThemeMatch()
  -> ApertureRouting() assigns P1 for in-scope readers
  -> Impact assessment
  -> Critic challenge
  -> RegisterWriteGate() for typed writes
  -> Immutable output package
  -> ReviewGate()
  -> TransitionGuard() for signal transitions only
  -> Controlled publication and reporting
```

The request creates a monitoring **instruction**. It does not create evidence, themes, or findings.

### 7.1 Agent boundaries

Within this flow, the Strategic Horizon Agent can propose the following:

- Candidate signals.
- Event relationships and theme relationships.
- Relevance to the request.

The agent must not do any of the following:

- Approve or publish.
- Create workflow state.
- Create authoritative theme or event identities.
- Assign final priority.
- Write to Dataverse.

All record changes continue through `RegisterWriteGate()` and Supervisor-controlled persistence.

### 7.2 A management request cannot bypass governance

A `P1` item follows the same path as any other item. It has the same evidence requirements, Critic challenge, package, `ReviewGate()` approval, and publication control. Priority changes **where it appears**. It never changes **whether it was governed**.

## 8. Audit requirements

Every `P1` entry in a management-facing pack must trace to an active request record. It must also trace to the evidence that satisfied it.

Record the following in `GRIT_FunctionExecutionLog`:

- The routing invocation.
- The reader profile and its version.
- The matched conditions and the assigned priority.
- The matched Management Request reference.
- The configuration version.
- The input and output hashes.

Retain the Management Request status history and its activation and closure decisions. Also retain the named requester.

## 9. Acceptance criteria

- `GRIT_NewsPriorityType` exists only on `GRIT_ImpactAssessment`.
- Two reader profiles can hold different priorities for one shared signal without mutating it.
- `P1` requires an active, in-scope `GRIT_ManagementRequest`.
- Priority is produced by `ApertureRouting()` and is never asserted by an agent.
- `PackAssembly()` places `P1` above `P2`, `P3`, and `P4` in every management-facing pack.
- No new signal workflow state is introduced.
- `ReviewGate()` remains the sole approval gate.
- `TransitionGuard()` remains signal-transition-only.
