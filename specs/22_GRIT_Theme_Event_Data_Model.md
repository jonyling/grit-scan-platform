# GRIT-Scan Phase 1: Theme, Event, and Layered Intelligence Data Model

**System of record:** Microsoft Dataverse  
**Data model principle:** Shared facts are versioned independently from reader-specific assessments.

## 1. Entity model

```text
GRIT_Theme 1:N GRIT_ThemeEvent N:1 GRIT_Event
GRIT_Event 1:N GRIT_EventSignal N:1 GRIT_Signals
GRIT_ReaderProfile 1:N GRIT_ImpactAssessment
GRIT_Theme / GRIT_Event / GRIT_Signals 1:N GRIT_ImpactAssessment
GRIT_OutputPackage 1:N GRIT_OutputPackageSignal N:1 GRIT_Signals
```

`GRIT_ImpactAssessment` never overwrites `GRIT_Theme`, `GRIT_Event`, or `GRIT_Signals`. `GRIT_OutputPackageSignal` remains the immutable package membership entity.

## 2. GRIT_Theme

| Field | Type | Rule |
| --- | --- | --- |
| ThemeId | GUID primary key | System generated. |
| ThemeName | Text | Versioned shared fact. |
| ThemeDescription | Multiline text | Versioned shared fact with citations. |
| ThemeStatus | Global Choice | `PROPOSED`, `ACTIVE`, `ESCALATING`, `STABLE`, `DECLINING`, `CLOSED`, `REOPENED`; governed configuration owns values. |
| ThemeType | Global Choice | `STANDING_RISK`, `EMERGING_THEME`, `MANAGEMENT_REQUESTED`, `WATCH_THEME`. Describes what the theme is; independent of `ThemeStatus`. See spec 31. |
| FirstObservedAtUtc / LastObservedAtUtc | Date/time, time-zone independent | UTC. |
| DirectionOfTravel | Choice | Controlled result/proposal status distinguished in record metadata. |
| ThemeConfidence | Decimal | Controlled metric or clearly labeled proposal. |
| StrategicSignificanceProposal | Choice/text | Agent proposal only. |
| ApprovedStrategicSignificance | Choice/text | Policy-approved controlled value. |
| EvidenceCoverageStatus | Choice | Deterministic coverage result. |
| ConfigurationVersion | Text | Required. |
| CreatedByExecution | Lookup to execution/run | Required. |
| CurrentApprovedVersion | Whole number | Projection to latest approved version. |
| SupersedesThemeVersion | Lookup to GRIT_Theme | Optional version lineage. |
| ContentHash | Text, 128 | Required immutable content hash. |

## 3. GRIT_Event

| Field | Type | Rule |
| --- | --- | --- |
| EventId | GUID primary key | System generated. |
| EventTitle / EventSummary | Text / multiline text | Versioned shared facts. |
| EventStatus / EventType | Global Choice | Governed lifecycle and taxonomy. |
| StartDate / LastDevelopmentDate | Date/time, time-zone independent | UTC where time is available. |
| Geography | Lookup or governed relationship | Do not use uncontrolled free text for governed geography. |
| Entities | Relationship to governed entity register | Many-to-many where needed. |
| CurrentSeverityProposal / CurrentVelocityProposal | Choice/decimal | Proposal values, never silently treated as approved. |
| EvidenceCoverageStatus | Choice | Deterministic coverage result. |
| ExistingEventMatchConfidence | Decimal | Result of matching/proposal evidence. |
| ConfigurationVersion | Text | Required. |
| CurrentApprovedVersion | Whole number | Projection. |
| SupersedesEventVersion | Lookup to GRIT_Event | Version lineage. |
| ContentHash | Text, 128 | Required. |

## 4. Relationship and Assessment Entities

### 4.1 GRIT_EventSignal

Immutable event-to-signal relationship: `EventId` lookup, `SignalId` lookup, `SignalVersion`, `RelationshipType`, `MatchMethod`, `MatchConfidence`, `EvidenceReference`, `IncludedAtUtc` (UTC), `InclusionReason`, `ConfigurationVersion`, and `ContentHash`.

### 4.2 GRIT_ThemeEvent

Versioned theme-to-event relationship: `ThemeId` lookup, `EventId` lookup, `RelationshipType`, `MatchMethod`, `MatchConfidence`, `IncludedAtUtc` (UTC), `InclusionReason`, `ConfigurationVersion`, and `ContentHash`.

### 4.3 GRIT_ImpactAssessment

Reader-specific record: `ImpactAssessmentId`, `ReaderProfileId` lookup, `ApertureType`, `NewsPriority`, optional `ManagementRequestId` lookup, optional `ThemeId`/`EventId`/`SignalId` lookups, `BusinessArea`, `LineOfBusiness`, `BusinessUnit`, `AffectedEntity`, `ExposurePath`, `BusinessConsequence`, `LinkedRisk`, `ProposedKRI`, `ProposedWatchpoint`, `ProposedAction`, `LikelihoodProposal`, `ImpactProposal`, `FinalControlledScore`, `FinalControlledBand`, `EvidenceConfidence`, `AssessmentStatus`, `ConfigurationVersion`, `ContentHash`, and source execution lookup.

`FinalControlledScore` and `FinalControlledBand` come only from `ScoreRisk()`. `NewsPriority` comes only from `ApertureRouting()` and is reader-scoped. It is never stored on `GRIT_Signals`, `GRIT_Event`, or `GRIT_Theme`. Every impact assessment remains scoped to its reader profile and aperture. See spec 29.

### 4.4 GRIT_ReaderProfile

`ReaderProfileId`, `ProfileName`, `ApertureType`, `BusinessScope`, `GeographyScope`, `RiskThemeScope`, `SourcePolicyVersion`, `WeakSignalTolerance`, `MaterialityPolicyVersion`, `ReportingTemplate`, `ReviewQueue`, `Active`, and `ConfigurationVersion`.

### 4.5 GRIT_ManagementRequest

Governed intake record for a leadership monitoring request. It is the only basis on which `ApertureRouting()` can assign `P1`. Spec 29 lists the full field set. The record has an optional lookup to `GRIT_Theme`. `GRIT_ImpactAssessment` references it when priority is `P1`.

### 4.6 GRIT_TrackingFeedback

Post-publication LOB feedback: `FeedbackId`, optional `ThemeId`/`EventId`/`SignalId` lookups, `LOB`, `SubmittedBy`, `FeedbackDate`, `FeedbackType`, `Rationale`, `RecommendedAction`.

`FeedbackType` is `TRACK`, `DO_NOT_TRACK`, `MONITOR`, `ESCALATE`, or `NEEDS_MORE_ANALYSIS`.

Feedback is governance input only. It never modifies `GRIT_Signals`, `GRIT_Event`, or `GRIT_Theme`. `ESCALATE` feedback is an input to `ThemeEscalationAssessment()`. Any resulting change still requires `RegisterWriteGate()` validation and policy-required approval. Feedback arrives after publication and is not an approval. `ReviewGate()` remains the sole approval gate.

### 4.7 GRIT_SourceRegister

Governed source register read by `CredibilityLookup()`. It holds `SourceClass`, credibility rating and rationale, source owner, approval authority, review frequency, next review date, and active flag. Spec 30 lists the full field set. `SourceClass` is a shared fact about the source and is never reader-scoped.

## 5. Keys, versions, and integrity

- Use GUID primary keys and publisher-prefixed logical names.
- Use these alternate keys:
  - Theme: name/version/configuration.
  - Event: normalized identity/version/configuration.
  - EventSignal: event/signal/signal-version.
  - ThemeEvent: theme/event/version.
  - ImpactAssessment: reader profile/subject/version.
  - OutputPackageSignal: package/signal/membership sequence.
- Theme, event, relationship, and assessment content is append-only or superseded by a new version. Historical content hashes never change.
- `EventMatch()` and `ThemeMatch()` can return candidates only. `RegisterWriteGate()` validates schema, lookup existence, authority, hashes, versions, and lifecycle policy before a typed write is persisted.
- `ThemeEscalationAssessment()` deterministically evaluates controlled event, LOB, geography, diversity, and regulatory metrics. It returns direction, velocity, escalation recommendation, and lifecycle-state proposal. It performs no writes.
- Merge, split, escalation, de-escalation, closure, reopening, and invalidation create versioned lineage records. Similar titles alone are never sufficient to merge.

## 6. Lifecycle

Agents can propose a new record, match, merge, split, escalation, decline, closure, reopening, or correction. `RegisterWriteGate()` validates the typed proposal. The Supervisor persists only accepted mutations. Human approval is required wherever the active promotion/lifecycle policy requires it. Corrected evidence supersedes affected versions and preserves historical links.

## 7. Security and audit

Organization-owned shared theme/event registers use centrally stewarded R&A roles. Reader profiles and impact assessments use row-level BA/LOB/BU access where required. Audit all create/update/status operations, configuration/version references, content hashes, relationship changes, and human approvals. Restrict direct write access to the Supervisor-controlled persistence path. Agents only propose.

## 8. Migration and audited-signal compatibility

The existing `GRITAuditedSignals` workbook is a compatibility source, not a replacement system of record. Preserve its fields during migration in the current signal/audit import model. Do not silently rename, discard, or overwrite them. Populate layered entities through versioned, auditable mappings. Retain `auditRecordId`, `flowRunConversationId`, and source timestamps as external/correlation references.

| Existing audited-signal field | Layered destination | Migration rule |
| --- | --- | --- |
| `signalId`, `headline`, `sourceName`, `sourcePublicationDate`, `sourceUrl`, `countryMarket`, `sourceUpdatedDate`, `informationCutOff` | `GRIT_Signals` | Preserve as source-fact fields; generate/retain fingerprint and signal version separately. |
| `eventSummary`, `trendStatus`, `assessmentType` | `GRIT_Event` and `GRIT_EventSignal` | Create only a proposed event/relationship unless EventMatch and RegisterWriteGate accept a governed write. |
| `matchedBusinessArea`, `matchedLineOfBusiness`, `affectedURSEntitiesMarkets`, `withinPriorityCountries`, `withinPrioritySources`, `exposurePath` | `GRIT_ImpactAssessment` and governed LOB/entity relationships | Reader-specific operational context; must not overwrite the shared signal/event. |
| `riskClassification`, `riskClassificationRationale`, `likelihoodRating`, `likelihoodJustification`, `severityRating`, `severityJustification`, `industryComparison`, `ratingBasis` | `GRIT_ImpactAssessment` | Preserve as proposal/rationale history. Final controlled score/band is populated only by `ScoreRisk()`. |
| `overallRiskRating` | `GRIT_ImpactAssessment.FinalControlledScore` / `FinalControlledBand` | Migrate as legacy rating evidence; do not relabel as a new controlled score without ScoreRisk validation and versioned mapping. |
| `recommendedManagementAction`, `analystNotes`, `auditorRecommendation`, `escalationTriggers` | `GRIT_ImpactAssessment` | Retain as proposed action, notes, recommendation, and watchpoint/trigger content with provenance. |
| `evidenceConfidence`, `sourceCrossCheck`, `assessmentLimitations` | Shared signal evidence metadata and `GRIT_ImpactAssessment.EvidenceConfidence` | Retain source-level evidence facts separately from reader-specific confidence. |
| `auditStatus`, `auditFindingsJson`, `requiresHumanSignOff`, `reportGenerationStatus`, `reportGeneratedTimestampUtc`, `loggedTimestampUtc` | Existing audit/log model and `GRIT_OutputPackage`/`GRIT_Approval` references | Preserve for audit history. Map human sign-off only to an exact package approval; it is not a signal-level approval. |
| `riskCodes` | `GRIT_ImpactAssessment.LinkedRisk` | Resolve through the governed risk register; preserve unresolvable legacy values as migration exceptions. |
| `priorityRank` | Reader-profile reporting order | Retain as a legacy presentation attribute; future ordering derives from governed profile and policy. |

This mapping gives existing URS operational detail a governed route into the `MATERIAL_APERTURE`. The same underlying signal/event evidence can still appear in `WIDE_APERTURE` strategic reporting. The mapping does not backfill a theme or event only from a workbook text field. Those records require the governed match/proposal/write process.
