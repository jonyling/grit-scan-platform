"""What a daily reviewer's decisions have taught GRIT (spec 48). Read-only: it reports the memory, it does not change it."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from grit import settings
from grit.pool import Pool
from grit.profiles import Profile
from grit.profiles.memory import OUTLET_REJECTIONS_TO_HIDE, build_memory
from grit.review.config import BP, MANAGER


def learning(pool: Pool, profile: Profile, *, pack: str | None = None) -> dict[str, Any]:
    like = f"%-{pack}-review" if pack else "%-review"
    rows = pool.rows("SELECT * FROM review_decisions WHERE profile_id = ? AND final = 1 AND batch_id LIKE ? ORDER BY batch_id",
                     (profile.profile_id, like))
    counts = Counter(row["decision"] for row in rows)
    decided = counts["APPROVE"] + counts["REJECT"]
    pairs = pool.rows(
        """SELECT b.signal_id, b.batch_id, m.decision AS manager FROM review_decisions b
           JOIN review_decisions m ON m.batch_id = b.batch_id AND m.signal_id = b.signal_id AND m.stage = ? AND m.final = 1
           WHERE b.stage = ? AND b.final = 1 AND b.decision = 'APPROVE' AND b.batch_id LIKE ?""",
        (MANAGER, BP, like),
    )
    upheld = sum(1 for row in pairs if row["manager"] == "APPROVE")
    feedback = pool.feedback(profile.profile_id)
    memory = build_memory(profile.profile_id, feedback, pool.latest_assessments(row["signal_id"] for row in feedback))
    outlets = sorted(feature.removeprefix("outlet:") for feature, count in memory.rejected.items()
                     if feature.startswith("outlet:") and count >= OUTLET_REJECTIONS_TO_HIDE and not memory.approved[feature])
    return {
        "profile": profile.label,
        "days": len({row["batch_id"] for row in rows}),
        "approved": counts["APPROVE"],
        "rejected": counts["REJECT"],
        "no_decision": counts["NO_DECISION"],
        "on_time_rate": round(decided / len(rows), 3) if rows else None,
        "approval_rate": round(counts["APPROVE"] / decided, 3) if decided else None,
        "bp_approvals_seen_by_manager": len(pairs),
        "manager_upheld_rate": round(upheld / len(pairs), 3) if pairs else None,
        "memory_signals": memory.size,
        "memory_on": profile.signal_buttons,
        **memory.summary(top=8),
        "outlets_hidden_in_weekly_view": outlets,
    }


def _rate(value: float | None) -> str:
    return "not established" if value is None else f"{value:.0%}"


def render(report: dict[str, Any]) -> str:
    lines = [
        f"# What GRIT has learnt from {report['profile']}",
        "",
        f"- Review days: {report['days']}",
        f"- Approved {report['approved']}, rejected {report['rejected']}, no decision by the deadline {report['no_decision']}",
        f"- Decided on time: {_rate(report['on_time_rate'])}. Approval rate: {_rate(report['approval_rate'])}.",
        f"- R&A BP approvals the BA Manager went on to approve: {_rate(report['manager_upheld_rate'])} of {report['bp_approvals_seen_by_manager']}",
        f"- Signals in memory: {report['memory_signals']}. Memory is {'on' if report['memory_on'] else 'off: set signalReview or signal_buttons'}.",
        "",
        "## Moves up (approved more than rejected)",
        *([f"- {feature} ({weight:+d})" for feature, weight in report["prefers"]] or ["- Nothing yet."]),
        "",
        "## Moves down (rejected more than approved)",
        *([f"- {feature} ({weight:+d})" for feature, weight in report["avoids"]] or ["- Nothing yet."]),
        "",
        f"## Outlets hidden in the weekly view (rejected {OUTLET_REJECTIONS_TO_HIDE} or more times, approved none)",
        *([f"- {name}" for name in report["outlets_hidden_in_weekly_view"]] or ["- None."]),
        "",
        "Memory reorders signals within a priority tier. It never hides a P1 or RED signal, never changes a shared fact or a score, "
        "and never hides a row from the daily review list.",
    ]
    return "\n".join(lines) + "\n"


def write(pool: Pool, profile: Profile, *, pack: str | None = None, root: Path | None = None) -> Path:
    path = (root or settings.OUTPUT_DIR / "review" / "learning") / f"{profile.profile_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(learning(pool, profile, pack=pack)), encoding="utf-8")
    return path
