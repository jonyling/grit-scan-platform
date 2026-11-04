"""grit scorecard: measure a weekly report against the URS methodology, so a change is judged by numbers, not impressions.

Each metric comes from the frozen brief alone. A metric with a target passes or fails; the others describe the report.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from typing import Any, Mapping

GOOD_BANDS = ("T1", "T2", "T3")


def _plain(value: Any) -> str:
    """Brief text is a string, or a dict with "plain" and cited "runs"."""
    return str(value.get("plain") or "") if isinstance(value, Mapping) else str(value or "")


def _score(selection: Mapping[str, Any]) -> int | None:
    value = (selection.get("editorial") or {}).get("significance")
    return sum(int(item) for item in value.values()) if isinstance(value, Mapping) else None


def _narrative(brief: Mapping[str, Any]) -> str:
    """The report's own words: its narrative fields and the standing items' developments, not the sources' titles."""
    from grit.pipeline.weekly import narrative_fields

    bottom = {"text": "", "escalation": "", **(brief.get("bottom_line") or {})}
    texts = [_plain(value) for value in narrative_fields({**brief, "bottom_line": bottom}).values()]
    texts += [_plain(row.get("development")) for row in brief.get("conditions") or []]
    return "\n".join(texts)


def scorecard(brief: Mapping[str, Any], corrections: Any = None) -> dict[str, Any]:
    """Metrics for one report. Targets follow the methodology and the 4 November 2026 review of the 2 November URS report.
    corrections: the pack's open corrections, as the brief shows them; default, the ones the brief was built with."""
    from grit.core.corrections import retired_in

    v2 = brief.get("v2") or {}
    tiers = v2.get("tiers") or {}
    cited = [key for key in brief.get("evidence_ids") or [] if key in tiers]
    bands = Counter(tiers[key] for key in cited)
    selections = {row["signal_id"]: row for row in ((brief.get("method") or {}).get("weekly") or {}).get("selections") or []
                  if isinstance(row, Mapping) and row.get("signal_id")}
    decided = [row.get("editorial") or {} for row in selections.values()]
    escalating = v2.get("escalating") or []
    escalating_scores = [_score(selections.get(str(row.get("lead")), {})) for row in escalating]
    unsupported = sum(1 for row in escalating if row.get("signal_ids") and all(tiers.get(key) in {"T4", "CONTESTED"} for key in row["signal_ids"]))
    weak_escalation = sum(1 for row, score in zip(escalating, escalating_scores)
                          if score is not None and score < 8 and not (selections.get(str(row.get("lead")), {}).get("editorial") or {}).get("immediate_threat"))
    status_text = [*(v2.get("arrows") or []), *(v2.get("risk_window") or [])]
    truncated = [text for text in status_text if not re.search(r"[.!?…▲▼=)]$", str(text).strip())]
    passing = [row for row in v2.get("watch") or [] if _plain(row.get("text")).startswith("Passing mention only")]
    gate = {row.get("question"): row for row in v2.get("gate") or []}
    contradiction = (gate.get("Were the top findings challenged by a contradiction search?") or {}).get("answer")
    steady = {row.get("name"): row.get("trend") for row in brief.get("conditions") or [] if row.get("trend") not in ("Escalating", None)}
    narrative = " ".join(_plain((brief.get("bottom_line") or {}).get(key)) for key in ("text", "escalation"))
    trend_mismatch = sorted(name for name in steady if name and _says_escalating(narrative, name))
    requests = [row for row in brief.get("management_requests") or [] if row.get("status") == "ACTIVE"]
    ledger = brief.get("corrections") or [] if corrections is None else corrections
    retired = sorted({str(row["phrase"]) for row in retired_in(_narrative(brief), ledger)})
    metrics = {
        "status": v2.get("status"),
        "cited_sources": len(cited),
        "source_bands": dict(sorted(bands.items())),
        "share_t1_t3": round(sum(bands[band] for band in GOOD_BANDS) / len(cited), 2) if cited else None,
        "contested_cited": bands.get("CONTESTED", 0),
        "unrated_outlets": len(v2.get("unrated_outlets") or []),
        "included": sum(1 for row in decided if row.get("decision") == "INCLUDE"),
        "watched_for_verification": sum(1 for row in decided if row.get("source_check")),
        "escalating_rows": len(escalating),
        "escalating_scores": escalating_scores,
        "escalating_below_8": weak_escalation,
        "escalating_on_weak_sources_only": unsupported,
        "truncated_status_text": len(truncated),
        "passing_mentions_shown": len(passing),
        "trend_words_contradict_trend": trend_mismatch,
        "requests_unanswered": sum(1 for row in requests if not row.get("signals")),
        "contradiction_search": contradiction,
        "open_corrections": len(ledger),
        "retired_phrases_used": retired,
    }
    checks = {
        "escalating rows have significance 8 or more": weak_escalation == 0,
        "no escalating row rests only on T4 or contested sources": unsupported == 0,
        "status text is not cut mid-sentence": not truncated,
        "no passing mention in the watch list": not passing,
        "the narrative's trend words match the trends": not trend_mismatch,
        "top findings were contradiction-searched": contradiction in (None, "Yes"),
        "no phrase the corrections ledger retired": not retired,
    }
    return {"metrics": metrics, "checks": checks, "passed": sum(checks.values()), "of": len(checks)}


def _says_escalating(text: str, name: str) -> bool:
    """The narrative calls the risk escalating: its first words within 80 characters of 'escalat'."""
    head = re.escape(" ".join(name.split()[:3]))
    return bool(re.search(rf"{head}.{{0,80}}escalat|escalat.{{0,80}}{head}", text, re.IGNORECASE | re.DOTALL))


def main(argv: list[str] | None = None) -> int:
    from datetime import date

    from grit.pipeline.reader_scan import run_id_for
    from grit.pool import Pool

    parser = argparse.ArgumentParser(prog="grit scorecard", description="Score each frozen weekly report of a run against the URS methodology.")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--date", required=True, type=date.fromisoformat)
    parser.add_argument("--profile", nargs="*", help="Reader profiles. Default: every report of the run.")
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON.")
    args = parser.parse_args(argv)
    pool = Pool()
    run_id = run_id_for(args.date, args.pack)
    rows = pool.rows("SELECT profile_id, status, brief_json FROM weekly_reports WHERE run_id = ? ORDER BY rowid", (run_id,))
    latest = {row["profile_id"]: row for row in rows if not args.profile or row["profile_id"] in args.profile}
    if not latest:
        print(f"No weekly report for {run_id} in this database.")
        return 1
    from grit.core.corrections import ledger_view

    ledger = [ledger_view(row) for row in pool.corrections(args.pack)]
    results = {profile: {"report_status": row["status"], **scorecard(row["brief"], ledger)} for profile, row in latest.items()}
    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 0
    for profile, result in results.items():
        metrics = result["metrics"]
        print(f"{profile}: {result['report_status']}, status {metrics['status']}, {result['passed']} of {result['of']} checks pass")
        print(f"  sources {metrics['cited_sources']} {metrics['source_bands']}, T1-T3 share {metrics['share_t1_t3']}, "
              f"unrated outlets {metrics['unrated_outlets']}, watched for verification {metrics['watched_for_verification']}")
        print(f"  escalating rows {metrics['escalating_rows']} scores {metrics['escalating_scores']}, "
              f"requests unanswered {metrics['requests_unanswered']}")
        for check, ok in result["checks"].items():
            if not ok:
                print(f"  FAIL: {check}")
    return 0
