"""P3 learning loop: challenge / override analytics and score→band tuning proposals.

Reads append-only band_challenges and assessment override fields. Never moves a band,
never edits score→band thresholds, and never invents workflow rows.

Governance: agents propose challenges; grit/core decides band; a named human resolves;
R&A authorizes any threshold or override-rule change after reviewing a proposal file.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from grit.core.challenges import (
    MISS_KIND_BAND_RULE_GAP,
    STATUS_ACCEPT_RULE_GAP,
    STATUS_DISMISS,
    STATUS_OPEN,
    STATUS_UPHOLD,
)
from grit.core.overrides import (
    BAND_ELEVATED,
    BAND_ESCALATING,
    BAND_WATCH,
    BANDS,
    OVERRIDE_REG_IN_FORCE,
    OVERRIDE_STAFF_THREAT,
    band_from_tier,
    load_score_band_thresholds,
    reader_band_of,
)
from grit.pool import Pool

FUNCTION_NAME = "BandLearningReport"
FUNCTION_VERSION = "1.0.0"

# Score-only band placement (no hard override) — used for false-precision and tuning clusters.
SCORE_BASIS = "SCORE"
BASIS_KEYS = (OVERRIDE_STAFF_THREAT, OVERRIDE_REG_IN_FORCE, SCORE_BASIS)

# Adjacent-band pairs that often signal cutoff noise rather than a hard miss.
_ADJACENT = frozenset({
    (BAND_ESCALATING, BAND_ELEVATED),
    (BAND_ELEVATED, BAND_ESCALATING),
    (BAND_ELEVATED, BAND_WATCH),
    (BAND_WATCH, BAND_ELEVATED),
})


def _basis(row: Mapping[str, Any]) -> str:
    rule = row.get("override_rule_id")
    if rule in (OVERRIDE_STAFF_THREAT, OVERRIDE_REG_IN_FORCE):
        return str(rule)
    return SCORE_BASIS


def _priority(row: Mapping[str, Any]) -> Mapping[str, Any]:
    priority = row.get("priority") if isinstance(row.get("priority"), Mapping) else {}
    return priority


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def latest_challenges(pool: Pool, *, pack: str | None = None, run_id: str | None = None) -> list[dict[str, Any]]:
    """Latest standing status per challenge_id, optionally scoped to a pack or run."""
    return pool.band_challenges_latest(pack=pack, run_id=run_id)


def override_hit_stats(pool: Pool, *, pack: str | None = None, run_id: str | None = None) -> dict[str, Any]:
    """Override hit rate from assessments (priority.override_rule_id), scoped like challenges."""
    rows = pool.assessment_band_rows(pack=pack, run_id=run_id)
    by_rule: Counter[str] = Counter()
    kept = 0
    with_override = 0
    by_band: Counter[str] = Counter()
    for row in rows:
        kept += 1
        priority = _priority(row)
        band = reader_band_of(row)
        by_band[band] += 1
        rule = priority.get("override_rule_id")
        if rule:
            with_override += 1
            by_rule[str(rule)] += 1
    return {
        "assessments": kept,
        "override_hits": with_override,
        "override_hit_rate": _rate(with_override, kept),
        "by_rule": {rule: by_rule[rule] for rule in sorted(by_rule)},
        "by_band": {band: by_band.get(band, 0) for band in BANDS},
    }


def challenge_outcome_stats(
    challenges: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Challenge rate by placement basis (override rule or SCORE) and outcome mix."""
    total = len(challenges)
    by_status: Counter[str] = Counter(str(row.get("status") or "") for row in challenges)
    by_basis: dict[str, dict[str, int]] = {
        key: {"total": 0, STATUS_OPEN: 0, STATUS_UPHOLD: 0, STATUS_ACCEPT_RULE_GAP: 0, STATUS_DISMISS: 0}
        for key in BASIS_KEYS
    }
    transitions: Counter[str] = Counter()
    adjacent_score = 0
    for row in challenges:
        basis = _basis(row)
        bucket = by_basis.setdefault(
            basis,
            {"total": 0, STATUS_OPEN: 0, STATUS_UPHOLD: 0, STATUS_ACCEPT_RULE_GAP: 0, STATUS_DISMISS: 0},
        )
        bucket["total"] += 1
        status = str(row.get("status") or "")
        if status in bucket:
            bucket[status] += 1
        core = str(row.get("band") or "")
        challenged = str(row.get("challenged_band") or "")
        transitions[f"{core}→{challenged}"] += 1
        if basis == SCORE_BASIS and (core, challenged) in _ADJACENT:
            adjacent_score += 1
    resolved = (
        by_status.get(STATUS_UPHOLD, 0)
        + by_status.get(STATUS_ACCEPT_RULE_GAP, 0)
        + by_status.get(STATUS_DISMISS, 0)
    )
    return {
        "challenges": total,
        "by_status": {
            STATUS_OPEN: by_status.get(STATUS_OPEN, 0),
            STATUS_UPHOLD: by_status.get(STATUS_UPHOLD, 0),
            STATUS_ACCEPT_RULE_GAP: by_status.get(STATUS_ACCEPT_RULE_GAP, 0),
            STATUS_DISMISS: by_status.get(STATUS_DISMISS, 0),
        },
        "resolved": resolved,
        "uphold_rate": _rate(by_status.get(STATUS_UPHOLD, 0), resolved),
        "rule_gap_rate": _rate(by_status.get(STATUS_ACCEPT_RULE_GAP, 0), resolved),
        "dismiss_rate": _rate(by_status.get(STATUS_DISMISS, 0), resolved),
        "by_basis": by_basis,
        "transitions": dict(sorted(transitions.items(), key=lambda item: (-item[1], item[0]))),
        "false_precision": {
            "adjacent_score_challenges": adjacent_score,
            "adjacent_score_share_of_challenges": _rate(adjacent_score, total),
            "note": (
                "Adjacent score-based challenges (Escalating↔Elevated or Elevated↔Watch with no override) "
                "are a false-precision regression signal: readers argue the cutoff, not a hard miss."
            ),
        },
    }


def tuning_signals(challenges: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Surfaced clusters from UPHOLD vs ACCEPT_RULE_GAP for score→band review.

    ACCEPT_RULE_GAP on score-based rows → consider moving the cutoff toward the challenged band.
    UPHOLD on the same transition → current cutoff is defended; do not loosen that edge.
    Never applies a change; returns review notes only.
    """
    gaps: Counter[tuple[str, str]] = Counter()
    upholds: Counter[tuple[str, str]] = Counter()
    for row in challenges:
        if _basis(row) != SCORE_BASIS:
            continue
        core = str(row.get("band") or "")
        challenged = str(row.get("challenged_band") or "")
        if core not in BANDS or challenged not in BANDS:
            continue
        key = (core, challenged)
        status = row.get("status")
        if status == STATUS_ACCEPT_RULE_GAP:
            gaps[key] += 1
        elif status == STATUS_UPHOLD:
            upholds[key] += 1

    thresholds = load_score_band_thresholds()
    tier = thresholds.get("tier") if isinstance(thresholds.get("tier"), Mapping) else {}
    escalating_max = int(tier.get("escalating_max", 1))
    elevated_max = int(tier.get("elevated_max", 3))

    proposals: list[dict[str, Any]] = []
    for (core, challenged), gap_count in sorted(gaps.items(), key=lambda item: (-item[1], item[0])):
        uphold_count = upholds.get((core, challenged), 0)
        note = (
            f"{gap_count} ACCEPT_RULE_GAP vs {uphold_count} UPHOLD on score-based {core}→{challenged}. "
            "R&A should review score→band cutoffs; this report does not change them."
        )
        suggestion: dict[str, Any] = {
            "core_band": core,
            "challenged_band": challenged,
            "accept_rule_gap": gap_count,
            "uphold": uphold_count,
            "net_gap": gap_count - uphold_count,
            "note": note,
            "apply": False,
        }
        # Concrete, reviewable hints for the common adjacent edges only.
        if core == BAND_WATCH and challenged == BAND_ELEVATED and gap_count > uphold_count:
            suggestion["proposed_tier"] = {
                "escalating_max": escalating_max,
                "elevated_max": min(4, elevated_max + 1),
                "rationale": "Watch→Elevated rule gaps dominate: consider raising elevated_max so more mid tiers land Elevated.",
            }
        elif core == BAND_ELEVATED and challenged == BAND_ESCALATING and gap_count > uphold_count:
            suggestion["proposed_tier"] = {
                "escalating_max": min(elevated_max, escalating_max + 1),
                "elevated_max": elevated_max,
                "rationale": "Elevated→Escalating rule gaps dominate: consider raising escalating_max so more top scores land Escalating.",
            }
        elif core == BAND_ESCALATING and challenged == BAND_ELEVATED and gap_count > uphold_count:
            suggestion["proposed_tier"] = {
                "escalating_max": max(0, escalating_max - 1) if escalating_max > 0 else 0,
                "elevated_max": elevated_max,
                "rationale": "Escalating→Elevated rule gaps dominate: consider lowering escalating_max so fewer mid scores land Escalating.",
            }
        elif core == BAND_ELEVATED and challenged == BAND_WATCH and gap_count > uphold_count:
            suggestion["proposed_tier"] = {
                "escalating_max": escalating_max,
                "elevated_max": max(escalating_max, elevated_max - 1),
                "rationale": "Elevated→Watch rule gaps dominate: consider lowering elevated_max so weaker mid scores land Watch.",
            }
        proposals.append(suggestion)

    # Upholds with no competing gaps: confirm the edge is healthy.
    for (core, challenged), uphold_count in sorted(upholds.items(), key=lambda item: (-item[1], item[0])):
        if gaps.get((core, challenged), 0) > 0:
            continue
        if uphold_count < 1:
            continue
        proposals.append({
            "core_band": core,
            "challenged_band": challenged,
            "accept_rule_gap": 0,
            "uphold": uphold_count,
            "net_gap": -uphold_count,
            "note": (
                f"{uphold_count} UPHOLD with no ACCEPT_RULE_GAP on score-based {core}→{challenged}: "
                "current cutoff defended; no change proposed."
            ),
            "apply": False,
        })
    return proposals


def learning_report(
    pool: Pool,
    *,
    pack: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Full P3 dashboard payload: challenge outcomes, override hit rate, tuning proposals."""
    challenges = latest_challenges(pool, pack=pack, run_id=run_id)
    overrides = override_hit_stats(pool, pack=pack, run_id=run_id)
    outcomes = challenge_outcome_stats(challenges)
    assessments = overrides["assessments"]
    challenge_rate = _rate(outcomes["challenges"], assessments)
    rule_gap_misses = []
    if pack:
        rule_gap_misses = [
            {"miss_id": row["miss_id"], "text": row["text"], "recorded_by": row["recorded_by"],
             "recorded_at": row.get("recorded_at"), "signal_id": row.get("signal_id")}
            for row in pool.misses(pack)
            if row.get("kind") == MISS_KIND_BAND_RULE_GAP
        ]
    else:
        # Cross-pack: scan known packs from challenge runs.
        packs = sorted({
            str((pool.run(row["run_id"]) or {}).get("pack") or "")
            for row in challenges
            if row.get("run_id")
        } - {""})
        for name in packs:
            for row in pool.misses(name):
                if row.get("kind") == MISS_KIND_BAND_RULE_GAP:
                    rule_gap_misses.append({
                        "pack": name,
                        "miss_id": row["miss_id"],
                        "text": row["text"],
                        "recorded_by": row["recorded_by"],
                        "recorded_at": row.get("recorded_at"),
                        "signal_id": row.get("signal_id"),
                    })
    proposals = tuning_signals(challenges)
    thresholds = load_score_band_thresholds()
    return {
        "function": FUNCTION_NAME,
        "version": FUNCTION_VERSION,
        "scope": {"pack": pack, "run_id": run_id},
        "challenge_rate": challenge_rate,
        "overrides": overrides,
        "outcomes": outcomes,
        "current_thresholds": {
            "tier": thresholds.get("tier"),
            "significance": thresholds.get("significance"),
        },
        "tuning_proposals": proposals,
        "rule_gap_misses": rule_gap_misses,
        "apply": False,
        "note": (
            "Bands from overrides and scores; order within band is model-proposed. "
            "Challenge outcomes guide R&A rule edits. Nothing in this report changes a band or a threshold."
        ),
    }


def propose_threshold_changes(report: Mapping[str, Any]) -> dict[str, Any]:
    """Review file shaped like misses propose: suggested threshold edits with apply=False."""
    current = report.get("current_thresholds") or {}
    tier = current.get("tier") if isinstance(current.get("tier"), Mapping) else {}
    proposed_tier = {
        "escalating_max": int(tier.get("escalating_max", 1)),
        "elevated_max": int(tier.get("elevated_max", 3)),
    }
    notes: list[str] = []
    # Prefer the strongest net_gap proposal that carries a concrete tier suggestion.
    ranked = sorted(
        (row for row in report.get("tuning_proposals") or [] if isinstance(row, Mapping) and row.get("proposed_tier")),
        key=lambda row: (-int(row.get("net_gap") or 0), str(row.get("core_band")), str(row.get("challenged_band"))),
    )
    if ranked:
        tip = ranked[0]["proposed_tier"]
        proposed_tier = {
            "escalating_max": int(tip["escalating_max"]),
            "elevated_max": int(tip["elevated_max"]),
        }
        notes.append(str(tip.get("rationale") or ranked[0].get("note") or ""))
    for row in report.get("tuning_proposals") or []:
        if isinstance(row, Mapping) and row.get("note"):
            notes.append(str(row["note"]))
    return {
        "proposed_score_band_thresholds": {
            "tier": proposed_tier,
            "significance": current.get("significance") or {
                "TOP": BAND_ESCALATING,
                "SIGNIFICANT": BAND_ELEVATED,
                "SECONDARY": BAND_WATCH,
                "LOW": BAND_WATCH,
            },
        },
        "proposed_prompt_notes": notes[:40],
        "apply": False,
        "note": (
            "Review this file. Nothing in the repository was changed. "
            "Accept a threshold edit only by updating config/score-band-thresholds.json as R&A."
        ),
    }


def format_learning_report(report: Mapping[str, Any]) -> str:
    """Plain-text dashboard for CLI / UI caption."""
    scope = report.get("scope") or {}
    scope_bits = []
    if scope.get("pack"):
        scope_bits.append(f"pack={scope['pack']}")
    if scope.get("run_id"):
        scope_bits.append(f"run={scope['run_id']}")
    scope_label = ", ".join(scope_bits) if scope_bits else "all packs"
    overrides = report.get("overrides") or {}
    outcomes = report.get("outcomes") or {}
    by_status = outcomes.get("by_status") or {}
    lines = [
        f"Band learning report ({scope_label})",
        f"Challenge rate: {report.get('challenge_rate')} "
        f"({outcomes.get('challenges', 0)} challenges / {overrides.get('assessments', 0)} assessments)",
        f"Override hit rate: {overrides.get('override_hit_rate')} "
        f"({overrides.get('override_hits', 0)} / {overrides.get('assessments', 0)})",
        "Override hits by rule: "
        + (", ".join(f"{k}={v}" for k, v in (overrides.get("by_rule") or {}).items()) or "none"),
        "Challenge outcomes: "
        + ", ".join(f"{k}={by_status.get(k, 0)}" for k in (STATUS_OPEN, STATUS_UPHOLD, STATUS_ACCEPT_RULE_GAP, STATUS_DISMISS)),
        f"Uphold / rule-gap / dismiss rates (of resolved): "
        f"{outcomes.get('uphold_rate')} / {outcomes.get('rule_gap_rate')} / {outcomes.get('dismiss_rate')}",
    ]
    false_precision = outcomes.get("false_precision") or {}
    lines.append(
        f"False-precision (adjacent score challenges): {false_precision.get('adjacent_score_challenges', 0)} "
        f"(share {false_precision.get('adjacent_score_share_of_challenges')})"
    )
    by_basis = outcomes.get("by_basis") or {}
    for key in BASIS_KEYS:
        bucket = by_basis.get(key) or {}
        if not bucket.get("total"):
            continue
        lines.append(
            f"  {key}: total={bucket.get('total', 0)} "
            f"OPEN={bucket.get(STATUS_OPEN, 0)} UPHOLD={bucket.get(STATUS_UPHOLD, 0)} "
            f"ACCEPT_RULE_GAP={bucket.get(STATUS_ACCEPT_RULE_GAP, 0)} DISMISS={bucket.get(STATUS_DISMISS, 0)}"
        )
    proposals = report.get("tuning_proposals") or []
    if proposals:
        lines.append("Score→band tuning signals (review only; apply=False):")
        for row in proposals[:12]:
            lines.append(f"  - {row.get('note')}")
    misses = report.get("rule_gap_misses") or []
    if misses:
        lines.append(f"BAND_RULE_GAP misses on backlog: {len(misses)}")
    lines.append(str(report.get("note") or ""))
    return "\n".join(lines)


def verify_score_band_roundtrip() -> None:
    """Sanity for tests: default thresholds reproduce the P0 band_from_tier mapping."""
    assert band_from_tier(1) == BAND_ESCALATING
    assert band_from_tier(2) == BAND_ELEVATED
    assert band_from_tier(3) == BAND_ELEVATED
    assert band_from_tier(4) == BAND_WATCH
