"""P2 / P3-style analytics for event-relation false merges and missed merges.

Reads the append-only event_relations ledger and Critic evidence. Never changes a
relation, band, override, or challenge. R&A reviews the report; apply stays false.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from grit.core.event_relations import AMBIGUOUS_HI, AMBIGUOUS_LO, T_SAME, applies_same
from grit.pool import Pool

FUNCTION_NAME = "RelationLearningReport"
FUNCTION_VERSION = "1.0.0"


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _evidence(row: Mapping[str, Any]) -> Mapping[str, Any]:
    value = row.get("evidence")
    return value if isinstance(value, Mapping) else {}


def relation_outcome_stats(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Label / status mix and Critic-driven false-merge / missed-merge proxies."""
    by_label: Counter[str] = Counter(str(row.get("label") or "") for row in rows)
    by_status: Counter[str] = Counter(str(row.get("status") or "") for row in rows)
    reused = sum(1 for row in rows if _evidence(row).get("reused"))
    auto_same = sum(1 for row in rows if _evidence(row).get("auto_same"))
    critic_split = sum(1 for row in rows if _evidence(row).get("critic_action") == "SPLIT")
    critic_uphold = sum(1 for row in rows if _evidence(row).get("critic_upheld"))
    critic_reclassify_same = sum(
        1 for row in rows
        if _evidence(row).get("critic_action") == "RECLASSIFY" and str(row.get("label") or "") == "SAME"
    )
    # False-merge proxy: weak/over-eager SAME that did not stand (REJECTED) or Critic SPLIT.
    false_merge = sum(
        1 for row in rows
        if str(row.get("status") or "") == "REJECTED"
        or _evidence(row).get("critic_action") == "SPLIT"
    )
    applied_same = sum(
        1 for row in rows
        if row.get("status") == "VALIDATED"
        and applies_same(
            str(row.get("label") or ""),
            float(row.get("confidence") or 0),
            critic_upheld=bool(_evidence(row).get("critic_upheld")),
        )
    )
    insufficient = by_label.get("INSUFFICIENT", 0)
    total = len(rows)
    return {
        "relations": total,
        "by_label": dict(sorted(by_label.items())),
        "by_status": dict(sorted(by_status.items())),
        "reused": reused,
        "reuse_rate": _rate(reused, total),
        "auto_same": auto_same,
        "applied_same": applied_same,
        "insufficient": insufficient,
        "critic": {
            "split": critic_split,
            "uphold": critic_uphold,
            "reclassify_to_same": critic_reclassify_same,
        },
        "false_merge": {
            "count": false_merge,
            "rate": _rate(false_merge, applied_same + false_merge),
            "note": (
                "False-merge proxy: REJECTED SAME or Critic SPLIT. "
                "R&A reviews whether recall heuristics over-merged."
            ),
        },
        "missed_merge": {
            "count": critic_reclassify_same,
            "rate": _rate(critic_reclassify_same, total),
            "note": (
                "Missed-merge proxy: Critic RECLASSIFY to SAME after a weaker proposal. "
                "Near-miss instrumentation (cosine 0.82–0.90) remains the complementary signal."
            ),
        },
        "ambiguous_band": {"lo": AMBIGUOUS_LO, "hi": AMBIGUOUS_HI, "t_same": T_SAME},
    }


def learning_report(
    pool: Pool,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Full relation-learning dashboard payload. Never applies changes."""
    if run_id:
        rows = pool.event_relations_for_run(run_id, validated_only=False)
    else:
        rows = pool.event_relations_latest(validated_only=False)
    outcomes = relation_outcome_stats(rows)
    return {
        "function": FUNCTION_NAME,
        "version": FUNCTION_VERSION,
        "scope": {"run_id": run_id},
        "outcomes": outcomes,
        "apply": False,
        "note": (
            "Event-relation analytics for R&A. False-merge and missed-merge rates are proxies from "
            "Critic actions and REJECTED SAME rows. Nothing in this report changes a relation, band, "
            "override, or challenge."
        ),
    }


def format_learning_report(report: Mapping[str, Any]) -> str:
    """Human-readable relation learning report."""
    outcomes = report.get("outcomes") or {}
    false_merge = outcomes.get("false_merge") or {}
    missed = outcomes.get("missed_merge") or {}
    critic = outcomes.get("critic") or {}
    lines = [
        f"{report.get('function')} v{report.get('version')}",
        f"Scope: run_id={((report.get('scope') or {}).get('run_id') or 'all')}",
        f"Relations (latest standing): {outcomes.get('relations', 0)}",
        f"Applied SAME: {outcomes.get('applied_same', 0)}; auto-SAME: {outcomes.get('auto_same', 0)}; "
        f"reused: {outcomes.get('reused', 0)} (rate {outcomes.get('reuse_rate')})",
        f"False-merge proxy: {false_merge.get('count', 0)} (rate {false_merge.get('rate')})",
        f"Missed-merge proxy: {missed.get('count', 0)} (rate {missed.get('rate')})",
        f"Critic: split={critic.get('split', 0)} uphold={critic.get('uphold', 0)} "
        f"reclassify_to_same={critic.get('reclassify_to_same', 0)}",
        f"By label: {outcomes.get('by_label') or {}}",
        f"By status: {outcomes.get('by_status') or {}}",
        str(report.get("note") or ""),
    ]
    return "\n".join(lines)
