"""Which runs the screen offers a reader, and which stored report it shows for an incomplete reasoned run. Read-only."""

from __future__ import annotations

from typing import Any

from grit.pool import Pool
from grit.profiles import Profile


def ready_snapshot(pool: Pool, run_id: str, profile_id: str) -> dict[str, Any] | None:
    """The latest READY weekly report for this reader, from any analysis version of the run."""
    return pool.one("SELECT * FROM weekly_reports WHERE run_id = ? AND profile_id = ? AND status = 'READY' "
                    "ORDER BY version DESC, report_id DESC LIMIT 1", (run_id, profile_id))


def listed_runs(pool: Pool, profile: Profile) -> list[dict[str, Any]]:
    """Complete runs, and runs whose current analysis is incomplete but which hold a READY report for this reader."""
    rows = pool.rows(
        """SELECT r.run_id, r.pack, r.status FROM runs r WHERE r.kind != 'DAILY' AND (r.status = 'COMPLETE' OR EXISTS (
             SELECT 1 FROM weekly_reports w WHERE w.run_id = r.run_id AND w.profile_id = ? AND w.status = 'READY'))
           ORDER BY r.run_date DESC, r.pack, r.started_at DESC""", (profile.profile_id,))
    return [row for row in rows if profile.brief_for(row["pack"])]
