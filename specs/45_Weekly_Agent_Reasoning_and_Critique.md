# Weekly agent reasoning and critique

Status: implemented behind `--reasoned`; offline validation only. Provider-backed
comparison and reader review are required before a default switch.

This specification refines the editorial boundary in specs 17, 24, 34, 35,
and 44. It does not change the controlled scoring definitions or human
approval requirements in specs 13-20.

## Authority

Agents can include, exclude, or watch a candidate for a weekly report.
The original deterministic assessment and source remain unchanged.
An editorial action cannot write scores, source ratings, register identities,
workflow state, approval, or publication status.

Every current-run candidate, including deterministic drops and research
additions, needs an explicit disposition. Duplicates remain attached to their
representative rather than becoming duplicate findings. Deferred work is not
a completed review.

Older sources can support an ongoing finding. A source published after the
run date must be labeled hindsight. Neither exception changes the source date
or implies that later evidence was available during the reporting window.

## Roles

The Supervisor selects permitted next work; a deterministic coordinator
enforces prerequisites. The Researcher obtains missing and contrary evidence.
Assessment establishes facts, exposure, transmission, impact and uncertainty.
The Critic independently challenges both selections and the complete narrative.
Reporting synthesizes the existing report sections. After a named human
decision, Memory proposes typed corrections separately from reader appetite.

Each invocation records its input, output, evidence, versions, status and
hashes. Structural validation does not establish semantic truth. Missing,
malformed or failed critique cannot be represented as a clean review.

An `INCLUDE` or `WATCH` selection must state the whole reasoning chain: `fact`,
`exposure`, `transmission` and `impact`. A link that cannot be supported is
written as "not established", not left out. A selection without the chain
breaks the contract and gets the normal repair attempt. The fast drop screen
is exempt, because anything it does not exclude goes to full review. The
Critic challenges the weakest link of each chain, not the wording.

An unsupported-claim or missing-coverage objection identifies the claim or
unchecked scope; it does not need a quotation proving the absence of evidence.
A positive factual correction must identify the supporting source passage.

## Significance and report status

An `INCLUDE` or `WATCH` selection also carries a hidden significance score,
taken from the URS methodology. The maximum is 14:

| Part | Range | Meaning |
|---|---|---|
| `exposure` | 0–3 | No credible link, indirect, identifiable, or people, project or entity directly exposed. |
| `consequence` | 0–3 | Routine, limited, material disruption possible, or major safety or business impact possible. This is the methodology's severity; `severity` stays the controlled harm score. |
| `immediacy` | 0–3 | Long term, months, weeks, or now and the next few days, judged by the event date. |
| `evidence` | 0–3 | Unverified, single weak source, credible media, or official or internal and corroborated. |
| `change` | 0–2 | No change from the previous scan, noteworthy change, or sharp deterioration. |

The contract replaces scores that the evidence cannot support and records the
original as `significance_capped`. Exposure 3 needs `exposure_status`
`CONFIRMED`. Evidence is set by rule from the bands of the cited sources'
outlets (spec 44, section 6; methodology 13D): 3 for approved internal
evidence, or an official source corroborated by another independent outlet;
2 for a T1 or T2 source, or two independent outlets; 1 for a single weak
source; 0 when only T4 or contested outlets carry it. A run assessed before
bands existed falls back to the cap by source confidence.

An `INCLUDE` whose cited sources are only unrated, T4 or contested, without
two independent unrated outlets, is recorded as `WATCH` with `source_check`
"verify" (methodology section 11). Each batch also receives other reports of
the same event, best band first, matched by shared headline words within a
week, so a selection can cite its corroboration. Each candidate shows its
`source_band` and outlets, and every call carries `authority.jurisdictions`,
the countries where the business has entities. Exposure decides relevance: a
credible event in one of them is identifiable exposure, and no role may
exclude a candidate because its impact is unconfirmed. Scores of 11–14 are top priority, 8–10 significant, 5–7 secondary,
and below 5 low. `immediate_threat` marks a credible immediate threat to the
business's people, sites, projects, customers, cash or continuity; it is top
priority at any score. Top findings for contradiction search are ranked by
this score first.

`impact_status` is `CONFIRMED`, `PLAUSIBLE` or `NOT_ESTABLISHED`. Confirmed
impact means the business has been affected, and it needs approved internal
evidence among the selection's sources. Otherwise the contract records
`PLAUSIBLE` and flags the downgrade.

The report status is set from the reviewed selections before Reporting runs,
separately from headline severity:

| Status | Rule |
|---|---|
| `RED` | The Risk & Assurance operator confirmed the impact of an included or watched finding. |
| `ELEVATED` | An included finding has possible or confirmed exposure and scores at least 2 for exposure, immediacy and evidence, or is an immediate threat, or has assessed `CONFIRMED` impact that awaits the operator's decision. |
| `WATCHPOINT` | Anything is included or watched. |
| `NORMAL` | Nothing is included or watched. |

A deterministic RED alert candidate matches a headline pattern. It still
drafts an alert for a named sender, but it does not make the report RED. The
status basis states how many candidates and assessed impacts await the
operator's decision. Reporting may not call the position RED unless the
status is RED.

The operator records `CONFIRMED` or `NOT_CONFIRMED`, with the impact, by
`grit red confirm` or `grit red reject`. Only names in `GRIT_RED_CONFIRMERS`
may decide. Decisions sit in the append-only `red_decisions` table, keyed by
pack and signal, and the latest stands. A decision is not part of the
evidence snapshot, so it does not start a new review version. It changes the
status policy that Reporting receives, so a rerun redoes only the report
stage.

### Reviewed risk links

A candidate's `condition_ids` are keyword matches to the register. For each
INCLUDE or WATCH, Assessment returns `risk_ids`: the standing items that the
fact and transmission support, or `[]` when none does. Every included or
watched item should map to the register (URS methodology, section 3). A role
may remove a keyword link, and may add a standing item that keyword matching
missed, but only from the register given in `authority.standing_items`. An
added link is listed in `risk_ids_added`, and the Critic corrects an
unsupported or missing link in its own selection. An ID outside the register
is dropped and listed in `risk_ids_dropped`. The reviewed selection then sets
the row's `condition_ids`, and keeps only the risk-cascade entries that trace
to a kept link, at entity, business-area and Group level. A removed theme no
longer counts as a strong match. The keyword links stay in
`keyword_condition_ids`. Without `risk_ids`, the deterministic links stand.

Until 4 November 2026 a role could not add a link. On the 2 November URS
week this deadlocked the run: the Critic asked for Emerging Risk 1 on Saudi
reports that had no keyword match, and no role could add it.

### Exposure snapshot

Each week R&A can supply an approved exposure snapshot: sites with headcount
and the last safety confirmation, traveller counts, projects, financial
exposures, and internal GRIT or ISOS alerts with distances to sites
(`grit/pipeline/exposure_snapshot.py`). It records counts, never names. Passed
to `grit weekly` with `--internal-evidence` and `--evidence-approved-by`, it
becomes approved internal records, the only basis on which a role may call
exposure confirmed. A site within 10 km of an alert is confirmed exposure;
further away it is possible exposure. The records go to every review batch,
the snapshot is shown in the report's first section, and the quality check
says whether one was supplied. `docs/templates/` holds an example and the
M365 Copilot prompt that prepares it.

### Action posture

`bottom_line.escalation` is the recommended action posture (URS methodology,
section 15): one principal ask, saying who should do what by when, then the
development that would raise the status. Reports label it "Recommended action
posture".

## Budgets and release

Countersearch needs an explicit nonnegative limit. There is no implicit
numeric default. Zero explicitly disables search and leaves requested checks
marked not searched. Provider limits remain additional constraints.
The top findings are up to five included items, ranked by significance score,
then RED candidates first, then by source tier and corroboration. They exist
only after review has scored the selections. If review changes which findings
rank top, a further research pass asks for their contradiction searches. The Researcher must request a `CONTRADICT`
search for each one: a query that would find a denial, correction, reversal or
later development. These searches spend the budget before any other search,
starting with findings that have none yet. The Researcher sees which findings
already have one, and a `CONTRADICT` request whose findings all have one is
skipped without spending the budget. A
top finding without a completed contradiction search opens a `CONTRA-<ID>`
issue targeted at research. The issue is material when the run had a search
budget, so it blocks readiness. When the operator set the limit to zero, it is
not material and is disclosed instead. The rule is checked again after every
revision round, so a finding that review moves into the top set also needs its
search, and while such an issue is open the revision round must be research.
Because review can change the top findings after the budget is spent, a
contradiction search for a top finding that has none may go up to five
searches past an enabled limit; no other search may.
A requested search that the budget could not run is disclosed as a limitation.
Only a search that failed is material. The completeness gate always states how
many top findings were challenged.
Source-page verification shares this network budget. A page check the
budget could not run is disclosed, like an unrun search, and does not block.
A false-positive objection is resolved once the candidate's current selection
excludes it, which is often in the same Critic call. A report refresh applies
these two rules to the issues it finds. The verifier pins a
validated public IP and checks the TLS hostname. It follows one redirect only
when the target is the same page: HTTPS, the same host apart from a leading
`www.`, and the same path apart from a trailing slash. It validates the
target's address again and records the final URL. Any other redirect needs an
explicit new reference. Extraction failures are recorded explicitly. The
source's answer is final for the run, so a page is fetched at most once. A
page check the budget could not cover is a material issue. A page the verifier
fetched but could not use is a non-material issue, and the completeness section
says that the finding relies on the retrieved text. An extracted page is still evidence,
not proof that its claims are true. Historical lookup stays within the pack,
returns at most twenty records, and records whether the result was truncated.

At most two reconciliation rounds are permitted for an analysis, including
final-report revisions. Usage survives resume. An unresolved material objection
prevents ordinary readiness. An incomplete preview is not an approved report.
A critical completeness-gate question answered "No" also prevents readiness,
even when the Critic raises no issue: the report is `INCOMPLETE`. The brief
records the blockers and other limitations under `method.weekly.completeness`
before its content hash is computed, so the final Critic reviews the same
disclosed gaps that the reader sees (spec 44, section 5).
A report that is not READY lists every reason in `blocked_by`: completeness
blockers, open material issues, changed evidence, cited signals the review
did not cover, missing assessments, and deferred candidates. Any open
material issue blocks every report, because submission and the run status
are decided for all reports together. Readiness is decided after every
report's final Critic, so the order in which reports are written does not
change the result.

Condition drafts get the same condition Critic as the legacy path. It
challenges each trend and summary against the cited sources, and the Assessor
redrafts on an accepted issue. The final Critic then treats the trend as
fixed, because Reporting cannot change it.

DEFER means a named check that cannot run now would decide between INCLUDE
and EXCLUDE. A candidate with no credible link to the business is EXCLUDE. A
final DEFER opens a material `DEFER-<ID>` issue, so a revision round revisits
the candidate. The issue resolves when a selection decides it.

A theme that an entity pack covers (`coveredBy` in the scan plan) cites that
pack's signals for the same date. In a reasoned run, those signals join the
business-area pool as `COVERED_BY_CHILD` sightings before the review, so the
agents review every signal a report can cite. A covered signal the review
excluded is not cited.
The first pass reviews every candidate. A second full pass runs only if
research acquired evidence. Revision rounds revisit only challenged candidates
and candidates without a selection.

The final report has its own rewrite budget. When the final Critic raises a
material objection against a report, Reporting rewrites it once and the Critic
reviews the new snapshot. This does not use the two pool revision rounds. In
the first live URS run (2 November 2026), the pool's re-review rounds used both
rounds, so all three report objections, including an overstated ransomware
exposure, blocked readiness without any rewrite.

Some report values are set by rule, and no rewrite can change them: the report
status and its basis, and each condition's trend from its own assessment. The
final Critic receives them as `fixed_values`, with the rule that sets them. An
objection to one of these values is non-material, so the Risk & Assurance
operator sees it, and the Critic challenges wording that overstates the value
instead. A material objection that Reporting cannot fix would otherwise block
readiness in every run.

A missing internal check is material only when a text claims confirmed
exposure or impact, or presents possible exposure as established. Possible
exposure correctly labeled without approved internal evidence is the required
wording, so the Critic lists the missing check as a non-material
`INTERNAL_EVIDENCE` issue.

Model inputs are bounded. The checks still validate quotes and citations
against the full stored text.

| Role | Receives |
|---|---|
| Assessment and Critic | Up to 16 candidates. Each has its text cut to 4,000 characters and only the status of its controlled score. Each batch also receives an index of up to 30 related items: linked duplicates, and included items that share a register condition. |
| Researcher | An index of the included items, with excerpts of 240 characters. Excluded items are given only as counts by reason. |
| Reporting and the final Critic | The report, with its selection audit reduced to counts and changes from the baseline. Cited items come with excerpts of 800 characters, and other included items with index entries. |

Each role reads a source's freshest text first. A page verified during the
run comes first, then the latest revision of the page seen by the run date,
then the text first stored. The pool keeps a page's first text unchanged, so
assessments do not drift. Different text for the same URL is stored in
`signal_revisions` only when the source states a later publication day, and
then it becomes new evidence for the analysis hash. A snippet that varies only
with the search query is not an update: in the October 2026 search logs, 1,885
of 16,416 URLs had more than one snippet, but only 73 had a later date. A
revision seen after the run date is hindsight and is not read.

A page check or internal approval after the run date does not move the source
out of its window. Its `time_scope` still follows the publication date. The
later text is labeled "retrieved after the run date: hindsight", and the
source carries `hindsight_checks`, so a fact found only in that text is called
hindsight. Before this rule, a run finished the day after its run date
labeled every verified source as hindsight, and the Critic objected to
in-window evidence as stale.

`published_at` is when a source was reported. `event_dates` lists up to three
dates (day and month) stated in the title or the first 600 characters, within
a year of the run date. Roles judge staleness by the event date, and the
source register shows "event dated" when it differs from publication.

Approved internal evidence can state exposure: a type (site, staff, contract,
travel, asset or other), a status (confirmed, possible or not exposed) and the
entity. A selection's `exposure_status` is `CONFIRMED` or `NOT_EXPOSED` only when
one of its sources is internal evidence with that status. Otherwise the
contract records `POSSIBLE` or `NOT_ESTABLISHED` and flags the downgrade.
Reporting calls exposure confirmed only for a `CONFIRMED` selection.

A request above 2,000,000 characters fails before any model call and leaves
the analysis incomplete. A Critic issue that is not material and lacks valid
evidence (a verbatim source quote, the challenged evidence, or the missing
evidence) is withheld and kept in the call output, because it blocks nothing; a
material one breaks the contract. A response that breaks its contract gets two repair
attempts, or up to three for Reporting. Each repair receives the rejected
output and accumulated validation errors. A selection that does not cover its
batch names the missing, unexpected, and repeated signal IDs. The attempts are logged, and a
response that still fails leaves the analysis incomplete. Repair inputs obey
the same size limit. A false-positive objection must identify the challenged
evidence but need not quote it. Factual corrections still need a verbatim
quote. Critic batches cannot see each other's issue IDs. If a batch reuses an
ID that already belongs to another target, the new issue is stored as
`ID@target`. Recording a critique twice does not duplicate its issues.

The exact report snapshot and its membership require critique and human
approval. New evidence or prose invalidates the corresponding readiness
result. Rendering makes no provider call. Explicit unassessed score outcomes
must remain unassessed; they cannot be replaced with invented score values.

`grit resume-report` can recover a failed Reporting call without rerunning
search, signal review, or section drafting. It requires the latest call to be
a failed Reporting call and verifies the unchanged evidence, complete Critic
selections, reconciliation checkpoint, report draft, report inputs, and
budget. It refuses recovery if a check fails. Open material objections remain
open, and the normal final-report critique and readiness rules still apply.
The final Critic can resolve only report-targeted issues; attempts to resolve
signal-level issues are recorded but do not clear them.
With `--refresh`, `grit resume-report` rebuilds every report from a finished
review instead. It requires unchanged evidence, a report stage that ended with
a validated final Critic or stopped on a failed Reporting call, the
reconciliation checkpoint, and complete Critic selections. Use it after an R&A RED decision, which changes the status policy
but not the evidence. A RED candidate that the review excluded is not listed
in the report's RED section; the status basis counts it.
`--no-submit` renders an incomplete preview without submitting it for review.

## Fast review

`--fast` is an opt-in review mode for reasoned runs. Every candidate is still
critiqued, but deterministic drops receive a compact screen first.

1. The Critic screens drops in batches of about 80 compact records: headline,
   dates, drop reason and an excerpt of up to 400 characters. The excerpt is
   the first 240 characters, then the sentences that name a pack term the
   reader matched; the matched terms are listed. `EXCLUDE` confirms a drop.
   Any other decision escalates the candidate.
2. Keeps, escalated drops and candidates without an assessment receive the
   full Assessment and Critic review described above.
3. A second pass after research, and each revision round, review only
   challenged candidates, candidates without a selection, and candidates
   whose stored evidence changed.

Batch boundaries depend only on signal IDs, so a new candidate changes only
its own batch. Each full-review batch receives context from the state before
its round, so batches run in parallel and replay the same way after a resume.
A call whose exact input was answered in an earlier version reuses that
answer, which is recorded under the current version. Only the model call and
contract check run on worker threads; all records are written in batch order.
A failed call does not discard the other calls in its phase.

A Critic may resolve only issues whose target is in its batch. An attempt to
resolve another issue is removed from the response and kept for audit.
Selecting fast review changes the analysis hash, so it starts a new version;
full-review hashes are unchanged.

## Compatibility

The existing weekly report layout and formats remain. Daily monitoring and
the Co-Pilot are not migrated by this specification. Enterprise connectors
remain out of scope; missing internal confirmations stay explicit.

Memory proposals are stored after a named human package decision. A separate
named admission is required before subsequent reasoning uses a correction or
reader preference. Only that reader can admit their preference. No admission
changes raw evidence, controlled scores, or an earlier approved artifact.

A report is not ready when it cites a signal this run's review did not cover. The exception is a scenario signpost
seen in an earlier run of the pack within the scenario window: it was kept in that run, the outlook shows it by rule
with its own date, and the narrative does not cite it.

R&A can also record a correction directly in the pack's corrections ledger
(`grit corrections`, spec 44). Every role receives the open ledger entries
with the admitted Memory corrections, and Reporting and the final Critic see
them in the report. A Reporting edit that uses a phrase the ledger retires
breaks the contract and gets the normal repair attempt. A final Critic treats
a repeated earlier statement as a material unsupported issue.
