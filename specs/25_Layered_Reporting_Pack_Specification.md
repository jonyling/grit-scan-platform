# GRIT-Scan Phase 1: Layered Reporting Pack Specification

**Assembler:** `PackAssembly()` deterministic function  
**Approval gate:** `ReviewGate()`  
**Publication control:** named human commitment plus verified controlled-publication confirmation

## 1. Purpose

Layered reporting produces coordinated Strategic Horizon and Operational Risk views from one shared evidence population. PackAssembly selects only records permitted by the configured reader profile, reporting policy, approval state, and publication policy; it preserves citations and drill-down links.

A single immutable `GRIT_OutputPackage` is preferred when strategic and operational sections use the same approved evidence population. Sections must be independently identifiable, versioned, and traceable. Separate packages are required when audience, evidence population, review policy, or publication policy differs.

## 2. Strategic Horizon Pack

The Group-level pack contains an executive overview followed by ten ordered sections:

1. **Annual Report Major Risks (Standing Risks)** — `ThemeType = STANDING_RISK`, including those with no activity in the period
2. **Management Requested Topics** — items assigned `P1` against an active `GRIT_ManagementRequest`
3. **Top Emerging Themes**
4. **Fastest Escalating Themes**
5. **Competitor / Industry Watch** — `P3` content
6. **Customer Watch** — `P4` content
7. **International SOS Highlights** — `SourceClass = INTERNATIONAL_SOS`
8. **Themes Requiring Management Attention**
9. **Declining Themes**
10. **Closed Themes**

Within every section, `PackAssembly()` orders entries by `NewsPriority` ascending, so `P1` always appears above `P2`, `P3`, and `P4`. Ordering is applied at assembly time and never mutates a stored record.

Every theme entry includes theme name, concise management summary, why it matters, direction of travel, velocity where applicable, affected sectors/LOBs, supporting approved events, evidence confidence, uncertainty, citations, and human approval/publication status. Emerging, escalating, declining, and closed sections are selected from governed lifecycle state and deterministic `ThemeEscalationAssessment()` results, not article-count ranking. It is not an article-count report.

Section 1 shows lifecycle state and direction for each standing risk; a standing risk with no supporting events in the period is reported as such, because absence of evidence is itself management information. Section 2 shows the originating request title and named requester. Section 7 presents International SOS content with full citations and no elevated authority — it ranks above ordinary media in prominence only.

## 3. Operational Risk Pack

The operational pack contains detailed audited-signal and LOB-impact information: headline, event summary, source, publication date, URL, country/market, BA/LOB/BU, evidence confidence, classification, rationale, likelihood, severity/impact, final controlled rating, industry comparison, recommended management action, audit findings, auditor recommendation, human-signoff status, linked event, and linked theme.

Navigation is bidirectional:

```text
Strategic drill-down: Theme -> Event -> Signal -> LOB Impact Assessment
Operational roll-up: LOB Impact Assessment -> Signal -> Event -> Theme
```

## 4. Publication and Status Rules

- A theme/event/assessment proposal is not a published fact.
- Reporting may use proposed or draft content only with visible status labels and never present it as published fact.
- Management-facing factual claims must retain evidence citations.
- `ReviewGate()` is the sole business approval gate for immutable output packages.
- A named human must commit to the exact approved package before controlled publication integration runs.
- `PublicationConcurrencyLock()` prevents only duplicate technical delivery.
- `PUBLICATION_CONFIRMED` is recorded only from verified controlled-publication confirmation.
- AIRIS handoff is independent and may produce `AIRIS_HANDOFF_READY` without waiting for publication confirmation.
- `TransitionGuard()` applies only to actual signal workflow transitions; theme/event writes use `RegisterWriteGate()`.

## 5. PackAssembly Contract

Inputs include reader profile/aperture, approved reporting policy, package section configuration, package membership, theme/event/signal/impact records, citations, status/approval/publication facts, and configuration version. Output includes immutable section content, inclusion/suppression decisions, drill-down links, content hash, membership hash, and deterministic input/output hashes.

PackAssembly must exclude content that policy does not permit, must not promote proposed content to fact, and must not alter shared evidence. It records its invocation in `GRIT_FunctionExecutionLog`; the Supervisor persists the package through the controlled path.

## 6. Acceptance Criteria

- Wide and material readers can receive different views over identical shared evidence.
- `P1` management-requested content appears above all other priorities in every management-facing pack.
- Standing risks lead the strategic pack, including those with no activity in the period.
- Every strategic entry drills down to events/signals and every operational assessment rolls up to a theme where linked.
- Every management-facing line has citations and visible lifecycle status.
- No unapproved package is represented as published.
- Strategic and operational sections can coexist in one immutable package without cross-contaminating reader-specific assessments.
