# URS TW Global Conditions Scanner — Agent Specification

**Status:** Proposed, ready to build in Copilot Studio
**Role in pipeline:** Source collection layer (Layer 1 input), feeding `Invoke-URSTWWeeklyRevision.ps1`
**Pattern reused from:** Chuan Sern Wong's existing `GRIT Risk Officer – Wildcard Scan` agent (Web Search + Current News API custom connector, proven working in this tenant)

## 1. Why this agent exists

This agent supplies the weekly revision engine with real developments, so a human no longer types them in as JSON. Typing JSON by hand defeats the purpose of an agentic process.

The weekly revision engine is deterministic. It does the following:

- Validates developments.
- Detects condition movement.
- Maps developments to STE themes.
- Gates entity-level descent on evidence.

The engine script is `Invoke-URSTWWeeklyRevision.ps1`.

This agent is the missing layer. It searches for real developments against the ten Taiwan-relevant baseline conditions. It proposes candidate development records in the exact schema that the revision engine already validates. A human reviews the agent's output before it is processed. The agent proposes. It does not publish.

This mirrors the project's standing governance rule: **agents propose; deterministic functions decide; humans sign off.**

## 2. Why it cannot be a PowerShell script

The collection step must run as a Copilot Studio agent, because only that path has working live search in this tenant. The following was confirmed in this workspace:

- A coding or PowerShell session here has no `fetch_webpage`-equivalent live web access. Every external source tested was blocked or forbidden.
- PowerShell in this environment has no search API key and no Azure access.
- The only proven live-search capability in this tenant is a Copilot Studio agent. That agent uses the **Web Search** tool and the **Current News API** custom connector. Chuan's Wildcard Scan agent already does this in production.

## 3. Input configuration

The agent is grounded on the following inputs:

- `config/URS-TW-Global-Baseline.json` — the ten standing conditions, with their names, domains, and `watchIndicators`. The agent uses `watchIndicators` as the search cue list.
- `config/URS-TW-Source-Register.json` — the governed credibility register. It mirrors `GRIT_SourceRegister` and `CredibilityLookup()` from `30_Source_Taxonomy_and_Credibility_Model.md`. This register is the single place where source credibility is decided. The agent's own opinion of a source is never authoritative. The register includes the SOP §3 list. It also includes sources approved 2026-09-06: ISW, AEI (curated Taiwan/China analysis, both HIGH), China Daily (state media, HIGH — see the attribution rule in §5), Bloomberg, NYT, DigiTimes (HIGH), and several MEDIUM-rated national outlets.
- The approved URS Taiwan source list (SOP §3): Reuters, CNA/Focus Taiwan, Taipei Times, Straits Times, Taiwan MND; Taiwan Central Weather Administration; Taiwan rail/metro authority notices; BIS/MOFCOM; Fed/central-bank sources; Taiwan MOJ/Agency Against Corruption; CISA/rail OT-cyber advisories

## 4. Search task

For **each of the ten conditions**, the agent searches for developments in the prior 7-day window. It uses the condition's `watchIndicators` as query seeds. The agent runs one search per condition. A single combined pass produced the unevidenced, funnel-shaped Wilson URS-TW draft that this project reviewed earlier.

If the agent finds nothing material for a condition, it must say so explicitly. It must not omit the condition.

Conditions 1–3 are US-China strategic competition, China's political trajectory, and Taiwan Strait pressure. For these, the agent also checks ISW's and AEI's recurring "China & Taiwan Update" briefs. A structured, recurring analyst synthesis is often better evidence than a raw news search for these conditions.

## 5. Output contract

The agent must return **only** a JSON array, with no prose preamble and no markdown fencing. Agents in this tenant have repeatedly broken JSON-only instructions before. This is documented for both the Analyst and Auditor branches of the existing wildcard flow. The import script therefore extracts the JSON object defensively.

**State-media attribution rule.** The register classifies some sources as state-controlled media, for example China Daily. These are rated HIGH for their value as a signal of official positioning and intent. They are never rated as independently verified fact. A development sourced from one must be phrased as `"Beijing state messaging states..."` or `"China Daily (state media) reports..."`. It must not be presented as a neutral fact. This rule applies at the scanner and at the downstream Briefing Composer stage.

The following example shows one record in the output array:

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

- `conditionHint` must be the condition **name** or **domain**. It must not be an invented category. The import script matches it against the baseline. If the script cannot match it confidently, it rejects the record instead of guessing the condition.
- A `NO_MATERIAL_CHANGE` record is valid and expected. Every condition must produce one record per run. The record is either a finding or an explicit no-change statement that lists the sources actually checked.
- `sources` must be specific articles, never a homepage or search-results page.
- `descentCandidate` is optional. Include it only when the agent has a genuine two-hop transmission path to a named URS Taiwan asset, supplier, or cash line. Guessing a plausible-sounding BA or LOB is worse than omitting it. The deterministic gate downstream depends on this field being genuine.
- The output has no `auditStatus`, no severity scores, and no `overallRiskRating`. Downstream governed functions produce those.

## 6. Agent instructions (Copilot Studio system prompt)

Use the following text as the agent's system prompt:

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

## 7. Tools required

- Web Search, as used by the existing Wildcard Scan agent.
- The Current News API custom connector, already deployed in this tenant.

This agent needs no Dataverse connector. It only proposes candidate JSON, which `Import-URSTWScanToRevisionInput.ps1` consumes.

## 8. Downstream processing

The agent's output passes through the following steps:

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

The human review step remains, but its job changes. The reviewer checks a small set of AI-collected, evidence-tagged candidates for accuracy. The reviewer no longer types developments from scratch.

## 9. Build sequence

1. Create the agent in Copilot Studio with the instructions in §6, grounded on the baseline JSON and source list.
2. Run it manually once against the current week and capture the raw output.
3. Feed that raw output through `Import-URSTWScanToRevisionInput.ps1`, which was built alongside this spec.
4. Confirm that the script produces a valid `URS-TW-Weekly-Revision-Input.json`.
5. Run the existing revision engine and delta composer against that output.
6. Schedule the agent with a Recurrence trigger, per the platform constraints already established for this tenant. Do this only after steps 2–5 succeed on a real run.
