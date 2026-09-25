# 34. GRIT-Scan Rebuild Specification (Google Compute + Dataverse)

**Status:** Draft for review
**Target:** Fresh build on Group-IT Linux VM, Python 3.11+, Dataverse retained as system of record
**Supersedes:** Nothing. Specs 13–33 remain governing. This document says *what to build and in what order*, not what the rules are.

---

## 0. Why this rebuild exists

The Dataverse prototype proved the governance model works. It cannot grow further on Power Platform because: Power Automate's DLP policy blocks on-demand flow triggers (so `TransitionGuard()` and `ReviewGate()` cannot be callable functions), and orchestration is 43 PowerShell scripts run by hand.

Moving **compute** to the Group-IT VM removes both limits. **Dataverse stays** — reachability with app auth was tested from two networks outside the tenant (HTTP 200, live rows returned), and a Postgres migration was rejected on cost (S$550–600/mo).

---

## 1. Invariants — violate none of these

| # | Invariant | Source |
|---|---|---|
| I1 | Agents propose. Deterministic functions decide. Named humans approve. | 17, 21 |
| I2 | Same input → same output, forever. No LLM call inside any scoring path. | 17 |
| I3 | Missing data returns an explicit absence marker. Never a default, inferred, or guessed value. | 15, impact_scoring.py |
| I4 | Every rating is reconstructable from logged fields alone, without re-running the scan. | 17, 18 |
| I5 | `GRIT_WorkflowState` is append-only. Supervisor is its only writer. Every row requires `TransitionGuard() = ALLOW`. | 18, 20 |
| I6 | `ReviewGate()` is the sole business approval gate. No package publishes without it. | 19 |
| I7 | Severity is the **maximum** across dimensions — never averaged, summed, or weighted. | 15 |
| I8 | Financial severity uses value-at-risk only. Never contract value. Absent → `INSUFFICIENT_DATA`. | 15 |
| I9 | No likelihood/probability estimation anywhere in signal scoring. `16_Likelihood_Definition.md` is an annual-assessment instrument and must not be imported. | 17 |
| I10 | Risk taxonomy is a tag and an optional gate. Never an arithmetic multiplicand. | 13 |

---

## 2. Architecture

```
Gemini agents (google-genai)          ← propose only, no write access
        ↓ typed proposals
Supervisor service (Python)           ← only component that persists state
        ↓ calls
Deterministic core (grit_scan/)       ← pure functions, no I/O, no network
        ↓ via
Dataverse gateway (msal + requests)   ← app-only auth, retry, idempotency
        ↓
Dataverse (unchanged schema)          ← system of record
        ↑
Streamlit UI                          ← review/approve, read-only on facts
```

**Runtime:** Group-IT VM `10.193.39.151` (Ubuntu 24.04). Shared box — use a venv, never system Python. Do not apply OS updates; IT owns the host.

**Approved libraries only:** `pydantic, requests, msal, openpyxl, pandas, python-dotenv, streamlit, plotly, google-genai, beautifulsoup4, nbformat, nbclient, ipykernel, ipywidgets, scikit-learn, xgboost, paramiko`.
`scikit-learn`/`xgboost` must not touch severity, transmission, evidence, or verdict. Clustering as a *proposal aid* only.

---

## 3. Component specifications

### S1 — Deterministic scoring core `grit_scan/`
**Port unchanged. Do not rewrite.** 932 lines, stdlib-only by design, 73 passing tests.

| Function | Returns |
|---|---|
| `grade_evidence(signal)` | HIGH/MEDIUM/LOW + tier, outlet count, SOP caveat |
| `classify_horizon(signal)` | MATERIALISED/IMMINENT/DEVELOPING/LATENT from `expected_impact_date` |
| `score_severity(signal, definition)` | max band + driving dimension + per-dimension basis |
| `resolve_transmission(signal, registry)` | DIRECT/COUNTERPARTY/PERIMETER/SECTOR/AMBIENT + matched entity |
| `is_high_negative_impact(sev, trans, evid)` | HIGH_NEGATIVE_IMPACT / POTENTIAL_HIGH / NOT_HIGH + rationale |
| `assess_impact(...)` | orchestrates the above; makes no judgement of its own |

This **is** the spec's `ScoreRisk()`. Register it in `GRIT_FunctionCatalog` under that name; do not build a second scorer.

**Done when:** `python run-impact-contract-tests.py` reports 73 PASS on the VM with zero code changes.

---

### S2 — Registry loaders
Port the 4 PowerShell matchers to Python, preserving behaviour exactly.

| Source | Target | Governs |
|---|---|---|
| `Get-URSTWSourceCredibility.ps1` | `credibility_lookup.py` | `CredibilityLookup()` (spec 30) |
| `Get-ExposureEntityMatch.ps1` | `exposure_matcher.py` | entity/alias binding |
| `Get-LOBMatch.ps1` | `lob_matcher.py` | active-LOB scope gate |
| `Get-STEThemeMatch.ps1` | `theme_matcher.py` | 14 STE themes (spec 13) |

Config JSON registers port as-is — they are platform-neutral.

**Carry forward the known keyword bug:** never match a keyword under ~5–6 chars unless it is a standalone word. `"ai"` matched inside `"Taiwan"`; `"war"` inside `"warehouses"`. Both were found only against real scan text.

**Done when:** each matcher reproduces its PowerShell test fixture's output exactly, plus the two false-positive regression cases above.

---

### S3 — Dataverse gateway `dataverse/client.py`
Single module. No other component talks to Dataverse directly.

- Auth: `msal.ConfidentialClientApplication` (app-only). Credentials from env vars, never in code, never in git.
- Requires App Registration + Application User + least-privilege role on `grit_*` tables (admin task, prompt already drafted).
- Idempotency: query by `grit_contenthash` before create; PATCH if found. Never touch approval fields on update.
- **Client-side name comparison for uniqueness probes.** A server-side OData `$filter` on a text column silently missed an existing row and created a duplicate package (2026-09-11).
- **Client secrets copied via email/screenshot can pick up invisible whitespace.** `AADSTS7000215 invalid_client` with a correct, freshly-issued secret was caused by a hidden trailing character introduced somewhere in the copy chain, not a wrong value. Always `.Trim()` a pasted secret before use.

**Done when:** create → read → update → dedup round-trips against a scratch record, and re-running the same import twice creates exactly one row.

**PROVEN (2026-09-24).** `grit_scan/test_dataverse_app_only.py` confirmed app-only token + authenticated read. `grit_scan/test_dataverse_roundtrip.py` confirmed create → patch → dedup: pass 1 created a scratch signal, pass 2 found it by `grit_contenthash` and patched it instead of duplicating, final count check passed. Application User + security role are correctly configured. **S3 exit criteria fully met — P1 is closed.** One residual bug found and fixed along the way: Dataverse rejects compact-format timestamps (`20260924T021547Z`) on real datetime fields; only proper ISO 8601 (`2026-09-24T02:15:47Z`) is accepted — keep display-string tags and actual datetime field values in separate variables, never reuse one for both.

---

### S4 — `TransitionGuard()` and `ReviewGate()`
Pure functions in Python. This is the whole point of leaving Power Platform: they become real callable functions instead of inline Scopes.

- `TransitionGuard(envelope) → ALLOW | DENY | ALREADY_APPLIED` + reason code + hashes. No writes, no queries.
- `ReviewGate(context) → APPROVE | REJECT | HOLD | RETURN_FOR_REWORK` + findings. No queries — Supervisor supplies all context.
- Rework is **package-wide**: every member signal returns to `ASSESSED` together.

Reference logic already exists in `Test-TransitionGuard.ps1` and spec 19 pseudo-code.

**Done when:** the spec 20 §12 test matrix passes, including stale-sequence DENY, idempotency conflict, and FAILED→RESTORE.

---

### S5 — Supervisor service
Replaces the 43 hand-run scripts. Only component that writes `GRIT_WorkflowState`.

Per signal: call functions in order → validate agent proposals against evidence → call `TransitionGuard()` → persist only on ALLOW → log every call to `GRIT_FunctionExecutionLog` / `GRIT_AgentExecutionLog`.

**Done when:** one signal runs `NEW → FETCHED → DEDUPED → ASSESSED → CRITIQUED → PENDING_REVIEW → APPROVED` with a guard row for every transition. *This chain has never been proven end-to-end and is the single most important acceptance test in this document.*

---

### S6 — Agent layer (Gemini)
Four agents, each with a narrow prompt and tool access: **Researcher**, **Assessment**, **Critic**, **Reporting**.

- Agents call deterministic functions as tools. They never compute a score, hash, credibility rating, or routing result.
- Every response recorded in `GRIT_AgentExecutionLog` with prompt version + response hash.
- Critic prompt is already drafted — it must call URL/entity/corroboration checks as tools, and report `NOT_VERIFIABLE` rather than filling a gap.
- No agent holds Dataverse write credentials.

**Done when:** an agent proposal that cites a non-existent entity is rejected by the Supervisor, not persisted.

---

### S7 — Weekly pipeline
Port the proven 6-script chain into one module:

```
raw scan (agent or .docx)
  → normalise to candidate schema        (condition fuzzy-match, reject placeholders)
  → revision  (exposure + LOB + themes)
  → ranking   (priority + credibility)
  → delta     (human-readable review artifact)
  → Dataverse import
```

`docx_reader.py` ports as-is (stdlib zip+XML — `python-docx` is not on the approved list).

**Done when:** the 2026-09-21 week reproduces byte-identical `revision.json` to the PowerShell run.

---

### S8 — Streamlit UI
Replaces the model-driven app. Minimum: signal list, signal detail with its impact assessment, Approve/Return-for-Rework, weekly package view.

- UI calls `ReviewGate()`. It never writes state directly.
- Show absence explicitly — `NOT_ASSESSED` and `INSUFFICIENT_DATA` must read as *"not established"*, never as a blank or a zero.
- Don't render fields nothing populates yet (the Dataverse form had ~20 empty rows; it read as broken, not as honest).

**Done when:** a reviewer can approve a package without touching Dataverse directly, and the resulting `GRIT_Approval` references the exact package version + hash.

---

## 4. Build order

| Phase | Deliverable | Exit criteria |
|---|---|---|
| **P0** | VM setup, repo clone, venv | 73 tests pass on the VM unchanged |
| **P1** | S3 gateway + app-only auth | ~~Idempotent round-trip against Dataverse~~ **DONE 2026-09-24** |
| **P2** | S2 matchers + S7 pipeline | Fixture contracts pass (89 total with P0). Byte-identical 2026-09-21 `revision.json` is not yet proven: the governed registers and golden output are content-excluded on this VM. |
| **P3** | S4 guards + S5 Supervisor | Full NEW→APPROVED chain with guard evidence |
| **P4** | S8 UI | Human approves a package through the UI |
| **P5** | S6 agents | Proposals logged, validated, rejected when unevidenced |
| **P6** | Theme/Event layer (specs 22, 26, 31) | Deferred — do not start before P4 |

Do not begin a phase before its predecessor's exit criteria are met.

---

## 5. Known gaps — carry forward honestly

1. **Financial dimension is dead until a contract register exists.** No sheet in `URS Entity Info.xlsx` carries value-at-risk or margin-at-risk. `FINANCIAL` returns `INSUFFICIENT_DATA` on every real signal tested. Narrow ask: margin-at-risk + status for the 6 Tier-1 Taiwan projects only.
2. **Acquisition supplies prose, not structured facts.** Two backtests (7 audited signals, 10 live weekly signals) returned `NOT_ASSESSED`/`AMBIENT` throughout, because no source carries fatality counts, injury severity, or exact registered entity names. *Fixing the scan prompt is cheaper than any code change and unblocks the most value.*
3. **Source tiers are not yet governed.** `SourceTier` assignment in the backtest was a one-off judgement call. Wire `CredibilityLookup()` to `GRIT_SourceRegister` before trusting evidence grades.
4. **Wire syndication inflates corroboration.** One wire story carried by three papers counts as three outlets and alone clears the ≥2 bar. Acquisition must tag syndicated pickups before scoring sees them.
5. ~~Auth is still interactive.~~ **Resolved 2026-09-24.** App-only (`ConfidentialClientApplication`) auth is fully proven against live Dataverse — token acquisition, authenticated read, create, update, and dedup all confirmed via `test_dataverse_app_only.py` and `test_dataverse_roundtrip.py`. P1 is closed.
6. **Resolved with Group-IT.** The SSH VM is AWS (`AWS-GRITWAU02`, kernel `6.17.0-1019-aws`). The service called from that VM is GCP. Do not treat the VM as a GCP instance, and do not call AWS application services from it. The rejected PostgreSQL option was an AWS database experiment, not the compute platform.
7. **P2 golden week is not reproducible here.** The four matchers and the weekly pipeline are ported and covered by in-repo fixtures that encode the PowerShell contracts, including the `ai`/`war` short-token guard. `URS-TW-Exposure-Ontology.json`, the source register, LOB profiles, annual-risks JSON, the global baseline, and `output/2026-09-21/` are content-excluded, so byte-identical `revision.json` cannot be checked until those files are supplied another way. Python JSON will also not be byte-identical to Windows PowerShell `ConvertTo-Json`.

---

## 6. Do not carry forward

The 8 data-repair scripts, demo-seed data, the device-code auth pattern, and any PowerShell 5.1 workaround (single-element array unrolling, `ConvertFrom-Json` array corruption, `$`-in-double-quotes). These are artefacts of the old platform.

---

## 7. Open — awaiting the 10 capability requirements

This spec covers *rebuilding what exists* plus the governance already written in 13–33. The ten target capabilities slot in here and may add components or reorder Phase 6. Each should be written as: **trigger → deterministic decision → who signs off → what gets logged.** Anything that cannot be written that way is not yet a specification.

---

## 8. Opportunity cost is a signal class, not a harm score

A missed market is in scope. Example: a Vietnam rail opening linked to Urban Solutions, where URS has no registered entity or project. Not participating can matter to management. It is not `HIGH_NEGATIVE_IMPACT`.

`classify_opportunity_cost()` decides only this:

| Input | Result |
|---|---|
| Participation question not supplied | `NOT_ASSESSED`. Prose is never read as a market opening. |
| No governed LOB keyword | `NOT_LINKED`. |
| Registered entity or project already in that country, or our entity is named | `PRESENT`. |
| LOB keyword matches and presence is absent | `OPPORTUNITY_COST`. A human must confirm it. |

The cost of not participating stays `INSUFFICIENT_DATA` until Finance supplies a value-at-risk figure. That figure is recorded. It is not converted into a severity band and it does not change the harm verdict.
