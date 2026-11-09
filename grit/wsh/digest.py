"""Build and render the monthly WSH discussion digest (spec 47). Skips format v2."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.pool import Pool
from grit.wsh.bulletins import (
    collapse_notes,
    council_line,
    detect_series,
    fill_energy,
    is_scanned_pdf_only,
    route_section,
)
from grit.wsh.precedents import store_from_selection, surface_for_note
from grit.wsh.scope import classify, in_scope, section_for_signal

NOT_ESTABLISHED = "not established"
DISCUSSION_CAP = 4  # full notes per section; the rest go to the log for serious incidents


def _plain(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _source(signal: Mapping[str, Any]) -> dict[str, Any]:
    published = signal.get("published_at")
    if hasattr(published, "isoformat"):
        published = published.isoformat()[:10]
    elif published:
        published = str(published)[:10]
    return {
        "signal_id": signal.get("signal_id"),
        "outlet": signal.get("publisher") or "unknown outlet",
        "title": signal.get("title") or "",
        "url": signal.get("url") or "",
        "date": published or None,
    }


def _note_from_signal(signal: Mapping[str, Any], classification: Mapping[str, Any], section_id: str) -> dict[str, Any]:
    """Deterministic note when no model draft is supplied. Never invents investigation findings or framework labels."""
    text = _plain(signal.get("text") or "")
    title = _plain(signal.get("title") or "")
    industries = [item["label"] for item in classification.get("industries") or []]
    hazards = [item["label"] for item in classification.get("hazards") or []]
    established = title
    if text:
        established = f"{title}. {_plain(text[:400])}"
    place = classification.get("place") or NOT_ESTABLISHED
    industry = industries[0] if industries else NOT_ESTABLISHED
    lesson = NOT_ESTABLISHED
    folded = text.casefold()
    # Affirmative investigation language only. Negations such as "no investigation findings" stay not established.
    affirmed = any(phrase in folded for phrase in (
        "investigation found", "investigation finds", "lessons learnt", "lessons learned", "coroner found", "findings include",
    ))
    denied = any(phrase in folded for phrase in ("no investigation findings", "no lessons", "have not been published", "not been published"))
    if section_id == "SERIOUS" and affirmed and not denied:
        lesson = "Investigation findings or lessons are named in the source; see the citation. No further finding is restated here beyond the source."
    implication = (
        f"This development could be relevant to {industry} operations in Singapore."
        if industry != NOT_ESTABLISHED
        else "A possible implication for a watched industry is not established from this source alone."
    )
    return {
        "title": title,
        "established": established,
        "date": _source(signal)["date"],
        "place": place,
        "industry": industry,
        "hazard": hazards[0] if hazards else NOT_ESTABLISHED,
        "not_established": "Any Energy Wheel, SCL, HECA or HFACS label not stated by the source remains not established.",
        "lesson_or_practice": lesson if section_id == "SERIOUS" else (
            text[:280] if classification.get("is_method") and text else NOT_ESTABLISHED
        ),
        "implication": implication,
        "sources": [_source(signal)],
        "classification": classification,
        "depth": "discussion",
    }


def _log_row(signal: Mapping[str, Any], classification: Mapping[str, Any]) -> dict[str, Any]:
    hazards = [item["label"] for item in classification.get("hazards") or []]
    return {
        "title": _plain(signal.get("title") or ""),
        "date": _source(signal)["date"],
        "place": classification.get("place") or NOT_ESTABLISHED,
        "energy": hazards[0] if hazards else NOT_ESTABLISHED,
        "source": _source(signal),
    }


def _note_from_selection(signal: Mapping[str, Any], classification: Mapping[str, Any], selection: Mapping[str, Any], section_id: str) -> dict[str, Any]:
    """Discussion note built from the Assessment/Critic chain (spec 45 pattern adapted for WSH)."""
    base = _note_from_signal(signal, classification, section_id)
    industry = selection.get("ste_industry") or base.get("industry") or NOT_ESTABLISHED
    series = detect_series(
        signal.get("title") or "", signal.get("text") or "", signal.get("url") or "", signal.get("publisher") or "",
    )
    hazard = fill_energy(classification, selection.get("hazard") or base.get("hazard"))
    controls = selection.get("what_companies_should_do") or NOT_ESTABLISHED
    lesson = controls if controls != NOT_ESTABLISHED else (selection.get("lesson_or_practice") or NOT_ESTABLISHED)
    established = selection.get("what_happened") if selection.get("what_happened") and selection.get("what_happened") != NOT_ESTABLISHED else (
        selection.get("fact") or base["established"]
    )
    note = {
        **base,
        "established": established,
        "industry": industry,
        "hazard": hazard,
        "lesson_or_practice": lesson,
        "implication": selection.get("impact") or base["implication"],
        "exposure": selection.get("exposure") or NOT_ESTABLISHED,
        "transmission": selection.get("transmission") or NOT_ESTABLISHED,
        "uncertainty": selection.get("uncertainty") or NOT_ESTABLISHED,
        "decision": selection.get("decision"),
        "reason": selection.get("reason"),
        "significance": selection.get("significance") or {},
        "ste_relevance": selection.get("reason") or "REVIEWED",
        "relevance_reason": selection.get("reason"),
        "depth": "discussion" if selection.get("decision") == "INCLUDE" else "watch",
        "bulletin_series": series,
        "is_bulletin": bool(series or selection.get("is_bulletin")),
        "what_happened": selection.get("what_happened") or NOT_ESTABLISHED,
        "failed_controls": selection.get("failed_controls") or NOT_ESTABLISHED,
        "earlier_cases": selection.get("earlier_cases") or NOT_ESTABLISHED,
        "what_companies_should_do": controls,
        "instrument": selection.get("instrument") or NOT_ESTABLISHED,
        "investigation_status": selection.get("investigation_status") or NOT_ESTABLISHED,
        "penalty": selection.get("penalty") or NOT_ESTABLISHED,
        "cluster_key": None,
        "prior_bulletins": [],
        "text_not_read": is_scanned_pdf_only(
            signal.get("title") or "", signal.get("text") or "", signal.get("url") or "",
        ),
    }
    from grit.wsh.bulletins import cluster_key

    note["cluster_key"] = cluster_key(
        signal.get("title") or "", signal.get("text") or "", series=series,
    )
    return note


def collect_items(
    pool: Pool,
    run_id: str,
    pack: Any,
    *,
    client: Any | None = None,
    model: str | None = None,
    use_relevance: bool = False,
    use_reasoned: bool = False,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Group items into digest sections.

    - Default / tests: hard STE gate (+ optional single-item relevance).
    - use_reasoned: wide candidate prefilter, then Assessment → Critic (main GRIT-Scan reasoning pattern).
    """
    if use_reasoned:
        return _collect_reasoned(pool, run_id, pack, client=client, model=model, progress=progress)
    scope = pack.scope or {}
    sections = {row["id"]: {"id": row["id"], "title": row["title"], "themeId": row["themeId"], "discussions": [], "log": [], "dropped": []}
                for row in scope.get("sections") or []}
    theme_to_section = {row["themeId"]: row["id"] for row in scope.get("sections") or []}
    dropped: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    relevance_client = client
    for row in pool.assessments(run_id, kept_only=False):
        signal = {
            "signal_id": row["signal_id"], "title": row.get("title"), "text": row.get("text"), "publisher": row.get("publisher"),
            "url": row.get("url"), "published_at": row.get("published_at"),
        }
        themes = [item.get("theme_id") for item in (row.get("themes") or []) if item.get("theme_id")]
        for target in (row.get("link") or {}).get("sighting_targets") or []:
            if target not in themes:
                themes.append(target)
        classification = classify(signal.get("title") or "", signal.get("text") or "", scope=scope)
        section_id = section_for_signal(classification, [str(item) for item in themes if item], scope,
                                        title=signal.get("title") or "", body=signal.get("text") or "")
        if not section_id:
            section_id = next((theme_to_section[theme] for theme in themes if theme in theme_to_section), None)
        series = detect_series(signal.get("title") or "", signal.get("text") or "", signal.get("url") or "", signal.get("publisher") or "")
        section_id = route_section(section_id, signal.get("title") or "", signal.get("text") or "", series=series)
        keep, reason = in_scope(classification, section_id=section_id, title=signal.get("title") or "", body=signal.get("text") or "")
        if not keep:
            dropped.append({"signal_id": signal["signal_id"], "title": signal.get("title"), "reason": reason})
            if section_id and section_id in sections:
                sections[section_id]["dropped"].append({"title": signal.get("title"), "reason": reason})
            continue
        if not section_id:
            dropped.append({"signal_id": signal["signal_id"], "title": signal.get("title"), "reason": "NO_SECTION"})
            continue
        title_key = _plain(signal.get("title") or "").casefold()
        if title_key in seen_titles:
            dropped.append({"signal_id": signal["signal_id"], "title": signal.get("title"), "reason": "DUPLICATE_TITLE"})
            continue
        relevance = None
        if use_relevance:
            from grit.wsh.relevance import check_relevance

            if relevance_client is None:
                from grit.agents.gemini import gemini_client

                relevance_client = gemini_client()
            relevance = check_relevance(signal, classification, section_id, client=relevance_client, model=model)
            if relevance.get("decision") != "KEEP":
                dropped.append({"signal_id": signal["signal_id"], "title": signal.get("title"),
                                "reason": f"RELEVANCE_DROP:{relevance.get('reason')}"})
                sections[section_id]["dropped"].append({"title": signal.get("title"), "reason": relevance.get("reason")})
                continue
        seen_titles.add(title_key)
        bucket = sections[section_id]
        note = _note_from_signal(signal, classification, section_id)
        note["hazard"] = fill_energy(classification, note.get("hazard"))
        note["bulletin_series"] = series
        note["is_bulletin"] = series is not None
        note["ste_relevance"] = reason
        if relevance:
            note["relevance_reason"] = relevance.get("reason")
            if relevance.get("ste_industry"):
                note["industry"] = relevance["ste_industry"]
        if section_id == "SERIOUS" and len(bucket["discussions"]) >= DISCUSSION_CAP:
            note["depth"] = "log"
            bucket["log"].append(_log_row(signal, classification))
        else:
            bucket["discussions"].append(note)
    bulletin_items = [
        {
            "title": row.get("title"), "text": row.get("text"), "url": row.get("url"), "publisher": row.get("publisher"),
            "series": detect_series(row.get("title") or "", row.get("text") or "", row.get("url") or "", row.get("publisher") or ""),
        }
        for row in pool.assessments(run_id, kept_only=False)
    ]
    return {
        "sections": [sections[row["id"]] for row in scope.get("sections") or []],
        "framework_boxes": dict(scope.get("frameworkBoxes") or {}),
        "dropped": dropped,
        "geography_phase": scope.get("geographyPhase"),
        "council_line": council_line(bulletin_items),
        "reasoning": None,
    }


def _collect_reasoned(pool: Pool, run_id: str, pack: Any, *, client: Any | None, model: str | None, progress: Any | None) -> dict[str, Any]:
    from grit.wsh.reason import run_reasoned_review

    scope = pack.scope or {}
    sections = {row["id"]: {"id": row["id"], "title": row["title"], "themeId": row["themeId"], "discussions": [], "log": [], "dropped": []}
                for row in scope.get("sections") or []}
    review = run_reasoned_review(pool, run_id, pack, client=client, model=model, progress=progress)
    signals = {row["signal_id"]: row for row in pool.assessments(run_id, kept_only=False)}
    dropped: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    pending: dict[str, list[dict[str, Any]]] = {key: [] for key in sections}
    # Prefer INCLUDE over WATCH, then higher evidence score, so duplicate wires of one event keep one note.
    ordered = sorted(
        review["selections"].items(),
        key=lambda item: (
            0 if item[1].get("decision") == "INCLUDE" else 1 if item[1].get("decision") == "WATCH" else 2,
            -int((item[1].get("significance") or {}).get("evidence") or 0),
            item[0],
        ),
    )
    for signal_id, selection in ordered:
        row = signals.get(signal_id) or {}
        title = row.get("title") or (selection.get("candidate") or {}).get("title")
        body = row.get("text") or ""
        if selection.get("decision") not in {"INCLUDE", "WATCH"}:
            dropped.append({"signal_id": signal_id, "title": title, "reason": f"{selection.get('decision')}:{selection.get('reason')}"})
            continue
        series = detect_series(title or "", body, row.get("url") or "", row.get("publisher") or "")
        section_id = route_section(selection.get("section_id"), title or "", body, series=series)
        if section_id not in sections:
            dropped.append({"signal_id": signal_id, "title": title, "reason": "NO_SECTION"})
            continue
        title_key = _plain(title or "").casefold()
        title_key = re.split(r"\s+[|\-–—]\s+", title_key, maxsplit=1)[0].strip()
        title_key = re.sub(r"\s+", " ", title_key)
        if title_key in seen_titles:
            dropped.append({"signal_id": signal_id, "title": title, "reason": "DUPLICATE_TITLE"})
            continue
        seen_titles.add(title_key)
        classification = classify(row.get("title") or "", row.get("text") or "", scope=scope)
        signal = {
            "signal_id": signal_id, "title": title, "text": row.get("text"), "publisher": row.get("publisher"),
            "url": row.get("url"), "published_at": row.get("published_at"),
        }
        note = _note_from_selection(signal, classification, selection, section_id)
        note["prior_bulletins"] = surface_for_note(pool, note.get("hazard") or "", exclude_signal=signal_id)
        if note.get("is_bulletin") and selection.get("decision") in {"INCLUDE", "WATCH"}:
            store_from_selection(
                pool,
                run_id,
                signal_id=signal_id,
                hazard=str(note.get("hazard") or ""),
                earlier_cases=str(selection.get("earlier_cases") or ""),
                mechanism=_plain(title or "")[:160],
            )
        if section_id == "SERIOUS" and selection.get("decision") == "WATCH":
            sections[section_id]["log"].append(_log_row(signal, classification))
        elif section_id == "SERIOUS" and len(pending[section_id]) >= DISCUSSION_CAP:
            sections[section_id]["log"].append(_log_row(signal, classification))
        else:
            pending[section_id].append(note)
    for section_id, notes in pending.items():
        sections[section_id]["discussions"] = collapse_notes(notes)
    bulletin_items = [
        {
            "title": row.get("title"), "text": row.get("text"), "url": row.get("url"), "publisher": row.get("publisher"),
            "series": detect_series(row.get("title") or "", row.get("text") or "", row.get("url") or "", row.get("publisher") or ""),
        }
        for row in signals.values()
    ]
    return {
        "sections": [sections[row["id"]] for row in scope.get("sections") or []],
        "framework_boxes": dict(scope.get("frameworkBoxes") or {}),
        "dropped": dropped,
        "geography_phase": scope.get("geographyPhase"),
        "council_line": council_line(bulletin_items),
        "reasoning": {"nominated": review["nominated"], "reviewed": review["reviewed"], "prompt_version": review["prompt_version"]},
    }


def build_digest(
    pool: Pool,
    run_id: str,
    profile: Any,
    *,
    client: Any | None = None,
    model: str | None = None,
    use_relevance: bool = False,
    use_reasoned: bool = False,
    progress: Any | None = None,
) -> dict[str, Any]:
    from grit.pipeline.reader_scan import _load_pack

    run = pool.run(run_id) or {}
    pack_name = str(run.get("pack") or "")
    pack = _load_pack(pack_name)
    brief_config = profile.brief_for(pack_name) or {}
    collected = collect_items(
        pool, run_id, pack, client=client, model=model,
        use_relevance=use_relevance and not use_reasoned,
        use_reasoned=use_reasoned,
        progress=progress,
    )
    run_date = run.get("run_date")
    if isinstance(run_date, date):
        run_date = run_date.isoformat()
    method_note = (
        "Wide scan nominates candidates. Assessment states fact → STE exposure → transmission → impact; "
        "the Critic challenges the weakest link (spec 45 pattern for WSH). Nothing is published by code."
        if use_reasoned else
        "Agents propose discussion notes. Deterministic scope and citation checks decide what is kept. Nothing is published by code."
    )
    digest = {
        "meta": {
            "pack": pack_name,
            "pack_label": pack.label,
            "level": pack.level,
            "report_kind": pack.report_kind,
            "run_id": run_id,
            "run_date": run_date,
            "title": brief_config.get("title") or "GRIT-Scan: Workplace Safety & Health",
            "subtitle": brief_config.get("subtitle") or "Monthly Workplace Safety & Health Digest",
            "prepared_for": brief_config.get("preparedFor") or profile.name,
            "audience": (pack.scan_plan or {}).get("audience"),
            "status": "DRAFT_FOR_REVIEW",
            "geography_phase": collected["geography_phase"],
            "reasoning": collected.get("reasoning"),
        },
        "bottom_line": _bottom_line(collected),
        "council_line": collected.get("council_line") or council_line([]),
        "framework_boxes": collected["framework_boxes"],
        "sections": collected["sections"],
        "dropped": collected["dropped"],
        "membership": [
            {"signal_id": src["signal_id"], "section": section["id"]}
            for section in collected["sections"]
            for note in section["discussions"]
            for src in note.get("sources") or []
            if src.get("signal_id")
        ],
        "method": {
            "note": method_note,
            "window_days": (pack.scan_plan or {}).get("windowDays"),
            "reasoning": collected.get("reasoning"),
        },
    }
    digest["content_hash"] = json.dumps(
        {"sections": [{k: section[k] for k in ("id", "discussions", "log")} for section in digest["sections"]],
         "bottom_line": digest["bottom_line"]},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return digest


def _bottom_line(collected: Mapping[str, Any]) -> str:
    counts = []
    for section in collected.get("sections") or []:
        n = len(section.get("discussions") or []) + len(section.get("log") or [])
        if n:
            counts.append(f"{n} in {section['title']}")
    if not counts:
        return "No in-scope Workplace Safety & Health development was kept from the checked sources this month."
    return "This month's digest covers " + "; ".join(counts) + ". Regulatory changes appear as alerts and as short notes under the topic they affect."


def render_markdown(digest: Mapping[str, Any]) -> str:
    meta = digest["meta"]
    lines = [
        f"# {meta['title']}",
        "",
        f"**{meta.get('subtitle') or ''}**",
        "",
        f"Prepared for: {meta.get('prepared_for')}",
        f"Run date: {meta.get('run_date')}",
        f"Status: {meta.get('status')}",
        f"Geography phase: {meta.get('geography_phase')}",
        "",
        "## Bottom line",
        "",
        digest.get("bottom_line") or "",
        "",
        digest.get("council_line") or council_line([]),
        "",
    ]
    boxes = digest.get("framework_boxes") or {}
    for section in digest.get("sections") or []:
        lines += [f"## {section['title']}", ""]
        box = boxes.get(section["id"])
        if box:
            lines += [f"> Standing frame: {box}", ""]
        if not section.get("discussions") and not section.get("log"):
            lines += ["No in-scope item was kept for this section this month.", ""]
            continue
        for note in section.get("discussions") or []:
            lines += [
                f"### {note['title']}",
                "",
                f"- **What the source establishes:** {note['established']}",
                f"- **Date / place:** {note.get('date') or NOT_ESTABLISHED} / {note.get('place') or NOT_ESTABLISHED}",
                f"- **Industry / energy:** {note.get('industry') or NOT_ESTABLISHED} / {note.get('hazard') or NOT_ESTABLISHED}",
            ]
            if note.get("exposure") or note.get("transmission"):
                lines += [
                    f"- **STE exposure:** {note.get('exposure') or NOT_ESTABLISHED}",
                    f"- **Transmission:** {note.get('transmission') or NOT_ESTABLISHED}",
                ]
            if note.get("is_bulletin") or section["id"] == "SERIOUS":
                lines += [
                    f"- **What happened:** {note.get('what_happened') or NOT_ESTABLISHED}",
                    f"- **What failed:** {note.get('failed_controls') or NOT_ESTABLISHED}",
                    f"- **Earlier cases the source names:** {note.get('earlier_cases') or NOT_ESTABLISHED}",
                    f"- **What the source says to do:** {note.get('what_companies_should_do') or NOT_ESTABLISHED}",
                    f"- **Instrument:** {note.get('instrument') or NOT_ESTABLISHED}",
                    f"- **Investigation status:** {note.get('investigation_status') or NOT_ESTABLISHED}",
                    f"- **Penalty:** {note.get('penalty') or NOT_ESTABLISHED}",
                ]
            lines += [
                f"- **Not established:** {note.get('not_established') or NOT_ESTABLISHED}",
                f"- **Lesson or practice:** {note.get('lesson_or_practice') or NOT_ESTABLISHED}",
                f"- **Possible implication:** {note.get('implication') or NOT_ESTABLISHED}",
            ]
            if note.get("uncertainty") and note.get("uncertainty") != NOT_ESTABLISHED:
                lines.append(f"- **Uncertainty:** {note['uncertainty']}")
            if note.get("text_not_read"):
                lines.append("- **Text not read:** Image-only PDF; Council HTML view-in-browser was not available.")
            for prior in note.get("prior_bulletins") or []:
                lines.append(f"- **Prior bulletin:** {prior}")
            for src in note.get("sources") or []:
                lines.append(f"- **Source:** [{src.get('outlet')}, {src.get('date') or 'n.d.'}]({src.get('url')})")
            lines.append("")
        if section.get("log"):
            lines += ["### Incident log", "", "| Title | Date | Place | Energy | Source |", "| --- | --- | --- | --- | --- |"]
            for row in section["log"]:
                src = row.get("source") or {}
                lines.append(
                    f"| {row.get('title')} | {row.get('date') or 'n.d.'} | {row.get('place')} | {row.get('energy')} | "
                    f"[{src.get('outlet')}]({src.get('url')}) |"
                )
            lines.append("")
    lines += [
        "## Method",
        "",
        (digest.get("method") or {}).get("note") or "",
        "",
        "This digest is a draft for review. It is not an approved STE position.",
        "",
    ]
    return "\n".join(lines)


def render_docx(digest: Mapping[str, Any], path: Path) -> Path:
    from docx import Document
    from docx.shared import Pt

    document = Document()
    meta = digest["meta"]
    document.add_heading(meta["title"], level=0)
    document.add_paragraph(meta.get("subtitle") or "")
    document.add_paragraph(f"Prepared for: {meta.get('prepared_for')} | Run date: {meta.get('run_date')} | Status: {meta.get('status')}")
    document.add_heading("Bottom line", level=1)
    document.add_paragraph(digest.get("bottom_line") or "")
    document.add_paragraph(digest.get("council_line") or council_line([]))
    boxes = digest.get("framework_boxes") or {}
    for section in digest.get("sections") or []:
        document.add_heading(section["title"], level=1)
        box = boxes.get(section["id"])
        if box:
            paragraph = document.add_paragraph()
            run = paragraph.add_run(f"Standing frame: {box}")
            run.italic = True
        if not section.get("discussions") and not section.get("log"):
            document.add_paragraph("No in-scope item was kept for this section this month.")
            continue
        for note in section.get("discussions") or []:
            document.add_heading(note["title"], level=2)
            for label, key in (
                ("What the source establishes", "established"),
                ("Date / place", None),
                ("Industry / energy", None),
                ("STE exposure", "exposure"),
                ("Transmission", "transmission"),
                ("What happened", "what_happened"),
                ("What failed", "failed_controls"),
                ("Earlier cases the source names", "earlier_cases"),
                ("What the source says to do", "what_companies_should_do"),
                ("Instrument", "instrument"),
                ("Investigation status", "investigation_status"),
                ("Penalty", "penalty"),
                ("Not established", "not_established"),
                ("Lesson or practice", "lesson_or_practice"),
                ("Possible implication", "implication"),
                ("Uncertainty", "uncertainty"),
            ):
                if key == "exposure" and not note.get("exposure") and not note.get("transmission"):
                    continue
                if key == "transmission" and not note.get("transmission") and not note.get("exposure"):
                    continue
                if key in {
                    "what_happened", "failed_controls", "earlier_cases", "what_companies_should_do",
                    "instrument", "investigation_status", "penalty",
                } and not note.get("is_bulletin") and section["id"] != "SERIOUS":
                    continue
                if key == "uncertainty" and (not note.get("uncertainty") or note.get("uncertainty") == NOT_ESTABLISHED):
                    continue
                if key:
                    document.add_paragraph(f"{label}: {note.get(key) or NOT_ESTABLISHED}")
                elif label.startswith("Date"):
                    document.add_paragraph(f"{label}: {note.get('date') or NOT_ESTABLISHED} / {note.get('place') or NOT_ESTABLISHED}")
                else:
                    document.add_paragraph(f"{label}: {note.get('industry') or NOT_ESTABLISHED} / {note.get('hazard') or NOT_ESTABLISHED}")
            for prior in note.get("prior_bulletins") or []:
                document.add_paragraph(f"Prior bulletin: {prior}")
            for src in note.get("sources") or []:
                document.add_paragraph(f"Source: {src.get('outlet')}, {src.get('date') or 'n.d.'}. {src.get('url')}")
        if section.get("log"):
            document.add_heading("Incident log", level=2)
            table = document.add_table(rows=1, cols=5)
            headers = ("Title", "Date", "Place", "Energy", "Source")
            for cell, header in zip(table.rows[0].cells, headers):
                cell.text = header
            for row in section["log"]:
                cells = table.add_row().cells
                src = row.get("source") or {}
                cells[0].text = str(row.get("title") or "")
                cells[1].text = str(row.get("date") or "n.d.")
                cells[2].text = str(row.get("place") or "")
                cells[3].text = str(row.get("energy") or "")
                cells[4].text = str(src.get("outlet") or "")
    document.add_heading("Method", level=1)
    document.add_paragraph((digest.get("method") or {}).get("note") or "")
    document.add_paragraph("This digest is a draft for review. It is not an approved STE position.")
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            run.font.size = run.font.size or Pt(11)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


def next_digest_version(pool: Pool, run_id: str, profile_id: str, *, output_root: Path | None = None) -> int:
    """Next free package version: one past the highest package row or on-disk vN folder.

    ``--no-submit`` never inserts a packages row, so versioning from the table alone kept rewriting v2.
    """
    from grit.settings import OUTPUT_DIR

    package_id = f"PKG-{run_id}-{profile_id}"
    latest = pool.one("SELECT version FROM packages WHERE package_id = ? ORDER BY version DESC LIMIT 1", (package_id,))
    highest = int(latest["version"]) if latest else 0
    root = (output_root or OUTPUT_DIR) / "runs" / run_id / profile_id
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and re.fullmatch(r"v\d+", child.name):
                highest = max(highest, int(child.name[1:]))
    return highest + 1


def write_digest_pack(
    pool: Pool,
    run_id: str,
    profile: Any,
    *,
    folder: Path | None = None,
    submit: bool = True,
    client: Any | None = None,
    model: str | None = None,
    use_relevance: bool = False,
    use_reasoned: bool = True,
    progress: Any | None = None,
) -> dict[str, Any]:
    """Write Word and Markdown for the WSH digest. Default path is Assessment → Critic (spec 45 pattern)."""
    from grit.settings import OUTPUT_DIR
    from grit.workflow.packages import submit_package

    digest = build_digest(
        pool, run_id, profile, client=client, model=model,
        use_relevance=use_relevance and not use_reasoned,
        use_reasoned=use_reasoned,
        progress=progress,
    )
    package_id = f"PKG-{run_id}-{profile.profile_id}"
    latest = pool.one("SELECT * FROM packages WHERE package_id = ? ORDER BY version DESC LIMIT 1", (package_id,))
    if submit and latest and latest["status"] == "PENDING_REVIEW":
        if latest["membership"].get("brief_hash") == digest["content_hash"]:
            return {"profile": profile.profile_id, "files": latest["files"], "content_hash": digest["content_hash"],
                    "package": {"package_id": package_id, "version": latest["version"], "reused": True}}
        return {"profile": profile.profile_id, "skipped": f"{package_id} v{latest['version']} is still pending review with different content."}
    version = next_digest_version(pool, run_id, profile.profile_id)
    folder = folder or OUTPUT_DIR / "runs" / run_id / profile.profile_id / f"v{version}"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"GRIT-Scan_wsh_{profile.profile_id}_{digest['meta']['run_date']}"
    files: dict[str, str] = {}
    formats = set((profile.brief_for(digest["meta"]["pack"]) or {}).get("formats") or ["docx", "md"])
    if "docx" in formats:
        files["docx"] = str(render_docx(digest, folder / f"{stem}.docx"))
    (folder / f"{stem}.md").write_text(render_markdown(digest), encoding="utf-8")
    (folder / f"{stem}.digest.json").write_text(json.dumps(digest, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    files["md"] = str(folder / f"{stem}.md")
    files["json"] = str(folder / f"{stem}.digest.json")
    result: dict[str, Any] = {"profile": profile.profile_id, "files": files, "content_hash": digest["content_hash"], "members": len(digest["membership"]), "report_kind": "wsh-digest"}
    if submit and digest["membership"]:
        try:
            # submit_package expects signal_id strings, as the BA brief membership list does.
            members = sorted({str(row["signal_id"]) for row in digest["membership"] if row.get("signal_id")})
            result["package"] = submit_package(
                pool, run_id=run_id, profile=profile, members=members,
                brief_hash=digest["content_hash"], files=files,
            )
        except ValueError as exc:
            result["package"] = {"skipped": str(exc)}
    return result
