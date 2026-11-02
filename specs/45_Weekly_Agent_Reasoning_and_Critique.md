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

An unsupported-claim or missing-coverage objection identifies the claim or
unchecked scope; it does not need a quotation proving the absence of evidence.
A positive factual correction must identify the supporting source passage.

## Budgets and release

Countersearch needs an explicit nonnegative limit. There is no implicit
numeric default. Zero explicitly disables search and leaves requested checks
marked not searched. Provider limits remain additional constraints.
Source-page verification shares this network budget. The verifier pins a
validated public IP, checks the TLS hostname, does not follow redirects, and
records extraction failures explicitly. An extracted page is still evidence,
not proof that its claims are true. Historical lookup stays within the pack,
returns at most twenty records, and records whether the result was truncated.

At most two reconciliation rounds are permitted for an analysis, including
final-report revisions. Usage survives resume. An unresolved material objection
prevents ordinary readiness. An incomplete preview is not an approved report.
The first pass reviews every candidate. A second full pass runs only if
research acquired evidence. Revision rounds revisit only challenged candidates
and candidates without a selection.

Model inputs are bounded. The checks still validate quotes and citations
against the full stored text.

| Role | Receives |
|---|---|
| Assessment and Critic | Up to 16 candidates. Each has its text cut to 4,000 characters and only the status of its controlled score. Each batch also receives an index of up to 30 related items: linked duplicates, and included items that share a register condition. |
| Researcher | An index of the included items, with excerpts of 240 characters. Excluded items are given only as counts by reason. |
| Reporting and the final Critic | The report, with its selection audit reduced to counts and changes from the baseline. Cited items come with excerpts of 800 characters, and other included items with index entries. |

A request above 2,000,000 characters fails before any model call and leaves
the analysis incomplete. A response that breaks its contract gets one repair
attempt. The model receives the validation error, and the rejected output is
logged. A second violation leaves the analysis incomplete. A false-positive
objection must identify the challenged evidence but need not quote it. Factual
corrections still need a verbatim quote. Critic batches cannot see each
other's issue IDs. If a batch reuses an ID that already belongs to another
target, the new issue is stored as `ID@target`. Recording a critique twice
does not duplicate its issues.

The exact report snapshot and its membership require critique and human
approval. New evidence or prose invalidates the corresponding readiness
result. Rendering makes no provider call. Explicit unassessed score outcomes
must remain unassessed; they cannot be replaced with invented score values.

## Fast review

`--fast` is an opt-in review mode for reasoned runs. Every candidate is still
critiqued, but deterministic drops receive a compact screen first.

1. The Critic screens drops in batches of about 80 compact records: headline,
   dates, drop reason and a 400-character excerpt. `EXCLUDE` confirms a drop.
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
