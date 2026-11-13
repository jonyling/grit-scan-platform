"""The daily review panel on the dashboard (spec 48). Read-only: decisions are made in SharePoint, not here."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from grit.pool import Pool
from grit.profiles import Profile
from grit.review.batch import decisions, items, steps
from grit.review.config import configured_packs, load_review_config, batch_id
from grit.review.cycle import schedule
from grit.review.learning import learning


def _rate(value: float | None) -> str:
    return "not established" if value is None else f"{value:.0%}"


def panel_data(pool: Pool, profile: Profile, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """For each review this reader takes part in: today's steps, their own stage's decisions, and what GRIT has learnt."""
    out = []
    for pack in configured_packs():
        config = load_review_config(pack)
        stage = next((name for name, item in config.stages.items() if item.profile_id == profile.profile_id), None)
        if stage is None:
            continue
        day = (now or datetime.now(config.zone)).astimezone(config.zone).date()
        batch = batch_id(day, pack)
        done = steps(pool, batch)
        counts: dict[str, int] = {}
        for row in decisions(pool, batch, stage).values():
            counts[row["decision"]] = counts.get(row["decision"], 0) + 1
        target = config.stages[stage]
        out.append({
            "pack": pack,
            "stage": stage,
            "batch": batch,
            "working_day": config.working_day(day),
            "deadline": f"{target.close_at:%H:%M} SGT",
            "list_url": target.list_url,
            "rows": len(items(pool, batch, stage)),
            "decisions": counts,
            "steps": [{"Due": f"{due:%H:%M}", "Step": step, "Status": (done.get(step) or {}).get("status") or "pending"} for step, due, _after in schedule(config)],
            "learning": learning(pool, profile, pack=pack),
        })
    return out


def daily_review_panel(st: Any, pool: Pool, profile: Profile) -> list[dict[str, Any]]:
    data = panel_data(pool, profile)
    st.subheader("Daily signal review")
    if not data:
        st.caption("This reader is not named in any config/review/<pack>.json.")
        return data
    for entry in data:
        stage = "R&A Business Partner" if entry["stage"] == "BP" else "Business Area Manager"
        st.markdown(f"**{entry['pack']}**: you review as the {stage}. Decide by {entry['deadline']}.")
        if not entry["working_day"]:
            st.caption("Today is not a working day. Today's signals join the next working day's review.")
        else:
            counts = entry["decisions"]
            st.write(f"Today: {entry['rows']} signal(s) in your list. Approved {counts.get('APPROVE', 0)}, rejected {counts.get('REJECT', 0)}, "
                     f"no decision by the deadline {counts.get('NO_DECISION', 0)}.")
            if entry["list_url"]:
                st.link_button("Open your review list in SharePoint", entry["list_url"])
            st.markdown("\n".join(f"- {row['Due']} {row['Step']}: {row['Status']}" for row in entry["steps"]))
        report = entry["learning"]
        st.markdown("**What GRIT has learnt from your decisions**")
        upheld = (f"The BA Manager went on to approve {_rate(report['manager_upheld_rate'])} of {report['bp_approvals_seen_by_manager']} R&A BP approvals."
                  if report["bp_approvals_seen_by_manager"] else "The BA Manager has not yet decided on any R&A BP approval.")
        st.write(f"Review days {report['days']}. Decided on time {_rate(report['on_time_rate'])}. Approval rate {_rate(report['approval_rate'])}. {upheld}")
        up, down = st.columns(2)
        up.caption("Moves up")
        up.write("\n".join(f"- {feature} ({weight:+d})" for feature, weight in report["prefers"]) or "Nothing yet.")
        down.caption("Moves down")
        down.write("\n".join(f"- {feature} ({weight:+d})" for feature, weight in report["avoids"]) or "Nothing yet.")
        st.caption("Memory reorders signals within a tier. It never moves a P1 or RED signal down and never hides a row from your list.")
    return data
