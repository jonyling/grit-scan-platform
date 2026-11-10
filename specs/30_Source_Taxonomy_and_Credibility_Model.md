# GRIT-Scan: Source Taxonomy and Credibility Model

**Status:** Proposed extension  
**Baseline:** Existing Phase 1 architecture (specs 17–28), unchanged  
**Deterministic function:** `CredibilityLookup()`

## 1. Purpose

This specification classifies approved sources. It gives `CredibilityLookup()` a governed data source. It also sets the onboarding controls that a source must pass before GRIT-Scan cites it.

The specification introduces the following:

- `SourceClass`, a classification of each source.
- The `GRIT_SourceRegister` table.
- Explicit handling for International SOS.

It changes no workflow state, no approval authority, and no existing function contract.

## 2. Source class

`SourceClass` is a global choice that describes what kind of source produced a signal.

| Value | Scope |
| --- | --- |
| `OFFICIAL_GOVERNMENT` | Government, regulator, central bank, or official public authority. |
| `OFFICIAL_FINANCE` | Exchange filings, audited financial disclosures, official market notices. |
| `INDUSTRY` | Industry bodies, standards organizations, trade associations. |
| `MEDIA` | News media and wire services. |
| `CUSTOMER` | Customer publications, notices, or disclosures. |
| `COMPETITOR` | Competitor publications, filings, or announcements. |
| `INTERNATIONAL_SOS` | International SOS advisories and alerts. |
| `CONSULTANCY` | Advisory, research, and consultancy output. |
| `INTERNAL` | ST Engineering internal reporting and assurance material. |

`SourceClass` is a property of the **source**. It is a shared fact, stored on `GRIT_SourceRegister`. Reader profiles can filter on it. They never redefine it.

## 3. Source register

`GRIT_SourceRegister` is the governed register that `CredibilityLookup()` reads. Without it, credibility has no auditable basis.

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
| `Active` | Yes/No | Only active sources can be cited. |
| `ConfigurationVersion` | Text | Required. |
| `Notes` | Multiline text | Governance commentary. |

## 4. Credibility lookup behavior

`CredibilityLookup()` remains deterministic. It performs no reasoning. It writes nothing and approves nothing.

**Inputs:**

- Source domain or publisher identifier.
- Signal reference.
- Configuration version.
- Function version.

**Outputs:**

- Matched source register reference.
- `SourceClass` and `CredibilityRating`.
- Active status and staleness result.
- Match method.
- Input and output hashes.

Rules:

1. An unmatched source returns `UNRATED` with no credibility inferred. The function never guesses. The Supervisor treats unrated evidence according to the active evidence policy.
2. The function flags a source as stale when it is inactive or past `NextReviewDateUtc`. A stale source is usable only where policy permits. The staleness flag carries into the assessment and the pack.
3. Credibility is never derived from an agent's opinion of the source.
4. Every lookup is recorded in `GRIT_FunctionExecutionLog` with the matched register reference and configuration version.

## 5. International SOS

International SOS is registered with `SourceClass = INTERNATIONAL_SOS` and carries its own credibility rating.

| Rule | Statement |
| --- | --- |
| **Ranking** | Ranks above ordinary `MEDIA` in configured credibility ordering and evidence weighting. |
| **Not government** | It is an advisory provider. It must not be treated as `OFFICIAL_GOVERNMENT`, because it is not an official public authority. |
| **Not a publication authority** | `IsPublicationAuthority = No`. An SOS advisory never authorizes publication. It never substitutes for `ReviewGate()`. |
| **Auditable** | Every SOS-derived signal retains its source record, retrieval evidence, and citation. |
| **Traceable** | SOS content is cited like any other source. It is never paraphrased into an uncited management assertion. |
| **Governed** | Subject to the same onboarding, review frequency, and staleness controls as any other source. |

Elevated ranking affects evidence weighting and reporting prominence only. It confers no governance authority.

## 6. Source onboarding requirements

A source must not be cited until all of the following exist:

1. **Source owner** — a named individual accountable for the source.
2. **Approval authority** — the named approver who onboarded it, with approval date.
3. **Review frequency** — `QUARTERLY`, `SEMI_ANNUAL`, or `ANNUAL`, with `NextReviewDateUtc` set.
4. **Credibility assignment** — a rating with written rationale. Governed configuration assigns it. An agent never does.
5. **SourceClass** — assigned from the controlled list.
6. **Active flag** — explicitly set.

Onboarding, rating changes, class changes, and deactivation are typed mutations. `RegisterWriteGate()` validates each one. The Supervisor persists it. Prior versions are retained.

## 7. Governance boundaries

- A source is evidence. It is never authority. No `SourceClass` value can approve, publish, or advance workflow state.
- `SourceClass` and credibility do not create or modify signal workflow states.
- Agents can report what a source said. They must not assign or alter its class or credibility.
- Reader profiles can include or exclude source classes. This changes what a reader sees. It never changes the shared source record or the underlying signal.

## 8. Acceptance criteria

- Every cited signal resolves to an active `GRIT_SourceRegister` entry with an owner, approver, review frequency, and rating.
- International SOS is classified `INTERNATIONAL_SOS`.
- International SOS ranks above ordinary media and holds no publication authority.
- Unmatched sources return `UNRATED` instead of an inferred rating.
- Stale sources are detectable and visibly flagged.
- Source classification introduces no workflow state and no approval path.
