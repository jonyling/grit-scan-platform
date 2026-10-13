"""Group-pack drafts for human review (spec 37): the baseline for Wilson's sign-off, and proposed scenarios.

Both read a completed run and write to output/runs/RUN/. Neither changes a register: a person edits the draft and saves it
under config/ste/.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from grit.pipeline.reader_scan import (
    ReaderRun,
    _cite,
    _kept,
    _load_pack,
    business_area_context,
    condition_sources,
    materiality_order,
)
from grit.pool import Pool

MAX_SHIFT_SOURCES = 12
REVIEW_NOTE = "Drafted by GRIT-Scan from cited evidence. Not reviewed. A named person edits and signs it off before it is used."


def _refs(pool: Pool, signal_ids: Sequence[str]) -> list[dict[str, Any]]:
    from grit.report.brief import fmt_when

    refs = []
    for signal_id in dict.fromkeys(signal_ids):
        signal = pool.signal(signal_id)
        if signal:
            refs.append({"signal_id": signal_id, "outlet": signal.get("publisher"), "title": signal.get("title"), "url": signal.get("url"), "when": fmt_when(signal.get("published_at"))})
    return refs


def _plain(pool: Pool, text: str) -> str:
    """Citation marks as '(Outlet, date)', for a draft a person edits by hand."""
    from grit.report.brief import citations, plain

    ids = set(re.findall(r"SIG-[0-9a-f]{16}", text or ""))
    signals = {signal_id: pool.signal(signal_id) for signal_id in ids}
    runs, _ = citations(text, {key: value for key, value in signals.items() if value})
    return plain(runs)


def draft_baseline(pool: Pool, run: ReaderRun, *, client: Any, model: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The Baseliner drafts each standing condition's current position from the run's most material sources."""
    from grit.agents import reader as agents

    pack = _load_pack(run.pack)
    conditions = list((pack.baseline or {}).get("conditions") or [])
    if not conditions:
        raise ValueError(f"Pack {run.pack} has no standing conditions to draft a baseline for.")
    rows = _kept(pool, run)
    drafted = []
    for condition in conditions:
        sources = condition_sources(rows, condition["id"], theme=True)
        table, back = agents.source_table(sources)
        result, call = agents.draft_baseline(client, model, condition=condition, sources=table)
        pool.log(actor_type="AGENT", run_id=run.run_id, **call.as_record())
        log(f"  Baseliner {condition['id']:12} {result['status']:12} sources {len(table)}")
        cited = [back[ref] for ref in result["source_ids"] if ref in back]
        position = _plain(pool, _cite(result["position"], back)) if result.get("position") else None
        readings = [{"indicator": item["indicator"], "reading": _plain(pool, _cite(item["reading"], back))} for item in result.get("indicators") or []]
        drafted.append({
            "id": condition["id"], "name": condition["name"], "status": result["status"], "currentAssessment": position, "indicatorReadings": readings,
            "gaps": result.get("gaps"), "sources": _refs(pool, cited), "violations": result.get("violations") or [],
        })
    document = {
        "baselineId": (pack.baseline or {}).get("baselineId"), "pack": run.pack, "run": run.run_id, "draftedOn": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "DRAFT_FOR_SIGN_OFF", "note": REVIEW_NOTE,
        "instructions": "Edit each currentAssessment, then copy it into the pack's baseline register. Set the register's status to SIGNED_OFF with signedOffBy and signedOffOn.",
        "conditions": drafted,
    }
    folder = run.folder / "baseline-draft"
    paths = _write(folder, f"{run.pack.upper()}-Baseline-Draft_{run.run_date.isoformat()}", document, _baseline_docx)
    counts = {status: sum(1 for item in drafted if item["status"] == status) for status in ("DRAFTED", "NO_EVIDENCE", "WITHHELD")}
    pool.log(actor_type="FUNCTION", actor="BaselineDraft", status="SUCCEEDED", run_id=run.run_id, version="baseline-draft-1", detail={**counts, "files": paths})
    return {**counts, "files": paths}


def propose_scenarios(pool: Pool, run: ReaderRun, *, client: Any, model: str, log: Callable[[str], None] = print) -> dict[str, Any]:
    """The Strategist proposes scenarios for each strategic shift, from the run's signals that hit it strongly."""
    from grit.agents import reader as agents

    pack = _load_pack(run.pack)
    shifts = list((pack.themes or {}).get("themes") or [])
    area = business_area_context(pack)
    rows = _kept(pool, run)
    latest = {row["condition_id"]: row for row in pool.conditions(run.run_id)}
    proposed = []
    for shift in shifts:
        chosen = [row for row in rows if any(hit.get("theme_id") == shift["themeId"] and hit.get("strong") for hit in row.get("themes") or [])]
        table, back = agents.source_table(materiality_order(chosen)[:MAX_SHIFT_SOURCES])
        touched = [
            {"id": condition["id"], "name": condition["name"], "trend": (latest.get(condition["id"]) or {}).get("trend_label"),
             "assessment": agents.strip_marks(str((latest.get(condition["id"]) or {}).get("summary") or ""))[:600] or None}
            for condition in (pack.baseline or {}).get("conditions") or [] if shift["themeName"] in (condition.get("steThemes") or [])
        ]
        result, call = agents.propose_scenarios(client, model, shift=shift, business_area=area, conditions=touched, sources=table)
        pool.log(actor_type="AGENT", run_id=run.run_id, **call.as_record())
        log(f"  Strategist {shift['themeId']:14} {result['status']:12} scenarios {len(result['scenarios'])}  sources {len(table)}")
        scenarios = [{**item, "sources": _refs(pool, [back[ref] for ref in item.get("source_ids") or [] if ref in back])} for item in result["scenarios"]]
        for item in scenarios:
            item.pop("source_ids", None)
        proposed.append({"themeId": shift["themeId"], "name": shift["themeName"], "status": result["status"], "scenarios": scenarios, "violations": result.get("violations") or []})
    document = {
        "registerId": f"{run.pack.upper()}-SCENARIOS", "pack": run.pack, "run": run.run_id, "proposedOn": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "PROPOSED_FOR_REVIEW", "note": REVIEW_NOTE,
        "instructions": "Edit, merge or delete scenarios, then save the file as the pack's STE-Scenarios.json with status REVIEWED, reviewedBy and reviewedOn.",
        "shifts": proposed,
    }
    folder = run.folder / "scenarios"
    paths = _write(folder, f"{run.pack.upper()}-Scenarios-Proposed_{run.run_date.isoformat()}", document, _scenarios_docx)
    counts = {"shifts": len(proposed), "scenarios": sum(len(item["scenarios"]) for item in proposed), "withheld": sum(1 for item in proposed if item["status"] == "WITHHELD")}
    pool.log(actor_type="FUNCTION", actor="ScenarioProposal", status="SUCCEEDED", run_id=run.run_id, version="scenarios-1", detail={**counts, "files": paths})
    return {**counts, "files": paths}


def _write(folder: Path, stem: str, document: Mapping[str, Any], render: Callable[[Mapping[str, Any], Path], None]) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    json_path, docx_path = folder / f"{stem}.json", folder / f"{stem}.docx"
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    render(document, docx_path)
    return {"json": str(json_path), "docx": str(docx_path)}


def _doc(title: str, document: Mapping[str, Any]) -> Any:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.add_heading(title, level=0)
    note = doc.add_paragraph()
    run = note.add_run(f"Status: {document['status']}. {document['note']} Run {document['run']}.")
    run.italic, run.font.size = True, Pt(9)
    doc.add_paragraph(document["instructions"])
    return doc


def _sources(doc: Any, refs: Sequence[Mapping[str, Any]]) -> None:
    from grit.report.docx_brief import _hyperlink

    for ref in refs:
        paragraph = doc.add_paragraph(style="List Number")
        if ref.get("url"):
            _hyperlink(paragraph, str(ref.get("outlet") or ref["url"]), str(ref["url"]))
        paragraph.add_run(f": {ref.get('title') or ''} ({ref.get('when') or 'date not stated'})")


def _baseline_docx(document: Mapping[str, Any], path: Path) -> None:
    doc = _doc("Group baseline: draft for sign-off", document)
    for item in document["conditions"]:
        doc.add_heading(f"{item['id']}  {item['name']}", level=1)
        doc.add_paragraph(f"Draft status: {item['status']}.")
        doc.add_paragraph(item["currentAssessment"] or "Withheld: the draft failed its checks. " + "; ".join(item["violations"][:2]))
        for reading in item["indicatorReadings"]:
            doc.add_paragraph(f"{reading['indicator']}: {reading['reading']}", style="List Bullet")
        if item.get("gaps"):
            doc.add_paragraph(f"Not covered by the sources: {item['gaps']}")
        _sources(doc, item["sources"])
        doc.add_paragraph("Signed off by: ____________________    Date: ____________")
    doc.save(str(path))


def _scenarios_docx(document: Mapping[str, Any], path: Path) -> None:
    doc = _doc("Strategic shifts: proposed scenarios", document)
    for shift in document["shifts"]:
        doc.add_heading(f"{shift['themeId']}  {shift['name']}", level=1)
        if not shift["scenarios"]:
            doc.add_paragraph("No scenario proposed: " + ("; ".join(shift["violations"][:2]) or "no source in this run hits the shift strongly."))
        for item in shift["scenarios"]:
            doc.add_heading(f"{item['name']} ({item['horizon']})", level=2)
            doc.add_paragraph(item["narrative"])
            doc.add_paragraph("Signposts:")
            for text in item.get("signposts") or []:
                doc.add_paragraph(text, style="List Bullet")
            doc.add_paragraph("Counter-indicators:")
            for text in item.get("counter_indicators") or []:
                doc.add_paragraph(text, style="List Bullet")
            doc.add_paragraph(f"Risk for STE: {item['risk_for_ste']}")
            if item.get("opportunity_for_ste"):
                doc.add_paragraph(f"Opportunity for STE: {item['opportunity_for_ste']}")
            _sources(doc, item["sources"])
            doc.add_paragraph("Reviewer decision: keep / edit / reject    Reviewer: ____________")
    doc.save(str(path))
