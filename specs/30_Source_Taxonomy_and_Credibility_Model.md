# GRIT-Scan: Source Taxonomy and Credibility Model

**Status:** Proposed extension  
**Baseline:** Existing Phase 1 architecture (specs 17–28), unchanged  
**Deterministic function:** `CredibilityLookup()`

## 1. Purpose

This specification classifies approved sources, gives `CredibilityLookup()` a governed data source, and sets the onboarding controls a source must pass before GRIT-Scan will cite it.

It introduces `SourceClass`, the `GRIT_SourceRegister` table, and explicit handling for International SOS. It changes no workflow state, no approval authority, and no existing function contract.

## 2. SourceClass

A global choice describing what kind of source produced a signal.

| Value | Scope |
| --- | --- |
| `OFFICIAL_GOVERNMENT` | Government, regulator, central bank, or official public authority. |
| `OFFICIAL_FINANCE` | Exchange filings, audited financial disclosures, official market notices. |
| `INDUSTRY` | Industry bodies, standards organisations, trade associations. |
| `MEDIA` | News media and wire services. |
| `CUSTOMER` | Customer publications, notices, or disclosures. |
| `COMPETITOR` | Competitor publications, filings, or announcements. |
| `INTERNATIONAL_SOS` | International SOS advisories and alerts. |
| `CONSULTANCY` | Advisory, research, and consultancy output. |
| `INTERNAL` | ST Engineering internal reporting and assurance material. |

`SourceClass` is a property of the **source**, not of the reader. It is a shared fact and is stored on `GRIT_SourceRegister`. Reader profiles may filter on it; they never redefine it.

## 3. GRIT_SourceRegister

The governed register that `CredibilityLookup()` reads. Without it, credibility has no auditable basis.

| Field | Type | Notes |
| --- | --- | --- |
| `SourceRegisterId` | GUID primary key | System generated. |
| `SourceName` | Text | Primary name. |
| `SourceDomain` | Text | Canonical domain or publisher identifier used for matching. |
| `SourceClass` | Choice → `GRIT_SourceClass` | Required. |
| `CredibilityRating` | Choice | `HIGH`, `MEDIUM`, `LOW`, `UNRATED`. |
| `CredibilityRationale` | Multiline text | Why this rating was assigned. |
| `SourceOwner` | Text / user reference | Named accountable owner. |
| `ApprovalAuthority` | Text / user reference | Who approved onboarding. |
| `ApprovedDateUtc` | Date/time, time-zone independent | UTC. |
| `ReviewFrequency` | Choice | `QUARTERLY`, `SEMI_ANNUAL`, `ANNUAL`. |
| `NextReviewDateUtc` | Date/time, time-zone independent | Drives the stale-source check. |
| `IsPublicationAuthority` | Yes/No | Always `No` in Phase 1; reserved and explicit. |
| `Active` | Yes/No | Only active sources may be cited. |
| `ConfigurationVersion` | Text | Required. |
| `Notes` | Multiline text | Governance commentary. |

## 4. CredibilityLookup() Behaviour

`CredibilityLookup()` remains deterministic, performs no reasoning, writes nothing, and approves nothing.

**Inputs:** source domain or publisher identifier, signal reference, configuration version, function version.

**Outputs:** matched source register reference, `SourceClass`, `CredibilityRating`, active status, staleness result, match method, and input/output hashes.

Rules:

1. An unmatched source returns `UNRATED` with no credibility inferred. The Supervisor treats unrated evidence per the active evidence policy; the function never guesses.
2. An inactive source, or one past `NextReviewDateUtc`, is flagged stale. Stale sources are usable only where policy permits, and the staleness is carried into the assessment and the pack.
3. Credibility is never derived from an agent's opinion of the source.
4. Every lookup is recorded in `GRIT_FunctionExecutionLog` with the matched register reference and configuration version.

## 5. International SOS

International SOS is registered with `SourceClass = INTERNATIONAL_SOS` and carries its own credibility rating.

| Rule | Statement |
| --- | --- |
| **Ranking** | Ranks above ordinary `MEDIA` in configured credibility ordering and evidence weighting. |
| **Not government** | Must not be treated as `OFFICIAL_GOVERNMENT`. It is an advisory provider, not an official public authority. |
| **Not a publication authority** | `IsPublicationAuthority = No`. An SOS advisory never authorises publication and never substitutes for `ReviewGate()`. |
| **Auditable** | Every SOS-derived signal retains its source record, retrieval evidence, and citation. |
| **Traceable** | SOS content is cited like any other source; it is never paraphrased into an uncited management assertion. |
| **Governed** | Subject to the same onboarding, review frequency, and staleness controls as any other source. |

Elevated ranking affects evidence weighting and reporting prominence only. It confers no governance authority.

## 6. Source Onboarding Requirements

A source may not be cited until all of the following exist:

1. **Source owner** — a named individual accountable for the source.
2. **Approval authority** — the named approver who onboarded it, with approval date.
3. **Review frequency** — `QUARTERLY`, `SEMI_ANNUAL`, or `ANNUAL`, with `NextReviewDateUtc` set.
4. **Credibility assignment** — a rating with written rationale, assigned through governed configuration, not by an agent.
5. **SourceClass** — assigned from the controlled list.
6. **Active flag** — explicitly set.

Onboarding, rating changes, class changes, and deactivation are typed mutations validated by `RegisterWriteGate()` and persisted by the Supervisor. Prior versions are retained.

## 7. Governance Boundaries

- A source is evidence, never authority. No `SourceClass` value can approve, publish, or advance workflow state.
- `SourceClass` and credibility do not create or modify signal workflow states.
- Agents may report what a source said; they may not assign or alter its class or credibility.
- Reader profiles may include or exclude source classes. This changes what a reader sees, never the shared source record or the underlying signal.

## 8. Acceptance Criteria

- Every cited signal resolves to an active `GRIT_SourceRegister` entry with an owner, approver, review frequency, and rating.
- International SOS is classified `INTERNATIONAL_SOS`, ranks above ordinary media, and holds no publication authority.
- Unmatched sources return `UNRATED` rather than an inferred rating.
- Stale sources are detectable and visibly flagged.
- Source classification introduces no workflow state and no approval path.
