"""AIRIS handoff: the register-linked tier 1-2 members of packages the BA head approved, for import into AIRIS (AI Risk
Intelligence System). Spec 39.

The export is JSON plus an Excel sheet with the same rows. Every row names the approved package version and hash it came
from, the register links, and the source. No score that GRIT-Scan does not compute is added.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from openpyxl import Workbook

from grit.core.canonical import canonical_hash
from grit.pool import Pool, utc_now
from grit.report.office import cell_text
from grit.profiles import Profile
from grit.settings import OUTPUT_DIR
from grit.workflow.packages import airis_ready, hand_off

FIELDS = ("signal_id", "title", "source", "published_at", "url", "business_area", "line_of_business", "named_items", "standing_conditions", "risk_register", "link_rung",
          "evidence_confidence", "harm_verdict", "approved_by", "package_id", "package_version", "package_hash")


def _record(pool: Pool, item: Mapping[str, Any], ba_head: Profile) -> dict[str, Any]:
    row = pool.one("SELECT a.*, s.title, s.url, s.publisher, s.published_at FROM assessments a JOIN signals s ON s.signal_id = a.signal_id WHERE a.signal_id = ? AND a.run_id = ?", (item["signal_id"], item["run_id"])) or {}
    package = pool.one("SELECT * FROM packages WHERE package_id = ? AND version = ?", (item["package_id"], item["version"])) or {}
    return {
        "signal_id": item["signal_id"], "title": row.get("title"), "source": row.get("publisher"), "published_at": row.get("published_at"), "url": row.get("url"),
        "business_area": "Urban Solutions", "line_of_business": (row.get("link") or {}).get("canonical_lob"),
        "named_items": [hit["item_name"] for hit in row.get("exposure") or [] if hit.get("strong")], "standing_conditions": row.get("condition_ids") or [],
        "risk_register": [f"{hit['level']} {hit['risk_id']} {hit['name']}" for hit in (row.get("risk_cascade") or [])[:5]], "link_rung": row.get("rung"),
        "evidence_confidence": row.get("evidence_confidence"), "harm_verdict": row.get("harm_verdict") or "NOT_ESTABLISHED", "approved_by": ba_head.label,
        "package_id": item["package_id"], "package_version": item["version"], "package_hash": package.get("content_hash"),
    }


def export_to_airis(pool: Pool, ba_head: Profile, *, folder: Path | None = None) -> dict[str, Any]:
    items = airis_ready(pool, ba_head)
    if not items:
        return {"exported": 0, "reason": "No package the BA head approved holds a register-linked tier 1-2 signal that has not gone to AIRIS already."}
    records = [_record(pool, item, ba_head) for item in items]
    results = hand_off(pool, ba_head, items)
    allowed = {result["signal_id"] for result in results if result["decision"] in {"ALLOW", "ALREADY_APPLIED"}}
    records = [record for record in records if record["signal_id"] in allowed]
    digest = canonical_hash(records)
    export_id = f"AIRIS-{utc_now()[:10]}-{digest[:8]}"
    folder = folder or OUTPUT_DIR / "airis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{export_id}.json"
    path.write_text(json.dumps({"exportId": export_id, "createdAt": utc_now(), "approvedBy": ba_head.label, "contentHash": digest, "signals": records}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "AIRIS import"
    sheet.append(list(FIELDS))
    for record in records:
        sheet.append([cell_text(", ".join(value) if isinstance(value, list) else value) for value in (record[field] for field in FIELDS)])
    workbook.save(str(path.with_suffix(".xlsx")))
    pool.insert("airis_exports", {"export_id": export_id, "created_at": utc_now(), "approved_by": ba_head.profile_id, "signal_ids_json": [r["signal_id"] for r in records], "path": str(path), "content_hash": digest})
    denied = [result for result in results if result["signal_id"] not in allowed]
    return {"exported": len(records), "path": str(path), "denied": denied}


def preview(pool: Pool, ba_head: Profile) -> Sequence[Mapping[str, Any]]:
    return [_record(pool, item, ba_head) for item in airis_ready(pool, ba_head)]
