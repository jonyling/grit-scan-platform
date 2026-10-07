"""Screen actions. Each one calls a governed function. ReviewGate() decides a package. The Supervisor writes the ledger.

This module writes no workflow row itself, and it never edits a shared signal.
"""

from __future__ import annotations

from typing import Any

from grit.pool import Pool, utc_now
from grit.profiles import Profile
from grit.workflow.packages import review_package


def record_feedback(pool: Pool, profile: Profile, signal_id: str, decision: str, rationale: str | None) -> None:
    pool.add_feedback(profile.profile_id, signal_id, decision, rationale or None)


def review(pool: Pool, profile: Profile, package_id: str, version: int, intent: str) -> dict[str, Any]:
    """ReviewGate() through the Supervisor. A reader-only profile is refused before the gate."""
    return review_package(pool, package_id=package_id, version=version, reviewer=profile, intent=intent)


def set_request_status(pool: Pool, profile: Profile, request_id: str, status: str) -> None:
    if profile.review_role not in {"REVIEWER", "BA_HEAD"}:
        raise PermissionError(f"{profile.label} cannot change a management request.")
    if status not in {"ACTIVE", "ON_HOLD", "CLOSED", "FULFILLED"}:
        raise ValueError(status)
    with pool.transaction() as conn:
        conn.execute("UPDATE management_requests SET status = ? WHERE request_id = ?", (status, request_id))
    pool.log(actor_type="FUNCTION", actor="ManagementRequestGate", status=status, subject=request_id, detail={"by": profile.profile_id})


def decide_alert(pool: Pool, profile: Profile, alert_id: str, status: str) -> None:
    """A named person records that they sent or dismissed a RED draft. Code never sends it."""
    if status not in {"SENT_BY_HUMAN", "DISMISSED"}:
        raise ValueError(status)
    with pool.transaction() as conn:
        conn.execute("UPDATE alerts SET status = ?, decided_by = ?, decided_at = ? WHERE alert_id = ?", (status, profile.profile_id, utc_now(), alert_id))
