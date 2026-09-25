# URS TW Global Conditions Scanner — Agent Specification

**Status:** Proposed, ready to build in Copilot Studio
**Role in pipeline:** Source collection layer (Layer 1 input), feeding `Invoke-URSTWWeeklyRevision.ps1`
**Pattern reused from:** Chuan Sern Wong's existing `GRIT Risk Officer – Wildcard Scan` agent (Web Search + Current News API custom connector, proven working in this tenant)

## 1. Why This Agent Exists

The weekly revision engine (`Invoke-URSTWWeeklyRevision.ps1`) is deterministic: it validates developments, detects condition movement, maps to STE themes, and gates entity-level descent on evidence. It was never meant to be fed by a human typing JSON by hand — that step defeats the purpose of an agentic process.

This agent is the missing layer: it searches for real developments against the ten Taiwan-relevant baseline conditions and proposes candidate development records in the exact schema the revision engine already validates. A human reviews the agent's output before it is processed — the agent proposes, it does not publish.

This mirrors the project's standing governance rule: **agents propose; deterministic functions decide; humans sign off.**

## 2. Why It Cannot Be a PowerShell Script

Confirmed in this workspace:

- `fetch_webpage`-equivalent live web access is not available from a coding/PowerShell session here (blocked/forbidden for every external source tested).
- PowerShell in this environment has no search API key and no Azure access.
- The only proven working live-search capability in this tenant is a Copilot Studio agent using the **Web Search** tool and the **Current News API** custom connector — exactly as Chuan's Wildcard Scan agent already does successfully in production.

So the collection step must run as a Copilot Studio agent, not a script.

## 3. Input Configuration

The agent is grounded on:

- `config/URS-TW-Global-Baseline.json` — the ten standing conditions, their names, domains, and `watchIndicators` (used as the search cue list)
- `config/URS-TW-Source-Register.json` — the governed credibility register (mirrors `GRIT_SourceRegister`/`CredibilityLookup()` from `30_Source_Taxonomy_and_Credibility_Model.md`). This is the single place source credibility is decided; the agent's own opinion of a source is never authoritative. Includes the SOP §3 list plus sources approved 2026-09-06: ISW, AEI (curated Taiwan/China analysis, both HIGH), China Daily (state media, HIGH — see the attribution rule in §5), Bloomberg, NYT, DigiTimes (HIGH), and several MEDIUM-rated national outlets.
- The approved URS Taiwan source list (SOP §3): Reuters, CNA/Focus Taiwan, Taipei Times, Straits Times, Taiwan MND; Taiwan Central Weather Administration; Taiwan rail/metro authority notices; BIS/MOFCOM; Fed/central-bank sources; Taiwan MOJ/Agency Against Corruption; CISA/rail OT-cyber advisories

## 4. Search Task

For **each of the ten conditions**, the agent searches for developments in the prior 7-day window using the condition's `watchIndicators` as query seeds. This is a per-condition task, not one combined search — a single combined pass is what produced the unevidenced, funnel-shaped Wilson URS-TW draft previously reviewed in this project.

If nothing material is found for a condition, the agent must say so explicitly rather than omit the condition.

For conditions 1–3 (US-China strategic competition, China's political trajectory, Taiwan Strait pressure), also check ISW's and AEI's recurring "China & Taiwan Update" briefs — a structured recurring analyst synthesis is often better evidence than a raw news search for these conditions.

## 5. Output Contract

The agent must return **only** a JSON array — no prose preamble, no markdown fencing. Because agents in this tenant have repeatedly violated JSON-only instructions in the past (documented for both the Analyst and Auditor branches of the existing wildcard flow), the import script defensively extracts the JSON object regardless.

**State-media attribution rule.** Sources classified as state-controlled media in the register (e.g. China Daily) are rated HIGH for their value as a signal of official positioning and intent — never as independently verified fact. Any development sourced from one must be phrased as `"Beijing state messaging states..."` / `"China Daily (state media) reports..."`, not presented as a neutral fact. This applies at both the scanner and the downstream Briefing Composer stage.

```json
[
  {
    "conditionHint": "US Federal Reserve policy and global borrowing costs",
    "movement": "ESCALATING",
    "evidenceConfidence": "HIGH",
    "summary": "One or two sentences describing what changed and why it matters to the standing assessment.",
    "sources": [
      { "name": "Reuters", "url": "https://...", "publicationDate": "2026-09-03" }
    ],
    "descentCandidate": {
      "businessArea": "Urban Solutions",
      "lineOfBusiness": "Mobility Rail",
      "entityOrUnit": "URS Taiwan",
      "transmissionPath": "hop 1 -> hop 2 -> URS TW cost/delivery/cash/staff line"
    }
  }
]
```

Rules enforced in the prompt:

- `conditionHint` must be the condition **name** or **domain**, not an invented category. The import script matches it against the baseline; if it cannot match confidently, the record is rejected rather than guessed into the wrong condition.
- A `NO_MATERIAL_CHANGE` record is valid and expected — every condition should produce one record per run, either a finding or an explicit no-change statement with the sources actually checked.
- `sources` must be specific articles, never a homepage or search-results page.
- `descentCandidate` is optional and should only be included when the agent has a genuine two-hop transmission path to a named URS Taiwan asset, supplier, or cash line. Guessing a plausible-sounding BA/LOB is worse than omitting it, because the deterministic gate downstream depends on this being genuine.
- No `auditStatus`, no severity scores, no `overallRiskRating`. Those are downstream governed functions, not this agent's job.

## 6. Agent Instructions (Copilot Studio system prompt)

```text
You are the URS TW Global Conditions Scanner.

Your job is to check for developments against ten standing strategic conditions
related to Taiwan, US-China competition, and global financial conditions. You do
not write a report. You do not assign risk ratings. You return structured
candidate observations only.

For each of the ten conditions listed below, search the approved sources for
material developments in the last 7 days. If you find nothing material, return
a NO_MATERIAL_CHANGE record naming the sources you actually checked. Do not
guess or invent a finding to avoid returning "no change."

Conditions (search using each one's watch indicators):
{{baseline_conditions_and_watch_indicators}}

Approved sources:
{{approved_source_list}}

Output ONLY a JSON array matching this schema, with no other text:
{{output_contract}}

Rules:
- One record per condition, minimum.
- Every finding must cite a specific article URL and publication date, never a
  homepage.
- Only include descentCandidate when you have a genuine two-hop path connecting
  the development to a named URS Taiwan project, supplier, or cash line. If you
  are not sure, omit descentCandidate rather than guess.
- Never output prose, headers, or markdown before or after the JSON array.
```

## 7. Tools Required

- Web Search (as used by the existing Wildcard Scan agent)
- Current News API custom connector (same connector already deployed in this tenant)
- No Dataverse connector needed for this agent — it only proposes candidate JSON, consumed by `Import-URSTWScanToRevisionInput.ps1`

## 8. Downstream Processing

```text
Copilot Studio agent output (raw JSON, possibly with prose)
    |
    v
Import-URSTWScanToRevisionInput.ps1
    - defensive JSON extraction
    - condition-name matching against the baseline
    - rejects unmatched or placeholder records
    - rejects descent candidates lacking full evidence
    |
    v
config/URS-TW-Weekly-Revision-Input.json   (valid schema, ready for the revision engine)
    |
    v
Invoke-URSTWWeeklyRevision.ps1
    |
    v
New-URSTWWeeklyDelta.ps1
    |
    v
Human review before circulation
```

The human review step remains, but its job changes: reviewing a small set of AI-collected, evidence-tagged candidates for accuracy — not typing developments from scratch.

## 9. Build Sequence

1. Create the agent in Copilot Studio with the instructions in §6, grounded on the baseline JSON and source list.
2. Run it manually once against the current week and capture the raw output.
3. Feed that raw output through `Import-URSTWScanToRevisionInput.ps1` (built alongside this spec) and confirm it produces a valid `URS-TW-Weekly-Revision-Input.json`.
4. Run the existing revision engine and delta composer against that output.
5. Only once steps 2–4 succeed on a real run should the agent be scheduled (Recurrence trigger, per the platform constraints already established for this tenant).
