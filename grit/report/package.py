"""Write one reader's weekly pack and put it into review.

For each format in the profile (docx, pptx, xlsx), plus Markdown and the JSON brief model, the files go to
output/runs/RUN_ID/PROFILE_ID/vN/, one folder per package version, so an approved version's files never change. The
package then enters the governed workflow as PENDING_REVIEW. An unchanged brief reuses the version already pending.
A changed brief needs the pending version returned for rework first; ReviewGate() is the only way to do that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grit.pool import Pool
from grit.profiles import Profile
from grit.report.brief import build_brief, routed_signals
from grit.report.docx_brief import render_docx
from grit.report.markdown import render_markdown
from grit.report.office import render_pptx, render_xlsx
from grit.settings import OUTPUT_DIR
from grit.workflow.packages import submit_package


def write_pack(pool: Pool, run_id: str, profile: Profile, *, folder: Path | None = None, submit: bool = True) -> dict[str, Any]:
    run = pool.run(run_id) or {}
    brief_config = profile.brief_for(str(run.get("pack")))
    if brief_config is None:
        return {"profile": profile.profile_id, "skipped": f"This profile receives no brief for the {run.get('pack')} pack."}
    reasoned = (run.get("config") or {}).get("weekly_mode") == "reasoned"
    snapshot = None
    if reasoned:
        from grit.pipeline.weekly import frozen_report
        snapshot = frozen_report(pool, run_id, profile.profile_id, ready=submit)
    brief = snapshot["brief"] if snapshot else build_brief(pool, run_id, profile)
    if snapshot and snapshot["status"] != "READY":
        from grit.pipeline.weekly import report_hash
        brief["meta"]["status"] = "INCOMPLETE_PREVIEW"
        brief["bottom_line"]["text"] = "Incomplete analysis: do not treat this preview as a cleared conclusion. " + brief["bottom_line"]["text"]
        brief["bottom_line"]["drafted_by"] = "Reporting; unresolved critique or missing coverage"
        brief["content_hash"] = report_hash(brief)
    package_id = f"PKG-{run_id}-{profile.profile_id}"
    latest = pool.one("SELECT * FROM packages WHERE package_id = ? ORDER BY version DESC LIMIT 1", (package_id,))
    if submit and latest and latest["status"] == "PENDING_REVIEW":
        if latest["membership"].get("brief_hash") == brief["content_hash"]:
            return {"profile": profile.profile_id, "files": latest["files"], "content_hash": brief["content_hash"], "package": {"package_id": package_id, "version": latest["version"], "reused": True}}
        return {"profile": profile.profile_id, "skipped": f"{package_id} v{latest['version']} is still pending review with different content. A reviewer must return it for rework or decide on it first."}
    version = (latest["version"] + 1) if latest else 1
    folder = folder or OUTPUT_DIR / "runs" / run_id / profile.profile_id / f"v{version}"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"GRIT-Scan_{brief['meta']['pack']}_{profile.profile_id}_{brief['meta']['run_date']}"
    files: dict[str, str] = {}
    formats = set(brief_config.get("formats") or ["docx"])
    if "docx" in formats:
        files["docx"] = str(render_docx(brief, folder / f"{stem}.docx"))
    if "pptx" in formats:
        files["pptx"] = str(render_pptx(brief, folder / f"{stem}.pptx"))
    if "xlsx" in formats:
        files["xlsx"] = str(render_xlsx(brief, snapshot["routed"] if snapshot else routed_signals(pool, run_id, profile), folder / f"{stem}.xlsx"))
    (folder / f"{stem}.md").write_text(render_markdown(brief), encoding="utf-8")
    (folder / f"{stem}.brief.json").write_text(json.dumps(brief, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    files["md"] = str(folder / f"{stem}.md")
    result: dict[str, Any] = {"profile": profile.profile_id, "files": files, "content_hash": brief["content_hash"], "members": len(brief["membership"])}
    if submit and brief["membership"]:
        try:
            result["package"] = submit_package(pool, run_id=run_id, profile=profile, members=brief["membership"], brief_hash=brief["content_hash"], files=files)
        except ValueError as exc:
            result["package"] = {"skipped": str(exc)}
    return result
