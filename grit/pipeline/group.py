"""Group-pack drafts for human review (spec 37): the baseline for Wilson's sign-off, and the best, medium and worst case
for STE under each strategic shift (spec 43).

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
    owner_roles,
)
from grit.pool import Pool

MAX_SHIFT_SOURCES = 8  # news signals per shift; the State of the Situation adds its report passages
CASE_TITLES = {"BEST": "Best case for STE", "MEDIUM": "Medium case for STE", "WORST": "Worst case for STE"}
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


def situation_for(shift_id: str, sots_dir: Path | None = None) -> dict[str, Any] | None:
    """The latest State of the Situation document whose topic covers the shift, or None."""
    from grit.library.catalogue import TOPICS
    from grit.settings import OUTPUT_DIR

    root = sots_dir or OUTPUT_DIR / "sots"
    topic = next((item for item in TOPICS if shift_id in item.themes), None)
    if topic is None or not root.is_dir():
        return None
    for folder in sorted((path for path in root.iterdir() if path.is_dir()), reverse=True):
        path = folder / f"{topic.topic_id}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def situation_view(document: Mapping[str, Any] | None) -> tuple[dict[str, Any] | None, list[str], dict[str, dict[str, Any]]]:
    """What the Strategist reads: the drafted sections (with [P#] marks), the key uncertainty names, and the passages."""
    if not document:
        return None, [], {}
    sections, uncertainties = {}, []
    for row in document.get("sections") or []:
        if row.get("status") != "DRAFTED" or row["key"] == "HISTORY":
            continue
        draft = row.get("draft") or {}
        if row["key"] == "UNCERTAINTIES":
            uncertainties = [str(entry["uncertainty"]) for entry in draft.get("items") or [] if entry.get("uncertainty")]
        sections[row["title"]] = draft.get("paragraphs") or [
            f"{entry.get('uncertainty') or entry.get('indicator')}: {entry.get('why_it_matters') or entry.get('reading') or 'no reading in the library'}" for entry in draft.get("items") or []
        ]
    passages = {
        key: {"title": f"{row.get('report')}, {row.get('page_label')} {row.get('page')}", "publisher": row.get("publisher"), "aliases": list(row.get("aliases") or []),
              "text": str(row.get("text") or ""), "report_id": row.get("report_id"), "page": row.get("page"), "page_label": row.get("page_label")}
        for key, row in (document.get("passages") or {}).items()
    }
    view = {"name": document.get("name"), "status": "draft, not signed off" if document.get("status") != "SIGNED_OFF" else "signed off", "sections": sections}
    return view, uncertainties, passages


def _evidence(pool: Pool, text: str, back: Mapping[str, str], passages: Mapping[str, Mapping[str, Any]]) -> str:
    """Evidence marks for the reviewer: [S#] becomes (outlet, date), [P#] becomes (report, edition, page)."""
    from grit.agents.reader import _CASE_MARK
    from grit.library.sots import render_text

    def swap(match: re.Match[str]) -> str:
        parts = [part.strip() for part in match.group(1).split(",")]
        signals = [back[ref] for ref in parts if ref in back]
        reports = [ref for ref in parts if ref in passages]
        labels = [f"{ref['outlet']}, {ref['when']}" for ref in _refs(pool, signals)]
        if reports:
            labels.append(render_text(f"[{', '.join(reports)}]", passages).strip("()"))
        return f"({'; '.join(labels)})" if labels else ""

    return re.sub(r"\s+([.,;])", r"\1", _CASE_MARK.sub(swap, text)).strip()


def propose_scenarios(pool: Pool, run: ReaderRun, *, client: Any, model: str, log: Callable[[str], None] = print, sots_dir: Path | None = None,
                      redraft: bool = False) -> dict[str, Any]:
    """The Strategist proposes a best, a medium and a worst case for STE under each strategic shift, from the shift's State
    of the Situation and the run's signals that hit the shift strongly (spec 43). redraft keeps the shifts already proposed
    in this run's draft and redoes only the others."""
    from grit.agents import reader as agents
    from grit.pipeline.scenarios import changes, reviewed_view

    stem = f"{run.pack.upper()}-Scenarios-BMW-Proposed_{run.run_date.isoformat()}"
    kept_shifts: dict[str, dict[str, Any]] = {}
    previous = run.folder / "scenarios" / f"{stem}.json"
    if redraft and previous.is_file():
        kept_shifts = {item["themeId"]: item for item in json.loads(previous.read_text(encoding="utf-8"))["shifts"] if item["status"] == "PROPOSED"}

    pack = _load_pack(run.pack)
    shifts = list((pack.themes or {}).get("themes") or [])
    area = business_area_context(pack)
    owners = owner_roles(pack)
    rows = _kept(pool, run)
    latest = {row["condition_id"]: row for row in pool.conditions(run.run_id)}
    proposed = []
    for shift in shifts:
        if shift["themeId"] in kept_shifts:
            proposed.append(kept_shifts[shift["themeId"]])
            log(f"  Strategist {shift['themeId']:14} KEPT         cases 3  (proposed earlier)")
            continue
        chosen = [row for row in rows if any(hit.get("theme_id") == shift["themeId"] and hit.get("strong") for hit in row.get("themes") or [])]
        table, back = agents.source_table(materiality_order(chosen)[:MAX_SHIFT_SOURCES])
        situation, uncertainties, passages = situation_view(situation_for(shift["themeId"], sots_dir))
        sources = {**table, **passages}
        touched = [
            {"id": condition["id"], "name": condition["name"], "trend": (latest.get(condition["id"]) or {}).get("trend_label"),
             "assessment": agents.strip_marks(str((latest.get(condition["id"]) or {}).get("summary") or ""))[:600] or None}
            for condition in (pack.baseline or {}).get("conditions") or [] if shift["themeName"] in (condition.get("steThemes") or [])
        ]
        reviewed = reviewed_view(pool, run.run_id, pack, shift["themeId"])
        result, call = agents.propose_scenarios(client, model, shift=shift, business_area=area, owners=owners, conditions=touched,
                                                situation=situation, uncertainties=uncertainties, sources=sources, reviewed=reviewed)
        pool.log(actor_type="AGENT", run_id=run.run_id, **call.as_record())
        log(f"  Strategist {shift['themeId']:14} {result['status']:12} cases {len(result['cases'])}  signals {len(table)}  passages {len(passages)}")
        cases = []
        for item in result["cases"]:
            ids = [ref for line in item.get("evidence") or [] for ref in agents._case_marks(line)]
            cases.append({
                **item, "title": CASE_TITLES[item["case"]],
                "evidence": [_evidence(pool, line, back, passages) for line in item.get("evidence") or []],
                "signals": _refs(pool, [back[ref] for ref in ids if ref in back]),
                "reports": [{"report_id": passages[ref]["report_id"], "title": passages[ref]["title"]} for ref in dict.fromkeys(ids) if ref in passages],
            })
        proposed.append({
            "themeId": shift["themeId"], "name": shift["themeName"], "horizon": "1-5 years", "status": result["status"],
            "situation": (situation or {}).get("name"), "builds_on": result.get("builds_on") or [], "cases": cases, "violations": result.get("violations") or [],
            "changes": changes(reviewed, {"cases": cases}) if cases else [],
        })
    document = {
        "registerId": f"{run.pack.upper()}-SCENARIOS", "pack": run.pack, "run": run.run_id, "proposedOn": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "status": "PROPOSED_FOR_REVIEW", "note": REVIEW_NOTE, "pointOfView": "Best and worst are judged for ST Engineering.",
        "instructions": "Edit, merge or reject cases, then save the file as the pack's STE-Scenarios.json with status REVIEWED, reviewedBy and reviewedOn.",
        "shifts": proposed,
    }
    folder = run.folder / "scenarios"
    paths = _write(folder, stem, document, _scenarios_docx)
    counts = {"shifts": len(proposed), "proposed": sum(1 for item in proposed if item["status"] == "PROPOSED"),
              "withheld": sum(1 for item in proposed if item["status"] == "WITHHELD"), "no_evidence": sum(1 for item in proposed if item["status"] == "NO_EVIDENCE")}
    pool.log(actor_type="FUNCTION", actor="ScenarioProposal", status="SUCCEEDED", run_id=run.run_id, version="scenarios-bmw-1", detail={**counts, "files": paths})
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
    doc = _doc("Strategic shifts: best, medium and worst case for STE", document)
    doc.add_paragraph(f"{document['pointOfView']} Horizon: 1 to 5 years. No case is a forecast; signposts show which one is unfolding.")
    for shift in document["shifts"]:
        doc.add_heading(f"{shift['themeId']}  {shift['name']}", level=1)
        if not shift["cases"]:
            doc.add_paragraph("No cases proposed: " + ("; ".join(shift["violations"][:2]) or "no State of the Situation and no source in this run."))
            continue
        if shift.get("situation"):
            doc.add_paragraph(f"Built on the State of the Situation: {shift['situation']}. Key uncertainties: {'; '.join(shift['builds_on']) or 'not named'}.")
        if shift.get("changes"):
            doc.add_paragraph("Changes from the reviewed version:")
            for text in shift["changes"]:
                doc.add_paragraph(text, style="List Bullet")
        for item in shift["cases"]:
            doc.add_heading(f"{item['title']}: {item['name']}", level=2)
            doc.add_paragraph(item["narrative"])
            doc.add_paragraph("Why it is plausible:")
            for text in item.get("evidence") or []:
                doc.add_paragraph(text, style="List Bullet")
            doc.add_paragraph("Signposts:")
            for number, text in enumerate(item.get("signposts") or [], start=1):
                doc.add_paragraph(f"{number}. {text}")
            doc.add_paragraph("Counter-indicators:")
            for text in item.get("counter_indicators") or []:
                doc.add_paragraph(text, style="List Bullet")
            doc.add_paragraph("What it could mean for STE:")
            for text in item.get("impact") or []:
                doc.add_paragraph(text, style="List Bullet")
            if item.get("where_ste_could_gain"):
                doc.add_paragraph(f"Hypothesis: to validate with BAs. {item['where_ste_could_gain']}")
            doc.add_paragraph("Planned responses:")
            for response in item.get("responses") or []:
                doc.add_paragraph(f"{response['action']} Owner: {response['owner']}. Trigger: signpost {response['trigger']}.", style="List Bullet")
            if item.get("signals"):
                doc.add_paragraph("News sources:")
                _sources(doc, item["signals"])
            doc.add_paragraph("Reviewer decision: keep / edit / reject    Reviewer: ____________")
    doc.save(str(path))
