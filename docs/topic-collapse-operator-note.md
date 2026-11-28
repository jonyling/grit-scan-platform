# Operator note: event relations and topic collapse

Flags for the reasoned weekly path (`grit weekly --reasoned`). Agents propose; deterministic rules in `grit/core/` decide; a named human approves via ReviewGate. Neither flag writes `band`, `tier`, `override_*`, or `challenge_status`.

## Flags

| Flag | CLI | Run config | What it does |
|---|---|---|---|
| Event relations | `--event-relations` | `event_relations=1` | Relation Resolver labels pairs SAME / UPDATE / CONTRADICTION / RELATED. Word overlap **nominates** pairs; only validated SAME (or a curated alias) unions events. Nightly index also runs a cheap auto-SAME path and weekly reuses by `input_hash`. |
| Topic collapse | `--topic-collapse` | `topic_collapse=1` | Topic Resolver groups distinct elevated events into persistent storylines (`topic_key`). Topics never write SAME and never collapse child `event_id`s. Near-threshold events may appear as theme candidates only. |

Both flags are opt-in. With relations off, the fallback path still requires ≥2 distinctive non-place terms (or a curated alias) before a word join. With topics off, the escalating table is unchanged (one row per elevated event).

Typical reasoned weekly:

```bash
grit weekly --pack PACK --reasoned --event-relations --topic-collapse --countersearch-limit N --profile PROFILE
```

## How to read topic rows

When `topic_collapse=1` and the resolver validates topics, the escalating section changes shape:

1. **Topic parent** — storyline label; continuity status such as `Week 3, escalating` (`status_vs_last_week`); combined implication (drafted child implications when present, else STE path / differences / basis); **worst child reader band** (Escalating > Elevated > Watch).
2. **Child rows** (indented) — event date, lead title, sources, topic confidence, and that event’s band. Child evidence stays visible; the parent only rolls up the decision frame.
3. **Unassigned elevated** — events the resolver left out of every topic render **as today** (full drafted row: summary, implications, response, sources).
4. **Emerging themes (not elevated)** — separate section from validated theme candidates (near-threshold only). Themes **never** change band; ReviewGate / a human decides whether to elevate.

Nightly index may **attach** a new event to an existing `topic_key` when its signature matches a child of that topic. It never creates a new topic overnight; minting keys remains a weekly Topic Resolver concern.

## Governance reminders

- `SAME` = one event. `RELATED` = Assessment peers. `TOPIC` = distinct events, one management decision frame.
- TopicCritic runs at most once on ambiguous borders (`confidence` 0.55–0.80).
- ReviewGate package hash includes the topic ledger hash when the flag is on.
- Evaluation numbers vs baseline: `docs/identity-eval-baseline.md` and `python3 tools/eval_identity.py`.
