#!/usr/bin/env python3
"""Offline identity-merge evaluation harness (Phase E).

Runs the current `_group` + `_merge_stories` (+ optional `label_merges`) path on
`tests/fixtures/identity_eval/` and prints:

  - false-merge rate   (UNRELATED pairs placed in the same event)
  - missed-merge rate  (SAME pairs left in different events)
  - max cluster size / max distinct-country count per event
  - topic purity / completeness vs topics.jsonl (when topics have event_ids)

Does not modify pipeline merge behaviour. No network / LLM calls.

Usage (from repo root):
  .venv/bin/python tools/eval_identity.py
  .venv/bin/python tools/eval_identity.py --json
  .venv/bin/python tools/eval_identity.py --fixtures tests/fixtures/identity_eval
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_FIXTURES = ROOT / "tests" / "fixtures" / "identity_eval"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _authoritative_label(pair: dict[str, Any]) -> str | None:
    """Only R&A labels count for rates. Provisional export heuristics are ignored for headline rates."""
    if pair.get("authoritative") is True:
        label = (pair.get("label") or "").strip().upper()
        return label or None
    status = (pair.get("label_status") or "").lower()
    source = (pair.get("label_source") or "").lower()
    if status in {"ra", "authoritative", "labelled"} or source in {"ra", "jony", "r&a"}:
        label = (pair.get("label") or "").strip().upper()
        return label or None
    return None


def _provisional_label(pair: dict[str, Any]) -> str | None:
    label = (pair.get("label") or "").strip().upper()
    if label in {"SAME", "RELATED", "UNRELATED"}:
        return label
    return None


def _signal_rows_for_week(week_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for item in _load_jsonl(week_dir / "signals.jsonl"):
        editorial = item.get("editorial") if isinstance(item.get("editorial"), dict) else {}
        rows.append(
            {
                "signal_id": item["signal_id"],
                "title": item.get("title") or "",
                "text": item.get("text") or "",
                "publisher": item.get("publisher") or item.get("signal_id"),
                "published_at": item.get("published_at") or "",
                "tier": item.get("tier"),
                "source_band": item.get("source_band") or "UNRATED",
                "rung": item.get("rung"),
                "lane": item.get("lane"),
                "red": item.get("red"),
                "red_status": item.get("red_status"),
                "editorial": {
                    "decision": editorial.get("decision") or "INCLUDE",
                    "source_ids": list(editorial.get("source_ids") or []),
                    "immediate_threat": bool(editorial.get("immediate_threat")),
                    "significance": editorial.get("significance"),
                    "risk_ids": list(editorial.get("risk_ids") or []),
                },
            }
        )
    return rows


def _build_events(rows: list[dict[str, Any]], drafted_items: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Reproduce the weekly clustering path offline: _group → elevate/score → _merge_stories → label_merges."""
    from grit.pipeline.events import (
        UP,
        _elevated,
        _group,
        _merge_stories,
        apply_merges,
        label_merges,
        significance_total,
    )
    from grit.pool import Pool

    pool = Pool(":memory:")
    groups = _group(pool, rows, same_pairs=[])
    events: list[dict[str, Any]] = []
    for event_id, members in groups.items():
        members = sorted(members, key=lambda row: str(row.get("published_at") or ""), reverse=True)
        scores = [significance_total(row) for row in members]
        scores = [s for s in scores if s is not None]
        score = max(scores) if scores else None
        elevated = _elevated(members)
        lead = members[0]
        dates = [str(row.get("published_at") or "")[:10] for row in members if row.get("published_at")]
        first = min(dates) if dates else "2026-01-01"
        last = max(dates) if dates else first
        events.append(
            {
                "event_id": event_id,
                "band": UP if elevated else "=",
                "new": True,
                "score": score,
                "elevated": elevated,
                "tier": min((row.get("tier") or 9) for row in members) if members else 9,
                "red": False,
                "red_candidate": False,
                "lead": lead,
                "members": members,
                "reports": members,
                "signal_ids": [row["signal_id"] for row in members],
                "publishers": len({row.get("publisher") for row in members}),
                "first_date": first,
                "last_date": last,
                "risks": [],
                "risk_label": "",
                "requests": [],
            }
        )
    events = _merge_stories(events, {}, same_pairs=[])
    if drafted_items:
        merges = label_merges(events, {eid: {"item": item} for eid, item in drafted_items.items()}, same_pairs=[])
        events = apply_merges(events, merges, {})
    return events


def _membership(events: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for event in events:
        eid = event["event_id"]
        for sid in event.get("signal_ids") or []:
            mapping[sid] = eid
        for row in event.get("members") or []:
            mapping[row["signal_id"]] = eid
    return mapping


def _cluster_stats(events: list[dict[str, Any]]) -> dict[str, Any]:
    from grit.core.countries import named

    sizes = []
    country_counts = []
    for event in events:
        members = list(event.get("members") or [])
        sizes.append(len(members) or len(event.get("signal_ids") or []))
        blob = " ".join(f"{row.get('title') or ''} {row.get('text') or ''}" for row in members)
        country_counts.append(len(named(blob)))
    return {
        "event_count": len(events),
        "elevated_event_count": sum(1 for e in events if e.get("elevated")),
        "max_cluster_size": max(sizes) if sizes else 0,
        "max_distinct_country_count": max(country_counts) if country_counts else 0,
        "mean_cluster_size": (sum(sizes) / len(sizes)) if sizes else 0.0,
    }


def _pair_rates(pairs: list[dict[str, Any]], membership: dict[str, str], *, authoritative_only: bool) -> dict[str, Any]:
    false_merges = missed = related_same_cluster = 0
    n_unrelated = n_same = n_related = 0
    skipped = 0
    used = 0
    for pair in pairs:
        label = _authoritative_label(pair) if authoritative_only else _provisional_label(pair)
        if not label:
            skipped += 1
            continue
        a, b = pair["signal_a"], pair["signal_b"]
        if a not in membership or b not in membership:
            skipped += 1
            continue
        used += 1
        same_cluster = membership[a] == membership[b]
        if label == "UNRELATED":
            n_unrelated += 1
            if same_cluster:
                false_merges += 1
        elif label == "SAME":
            n_same += 1
            if not same_cluster:
                missed += 1
        elif label == "RELATED":
            n_related += 1
            if same_cluster:
                related_same_cluster += 1
    return {
        "pairs_considered": used,
        "pairs_skipped": skipped,
        "n_same": n_same,
        "n_related": n_related,
        "n_unrelated": n_unrelated,
        "false_merges": false_merges,
        "missed_merges": missed,
        "related_collapsed_to_same": related_same_cluster,
        "false_merge_rate": (false_merges / n_unrelated) if n_unrelated else None,
        "missed_merge_rate": (missed / n_same) if n_same else None,
        "label_mode": "authoritative" if authoritative_only else "provisional",
    }


def _topic_metrics(
    topics_row: dict[str, Any],
    events: list[dict[str, Any]],
    membership: dict[str, str],
    *,
    fixtures: Path,
) -> dict[str, Any]:
    """Purity / completeness when topic event_ids are filled. Gaps → null metrics."""
    topics = topics_row.get("topics") or []
    assigned = []
    for topic in topics:
        eids = [eid for eid in (topic.get("event_ids") or []) if eid and eid != "TBD"]
        if eids:
            assigned.append((topic.get("topic_key") or topic.get("label"), eids))
    if not assigned:
        return {
            "topic_purity": None,
            "topic_completeness": None,
            "topics_with_event_ids": 0,
            "gap_for_jony": bool(topics_row.get("gap_for_jony")),
            "note": "no topic event_ids filled — awaiting R&A",
        }

    # Map exported elevated event_id → offline cluster id via shared member signals.
    # Fixture events.jsonl carries member signal ids for escalating rows.
    # Completeness: fraction of expected topic event_ids that land in the dominant offline cluster for that topic.
    # Purity: among signals in those clusters, fraction whose expected topic matches.
    # Without a signal→topic gold map beyond event_ids, approximate using event_id membership in fixture export.
    week_events = {
        e["event_id"]: e
        for e in _load_jsonl(fixtures / "weeks" / topics_row["week_id"] / "events.jsonl")
    }
    # Build expected signal → topic
    expected: dict[str, str] = {}
    for key, eids in assigned:
        for eid in eids:
            for sid in (week_events.get(eid) or {}).get("member_signal_ids") or []:
                expected[sid] = key

    if not expected:
        return {
            "topic_purity": None,
            "topic_completeness": None,
            "topics_with_event_ids": len(assigned),
            "gap_for_jony": True,
            "note": "topic event_ids present but no member signals resolved",
        }

    # Offline cluster → signals
    cluster_signals: dict[str, set[str]] = defaultdict(set)
    for sid, cid in membership.items():
        cluster_signals[cid].add(sid)

    # Completeness: for each topic, take signals expected in topic; share of those co-located with the topic's mode cluster
    completeness_scores = []
    purity_scores = []
    for key, eids in assigned:
        exp_signals = {sid for sid, t in expected.items() if t == key}
        if not exp_signals:
            continue
        # mode cluster among expected signals that were clustered
        counts: dict[str, int] = defaultdict(int)
        for sid in exp_signals:
            if sid in membership:
                counts[membership[sid]] += 1
        if not counts:
            completeness_scores.append(0.0)
            continue
        mode_cluster = max(counts, key=counts.get)
        completeness_scores.append(counts[mode_cluster] / len(exp_signals))
        # purity of mode cluster relative to this topic
        cluster = cluster_signals[mode_cluster]
        if cluster:
            purity_scores.append(len(cluster & exp_signals) / len(cluster))

    return {
        "topic_purity": (sum(purity_scores) / len(purity_scores)) if purity_scores else None,
        "topic_completeness": (sum(completeness_scores) / len(completeness_scores)) if completeness_scores else None,
        "topics_with_event_ids": len(assigned),
        "gap_for_jony": bool(topics_row.get("gap_for_jony")),
        "note": "provisional substring-filled topics; treat as indicative until R&A confirms",
    }


def evaluate(fixtures: Path) -> dict[str, Any]:
    pairs = _load_jsonl(fixtures / "pairs.jsonl")
    topics = {row["week_id"]: row for row in _load_jsonl(fixtures / "topics.jsonl")}
    weeks_dir = fixtures / "weeks"
    week_ids = sorted(p.name for p in weeks_dir.iterdir() if p.is_dir()) if weeks_dir.exists() else []

    week_results = []
    all_membership: dict[str, str] = {}
    global_stats = {
        "max_cluster_size": 0,
        "max_distinct_country_count": 0,
    }

    for week_id in week_ids:
        week_dir = weeks_dir / week_id
        rows = _signal_rows_for_week(week_dir)
        drafted = {
            ev["event_id"]: (ev.get("item") or "")
            for ev in _load_jsonl(week_dir / "events.jsonl")
            if ev.get("elevated") and ev.get("item")
        }
        events = _build_events(rows, drafted_items=drafted)
        membership = _membership(events)
        # Prefix week to avoid cross-week id collisions in aggregate pair checks
        for sid, eid in membership.items():
            all_membership[f"{week_id}::{sid}"] = f"{week_id}::{eid}"
        stats = _cluster_stats(events)
        global_stats["max_cluster_size"] = max(global_stats["max_cluster_size"], stats["max_cluster_size"])
        global_stats["max_distinct_country_count"] = max(
            global_stats["max_distinct_country_count"], stats["max_distinct_country_count"]
        )
        topic_metrics = _topic_metrics(
            topics.get(week_id) or {"week_id": week_id, "topics": [], "gap_for_jony": True},
            events,
            membership,
            fixtures=fixtures,
        )
        # Per-week pair rates (pairs scoped to week)
        week_pairs = [p for p in pairs if p.get("week_id") == week_id]
        week_results.append(
            {
                "week_id": week_id,
                "signals": len(rows),
                "cluster_stats": stats,
                "pair_rates_authoritative": _pair_rates(week_pairs, membership, authoritative_only=True),
                "pair_rates_provisional": _pair_rates(week_pairs, membership, authoritative_only=False),
                "topic_metrics": topic_metrics,
            }
        )

    # Aggregate pair rates: membership keys are week-prefixed; pairs are not — evaluate per week then micro-average
    def micro_average(key: str) -> dict[str, Any]:
        false = missed = n_u = n_s = used = 0
        for wr in week_results:
            block = wr[key]
            false += block["false_merges"]
            missed += block["missed_merges"]
            n_u += block["n_unrelated"]
            n_s += block["n_same"]
            used += block["pairs_considered"]
        return {
            "pairs_considered": used,
            "false_merges": false,
            "missed_merges": missed,
            "n_unrelated": n_u,
            "n_same": n_s,
            "false_merge_rate": (false / n_u) if n_u else None,
            "missed_merge_rate": (missed / n_s) if n_s else None,
        }

    auth = micro_average("pair_rates_authoritative")
    prov = micro_average("pair_rates_provisional")
    topic_purities = [wr["topic_metrics"]["topic_purity"] for wr in week_results if wr["topic_metrics"]["topic_purity"] is not None]
    topic_comps = [wr["topic_metrics"]["topic_completeness"] for wr in week_results if wr["topic_metrics"]["topic_completeness"] is not None]

    return {
        "fixtures": str(fixtures),
        "weeks": week_ids,
        "headline": {
            "false_merge_rate_authoritative": auth["false_merge_rate"],
            "missed_merge_rate_authoritative": auth["missed_merge_rate"],
            "false_merge_rate_provisional": prov["false_merge_rate"],
            "missed_merge_rate_provisional": prov["missed_merge_rate"],
            "max_cluster_size": global_stats["max_cluster_size"],
            "max_distinct_country_count": global_stats["max_distinct_country_count"],
            "topic_purity_mean": (sum(topic_purities) / len(topic_purities)) if topic_purities else None,
            "topic_completeness_mean": (sum(topic_comps) / len(topic_comps)) if topic_comps else None,
            "authoritative_pairs_considered": auth["pairs_considered"],
            "provisional_pairs_considered": prov["pairs_considered"],
            "baseline_status": (
                "provisional_only"
                if auth["pairs_considered"] == 0
                else "authoritative"
            ),
        },
        "aggregate_authoritative": auth,
        "aggregate_provisional": prov,
        "weeks_detail": week_results,
    }


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "n/a (no labelled pairs)"
    return f"{value:.3f} ({value * 100:.1f}%)"


def print_report(result: dict[str, Any]) -> None:
    h = result["headline"]
    print("Identity eval (Phase E) — current merge path")
    print(f"fixtures: {result['fixtures']}")
    print(f"weeks: {', '.join(result['weeks'])}")
    print()
    print("Headline")
    print(f"  baseline_status:              {h['baseline_status']}")
    print(f"  false-merge rate (auth):      {_fmt_rate(h['false_merge_rate_authoritative'])}")
    print(f"  missed-merge rate (auth):     {_fmt_rate(h['missed_merge_rate_authoritative'])}")
    print(f"  false-merge rate (provisional): {_fmt_rate(h['false_merge_rate_provisional'])}")
    print(f"  missed-merge rate (provisional): {_fmt_rate(h['missed_merge_rate_provisional'])}")
    print(f"  max cluster size:             {h['max_cluster_size']}")
    print(f"  max distinct-country count:   {h['max_distinct_country_count']}")
    print(f"  topic purity (mean):          {_fmt_rate(h['topic_purity_mean'])}")
    print(f"  topic completeness (mean):    {_fmt_rate(h['topic_completeness_mean'])}")
    print()
    for wr in result["weeks_detail"]:
        cs = wr["cluster_stats"]
        tm = wr["topic_metrics"]
        print(f"Week {wr['week_id']}: signals={wr['signals']} events={cs['event_count']} "
              f"elevated={cs['elevated_event_count']} max_size={cs['max_cluster_size']} "
              f"max_countries={cs['max_distinct_country_count']}")
        print(f"  topics: purity={tm.get('topic_purity')} completeness={tm.get('topic_completeness')} "
              f"({tm.get('note')})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--json", action="store_true", help="Print full JSON result")
    args = parser.parse_args(argv)
    if not args.fixtures.exists():
        print(f"fixtures not found: {args.fixtures}", file=sys.stderr)
        return 2
    result = evaluate(args.fixtures)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
