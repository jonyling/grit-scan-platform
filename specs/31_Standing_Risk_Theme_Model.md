# GRIT-Scan: Standing Risk Theme Model

**Status:** Proposed extension  
**Baseline:** Existing Phase 1 architecture (specs 17–28), unchanged  
**Applies to:** `GRIT_Theme`, `ThemeMatch()`, `ThemeEscalationAssessment()`

## 1. Purpose

Annual Report major risks become **Standing Themes** in the governed theme register. This gives GRIT-Scan a persistent spine that management already recognises, and gives new events somewhere legitimate to attach.

This extension populates the existing `ThemeType` field on `GRIT_Theme`. It introduces no new table, no new workflow state, and no change to theme lifecycle states, `ReviewGate()`, or `TransitionGuard()`.

## 2. ThemeType

A global choice on `GRIT_Theme`, distinct from and orthogonal to `ThemeStatus` (the lifecycle).

| Value | Meaning |
| --- | --- |
| `STANDING_RISK` | An approved Annual Report major risk. Persistent, seeded, and centrally governed. |
| `EMERGING_THEME` | A newly identified development not yet attached to a standing risk. |
| `MANAGEMENT_REQUESTED` | A theme originating from an active `GRIT_ManagementRequest`. |
| `WATCH_THEME` | Retained for monitoring without current standing-risk status. |

**`ThemeType` describes what a theme *is*. `ThemeStatus` describes where it is in its lifecycle.** A `STANDING_RISK` may be `ACTIVE`, `ESCALATING`, `STABLE`, or `DECLINING`. The two are recorded and evaluated independently.

## 3. Seeding Standing Risks

Standing risks are seeded from the approved Annual Report risk register through a governed, versioned import.

- Risk names, definitions, and groupings are **configuration**, not code. Nothing is hard-coded in a function, prompt, or script.
- Each seeded theme records its source document, register version, approval reference, and configuration version.
- Seeding is a typed mutation validated by `RegisterWriteGate()` and persisted by the Supervisor.
- Seeded themes enter as `ThemeType = STANDING_RISK`, `ThemeStatus = ACTIVE`.
- Re-seeding after an Annual Report refresh creates **new versions** with lineage. Prior versions and their event relationships remain intact.

Placeholder examples only, to be replaced by the approved register at configuration time: *competition*, *financial volatility*, *major project cost escalation*, *cybersecurity*, *geopolitical impact*, *fraud and corruption*. These illustrate the abstraction level and must not be treated as the active set.

## 4. Attachment Rules

When an accepted event is evaluated against the theme register:

| Condition | Outcome |
| --- | --- |
| `ThemeMatch()` returns a confident match to a standing risk | Propose a `GRIT_ThemeEvent` relationship to that standing risk |
| Match is to a non-standing theme | Propose the relationship to that theme |
| No confident match, governed emergence criteria met | Propose a new `EMERGING_THEME` |
| No confident match, criteria not met | Remain an event with no theme relationship; record insufficient evidence |
| Event originates from an active management request | Propose `MANAGEMENT_REQUESTED`, or attach to a standing risk where one clearly applies |

Rules:

1. `ThemeMatch()` returns **candidates only**. It never creates an authoritative theme identity.
2. An event may attach to more than one theme where the evidence genuinely supports it. Each relationship is separately versioned and justified.
3. Title similarity alone never justifies attachment or merge.
4. An emerging theme may later be promoted to `STANDING_RISK` **only** through the Annual Report governance process — never by metric movement, and never by an agent.
5. Attaching an event to a standing risk does not alter the standing risk's approved definition.

## 5. Lifecycle Interaction

Standing risks use the same seven lifecycle states as any other theme: `PROPOSED`, `ACTIVE`, `ESCALATING`, `STABLE`, `DECLINING`, `CLOSED`, `REOPENED`.

Two governance differences apply:

- A standing risk is **not closed by metric decline alone**. It remains in the register until the Annual Report register changes. Declining activity moves it to `DECLINING`, not `CLOSED`.
- Closure or removal of a standing risk requires the Annual Report governance process and named human approval.

## 6. ThemeEscalationAssessment() Extension

`ThemeEscalationAssessment()` remains pure and deterministic — no reads, no writes, no agent invocation. Two inputs are added:

| Input | Required | Description |
| --- | --- | --- |
| `theme_type` | Yes | Governed `ThemeType`, so standing-risk rules can be applied. |
| `standing_risk_activity` | Conditional | Controlled activity metric for a standing risk over the comparison period. Required when `theme_type = STANDING_RISK`. |

These join the existing inputs — supporting event count, event growth rate, affected LOB count, geography count, source diversity, and regulatory activity — all supplied by `ThemeMetricCalculation()`.

Additional deterministic rules:

1. Standing-risk activity is a weighted contributor to the movement score, per the active configuration version.
2. `CLOSE_CANDIDATE` is never returned for a `STANDING_RISK`. Sustained decline returns `DE_ESCALATE` with a `DECLINING` proposal.
3. Escalation of a standing risk still requires the configured minimum evidence, diversity, and coverage gates. High event volume alone is insufficient.
4. Where `theme_type = MANAGEMENT_REQUESTED`, an active request is a configured contributor to management-attention selection, but never a substitute for evidence.

## 7. Reporting

Standing risks appear as the leading section of the Strategic Horizon Pack, showing current lifecycle state, direction of travel, supporting approved events, evidence confidence, uncertainty, citations, and approval status.

A standing risk with no supporting events in the period is reported as such. Absence of evidence is itself management information and must not be hidden by omitting the row.

## 8. Governance Boundaries

- No new signal workflow state. Theme type and seeding are register operations, validated by `RegisterWriteGate()`.
- `ReviewGate()` remains the sole business approval gate for output packages.
- `TransitionGuard()` remains signal-transition-only.
- Agents may propose attachment, emergence, and significance. They may not seed, promote, reclassify, or close a standing risk.
- Standing risks are shared facts. Reader profiles change which are surfaced and in what detail, never their definitions or lifecycle state.

## 9. Acceptance Criteria

- Annual Report risks exist as `ThemeType = STANDING_RISK` from governed, versioned configuration with no hard-coded names.
- `ThemeType` and `ThemeStatus` are independent and separately evaluated.
- New events either attach to an existing theme or create an emerging theme, always through `ThemeMatch()` and `RegisterWriteGate()`.
- Promotion to standing risk occurs only through Annual Report governance.
- `ThemeEscalationAssessment()` consumes standing-risk activity and never returns `CLOSE_CANDIDATE` for a standing risk.
- Re-seeding preserves historical versions and event relationships.
