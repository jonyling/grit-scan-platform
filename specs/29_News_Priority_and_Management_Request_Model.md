# GRIT-Scan: News Priority and Management Request Model

**Status:** Proposed extension  
**Baseline:** Existing Phase 1 architecture (specs 17–28), unchanged  
**System of record:** Microsoft Dataverse

## 1. Purpose

This specification introduces a governed priority order for surfaced intelligence, and a formal intake path for topics that management explicitly asks GRIT-Scan to monitor.

It extends the existing model. It does not alter the Supervisor, `GRIT_WorkflowState`, `ReviewGate()`, `TransitionGuard()`, the Theme/Event model, Reader Profiles, or theme lifecycle.

## 2. GRIT_NewsPriorityType

A global choice with four values, in strict order:

| Priority | Value | Definition |
| --- | --- | --- |
| **P1** | `MANAGEMENT_REQUEST` | Management or leadership has explicitly requested monitoring of this topic. |
| **P2** | `DIRECT_LOB_IMPACT` | Direct impact on existing LOB operations, products, services, risks, customers, suppliers, or markets. |
| **P3** | `COMPETITOR_INDUSTRY` | Competitor, industry, peer, market, or sector developments. |
| **P4** | `CUSTOMER` | Customer-specific developments. |

### 2.1 Placement — reader-scoped, not a shared fact

`GRIT_NewsPriorityType` is stored on **`GRIT_ImpactAssessment`**. It is never stored on `GRIT_Signals`, `GRIT_Event`, or `GRIT_Theme`.

Priority is a property of *this evidence assessed for this reader*, not of the news itself. The same supply-chain event is `P2` for a directly exposed LOB and `P3` for one watching a competitor. Storing priority on the shared record would let reader appetite overwrite a shared fact, which the aperture model exists to prevent.

Consequences:

- Two reader profiles may hold different priorities for the same signal, simultaneously and legitimately.
- Reprioritising a reader's view never mutates the underlying signal, event, or theme.
- Wide and Material apertures continue to read the same fact base.

### 2.2 Assignment is deterministic

Priority is assigned by `ApertureRouting()` from governed inputs. Agents may propose relevance and rationale; they do not assign priority.

| Priority | Deterministic condition |
| --- | --- |
| `P1` | An active `GRIT_ManagementRequest` matches the subject within the reader's governed scope. |
| `P2` | A governed exposure path exists to the reader's entity, country, customer, supplier, product, programme, technology, annual risk, KRI, or watchpoint. |
| `P3` | Subject matches configured competitor, peer, industry, or sector scope without a direct exposure path. |
| `P4` | Subject matches configured customer scope without a direct exposure path. |

Where more than one condition is satisfied, the **lowest-numbered priority wins**. The routing result records every condition that matched, not only the winner, so the decision is explainable.

## 3. Governance Rules

1. Priority never changes a shared fact. It qualifies a reader-specific assessment only.
2. Priority is a deterministic function output, recorded in `GRIT_FunctionExecutionLog` with input and output hashes.
3. An agent may not set, override, or infer a final priority.
4. A `P1` assignment requires a real, active, in-scope `GRIT_ManagementRequest` record. There is no free-text or asserted P1.
5. Priority carries no approval authority. `ReviewGate()` remains the sole business approval gate.
6. Priority creates no workflow state and triggers no `TransitionGuard()` call.
7. Changing a reader profile or its priority configuration produces new assessments; it never rewrites historical ones.

## 4. Reporting Behaviour

`PackAssembly()` orders management-facing content by priority, ascending: **P1 always appears above P2, P3, and P4**, without exception.

- Ordering is a presentation rule applied at assembly time. It does not mutate stored records.
- Each entry displays its priority and the reason it was assigned.
- A `P1` entry displays the originating Management Request title and requester.
- Priority never overrides lifecycle or approval status labelling. A `P1` proposal is still visibly a proposal.
- An empty priority band is omitted rather than padded.

## 5. Escalation Behaviour

- `P1` items unresolved beyond their configured review date are surfaced under *Themes Requiring Management Attention*.
- Priority is an **input** to `ThemeEscalationAssessment()`, never a substitute for it. A `P1` item does not automatically escalate a theme.
- Escalation continues to require deterministic metrics, `RegisterWriteGate()` validation, and human approval where policy requires it.
- Repeated `P2` assignments across multiple reader profiles are a configured contributor to cross-LOB exposure metrics.

## 6. GRIT_ManagementRequest

The governed intake record for a leadership monitoring request.

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
- Matching a request to a signal or event is recorded as a relational, auditable fact. Assigning the resulting priority remains reader-scoped.

## 7. Intake Flow

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

Within this flow the Strategic Horizon Agent may propose candidate signals, event relationships, theme relationships, and relevance to the request.

It must not approve, publish, create workflow state, create authoritative theme or event identities, assign final priority, or write to Dataverse. All record mutations continue through `RegisterWriteGate()` and Supervisor-controlled persistence.

### 7.2 A management request cannot bypass governance

A `P1` item follows the identical path as any other item: same evidence requirements, same Critic challenge, same package, same `ReviewGate()` approval, same publication control. Priority changes **where it appears**, never **whether it was governed**.

## 8. Audit Requirements

Record in `GRIT_FunctionExecutionLog`: the routing invocation, reader profile and version, matched conditions, assigned priority, matched Management Request reference, configuration version, and input/output hashes.

Retain Management Request status history, activation and closure decisions, and the named requester. Every `P1` entry in a management-facing pack must be traceable to an active request record and to the evidence that satisfied it.

## 9. Acceptance Criteria

- `GRIT_NewsPriorityType` exists only on `GRIT_ImpactAssessment`.
- Two reader profiles can hold different priorities for one shared signal without mutating it.
- `P1` requires an active, in-scope `GRIT_ManagementRequest`.
- Priority is produced by `ApertureRouting()` and is never asserted by an agent.
- `PackAssembly()` places P1 above P2, P3, and P4 in every management-facing pack.
- No new signal workflow state is introduced.
- `ReviewGate()` remains the sole approval gate; `TransitionGuard()` remains signal-transition-only.
