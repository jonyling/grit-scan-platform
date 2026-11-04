"""Render a brief model as Markdown, for the dashboard preview and for review in a text diff."""

from __future__ import annotations

from typing import Any, Mapping

from grit.report.docx_brief import (
    APPENDIX_LETTERS, CORRECTIONS_HEADERS, CORRECTIONS_NOTE, FOLLOW_UPS_LABEL, FOLLOW_UPS_NOTE, NO_CORRECTIONS, OPTIONS_NOTE, POSTURE_TITLE,
    SCENARIO_HEADERS, SCENARIO_NOTE, SECTION_TITLES, V2_APPENDIX, V2_NOTE, V2_REPLACES, correction_cells, follow_ups, implication_text,
    key_risk_headers, posture_lines, priority_trend, radar_cells, radar_headers, red_line, response_lines, scenario_cells, section_title, v2_title,
)
from grit.report.notices import DISCLAIMER
from grit.pipeline.indicators import indicator_line
from grit.report.repetition import dedupe_markdown
from grit.report.suggestions import suggestions_for_signal


def _md(rendered: Mapping[str, Any] | None, fallback: str = "No new item found this week in the checked sources.") -> str:
    if not rendered:
        return fallback
    parts = []
    for run in rendered["runs"]:
        if "text" in run:
            parts.append(run["text"])
        else:
            refs = "; ".join(f"[{ref['outlet']}, {ref['date'] or 'n.d.'}]({ref['url']})" if ref.get("url") else str(ref["outlet"]) for ref in run["cite"])
            parts.append(f" ({refs})")
    return "".join(parts).replace("  ", " ").strip()


def _cell(text: Any) -> str:
    return str(text if text is not None else "").replace("|", "/").replace("\n", " ")


def _refs(refs: Any) -> str:
    return "; ".join(f"[{ref['outlet']}]({ref['url']})" if ref.get("url") else str(ref.get("outlet")) for ref in refs or [])


def _table(headers: Any, rows: Any) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers), *("| " + " | ".join(_cell(value) for value in row) + " |" for row in rows)]


EXPOSURE_TABLES = (
    ("sites", "Sites", ("Entity", "City", "Country", "Staff", "Safety last confirmed"), ("entity", "city", "country", "headcount", "safetyConfirmed")),
    ("travel", "Travel", ("Destination", "Country", "Travellers", "Until", "Safety last confirmed"), ("destination", "country", "travellers", "until", "safetyConfirmed")),
    ("projects", "Projects", ("Project", "Entity", "Where", "Value", "Next milestone"), ("name", "entity", "city", "value", "nextMilestone")),
    ("financial", "Financial exposure", ("Item", "Entity", "Amount"), ("item", "entity", "amount")),
    ("alerts", "Internal alerts", ("Date", "Location", "Summary"), ("date", "location", "summary")),
)


def _exposure(snapshot: Mapping[str, Any] | None) -> list[str]:
    """The approved exposure snapshot (URS methodology, section 15.2), or a statement that there was none."""
    if not snapshot:
        return ["No approved internal exposure snapshot this week. Staff, site and project exposure rests on public sources only."]
    lines = [f"Approved internal data as of {snapshot.get('asOf')}, approved by {snapshot.get('approvedBy')}. "
             f"Sources: {'; '.join(snapshot.get('sources') or []) or 'not stated'}.", ""]
    for key, title, headers, fields in EXPOSURE_TABLES:
        if snapshot.get(key):
            lines += [f"**{title}**", "", *_table(list(headers), [[row.get(field) if row.get(field) not in (None, "") else "Not recorded"
                                                                      for field in fields] for row in snapshot[key]]), ""]
    return lines


def _v2(brief: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """Format v2 sections (spec 44) that open the report, in the URS methodology's order (section 15), as Wilson's report does."""
    v2, short = brief["v2"], brief["v2"]["short"]
    sections: list[tuple[str, list[str]]] = [("Exposure snapshot", _exposure(v2.get("exposure")))]
    if v2["changes"]:
        sections.append(("What changed since the last report", _table(["Item", "Last report", "This report", "Direction"],
                                                                       [[row["item"] + (f" [{row['risk']}]" if row["risk"] else ""), row["before"], row["now"], row["direction"]] for row in v2["changes"]])))
    look = _table(["Date", "Event, as the source states it", f"Relevance to {short}", "Sources"], [[row["date"], row["event"], row["relevance"], _refs(row["sources"])] for row in v2["look_ahead"]]) \
        if v2["look_ahead"] else ["No source linked to the business area states a dated event in the next 30 days."]
    sections.append(("Look-ahead: the next 30 days", look))
    rows = [[n, f"{row['band']} **{row['item']}**" + (f" [{row['risk']}]" if row["risk"] else ""), _md(row["summary"], "Withheld: the draft failed its checks. See the QA log."),
             _md(row["implications"], "Not drafted."), f"{(row.get('response') or {}).get('action') or 'Not drafted.'} Owner: {(row.get('response') or {}).get('owner') or 'to assign'}.", _refs(row["sources"])]
            for n, row in enumerate(v2["escalating"], start=1)]
    sections.append(("Escalating and elevated items", [*_table(["#", "Item and risk", "Summary", f"Implications for {short}", "Suggested response", "Sources"], rows), "", f"*{V2_NOTE}*"]
                     if rows else ["No event met the escalation rule this week."]))
    if v2["priorities"]:
        sections.append(("Prioritisation", _table(["#", "Item", "Trend", "Risk linkage", "Owner or forum"], [[n, row["item"], row["trend"], row["risk"], row["owner"]] for n, row in enumerate(v2["priorities"], start=1)])))
    sections.append(("Coverage matrix", _table(["Line of business", *v2["categories"]], [[line["lob"], *(line["cells"][category]["text"] for category in v2["categories"])] for line in v2["coverage"]])))
    sections.append(("Jurisdiction coverage", _table(["Jurisdiction", "Entities", "In-window finding", "Trend"],
                                                      [[row["country"], "; ".join(row["entities"]), row["finding"], row["trend"]] for row in v2["jurisdictions"]])))
    return sections


def _v2_closing(brief: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
    """The named watch and the recommended action posture, which close a format v2 report before its quality check."""
    v2 = brief["v2"]
    sections: list[tuple[str, list[str]]] = []
    if v2["watch"]:
        sections.append(("Named supplier, customer and competitor watch", _table(["Name", "Type", "This week"], [[row["name"], row["type"], _md(row["text"])] for row in v2["watch"]])))
    posture = [f"**{label.strip()}** {text}" for label, text in posture_lines(brief)]
    posture = [line for item in posture for line in (item, "")]
    posture += [f"- **{label.strip()}** {text}" for label, text in (red_line(alert, brief.get("red_triggers") or {}) for alert in brief.get("red") or [])]
    owners = follow_ups(brief)
    if owners:
        posture += ["", f"**{FOLLOW_UPS_LABEL.strip()}** {FOLLOW_UPS_NOTE}", "", *(f"- **{owner}**: {'; '.join(refs)}." for owner, refs in owners)]
    sections.append((POSTURE_TITLE, posture))
    return sections


def _v2_tail(brief: Mapping[str, Any], number: int) -> list[str]:
    v2 = brief["v2"]
    rows = [[row["question"] + (" (critical)" if row.get("critical") else ""), row["answer"], row["note"]] for row in v2["gate"]]
    lines = [f"## {number}. Quality and completeness check", "", *_table(["Question", "Answer", "Note"], rows), ""]
    blocking = (v2.get("completeness") or {}).get("blocking") or []
    if blocking:
        lines += [f"Not ready for release: {len(blocking)} critical completeness questions were answered No.", ""]
    if v2["conflicts"]:
        lines += _table(["Item", "What the sources say", "Treatment"], [[row["item"], row["said"], row["treatment"]] for row in v2["conflicts"]]) + [""]
    corrections = brief.get("corrections") or []
    lines += ["### Corrections ledger", ""]
    lines += [f"*{CORRECTIONS_NOTE}*", "", *_table(list(CORRECTIONS_HEADERS), [correction_cells(row) for row in corrections]), ""] if corrections else [NO_CORRECTIONS, ""]
    return lines


def render_markdown(brief: Mapping[str, Any]) -> str:
    meta = brief["meta"]
    bottom = brief["bottom_line"]
    lines = [
        f"# {meta['title']}", "", f"{meta['subtitle']}", "",
        f"*Scan window: {meta['window']} | Prepared for: {meta['prepared_for']}" + (" | Status: draft for review, not approved.*" if meta.get("show_review_status", True) else "*"),
        "", DISCLAIMER, "",
        f"> **{bottom['label']} — Bottom line.** {bottom['text']} {bottom.get('escalation') or ''}", "",
    ]
    v2 = brief.get("v2")
    sections: list[tuple[str, str, list[str]]] = []  # (key, title, body)
    appendix: list[tuple[str, str, list[str]]] = []
    if v2:
        basis = (v2.get("status_basis") or {}).get("basis")
        status = f"**STATUS: {v2['status']}.**" + (f" {basis}" if basis else "") + (f" Trend: {'; '.join(v2['arrows'])}." if v2["arrows"] else "") + (f" Next dated items: {'; '.join(v2['risk_window'])}." if v2["risk_window"] else "")
        lines[lines.index(DISCLAIMER) + 2:lines.index(DISCLAIMER) + 2] = [status, ""]
        sections += [(title, title, body) for title, body in _v2(brief)]
    for key in meta["sections"]:
        if key == "BOTTOM_LINE" or key not in SECTION_TITLES or (v2 and key in V2_REPLACES):
            continue
        body: list[str] = []
        if key == "ORDER_BOOK" and brief.get("order_book"):
            book = brief["order_book"]
            body += [f"- {item['label']}: {item['value']}" for item in book.get("anchors") or []]
            body += [f"- {item['label']}: {item['value']}" for item in book.get("contracts") or []]
        elif key == "PROJECTS":
            for group in brief.get("projects") or []:
                body.append(f"**{group['heading']}**")
                body += [f"- {item['name']}: {_md(item['text'])}" for item in group["items"]]
        elif key == "MANAGEMENT_REQUESTS":
            for row in [item for item in brief.get("management_requests") or [] if item["status"] == "ACTIVE"]:
                body.append(f"- **{row['title']}** ({row['source'] or row['requested_by']}): " + ("; ".join(f"[{item['title']}]({item['url']}) ({item['outlet']}, {item['date'] or 'n.d.'}{', ' + item['via'] if item.get('via') else ''})" for item in row["signals"]) or "no signal matched this week."))
        elif key == "ENTITY_PACKS":
            for row in brief.get("entity_packs") or []:
                body.append(f"- **{row['label']}**: {row['bottom_line'] or 'no validated bottom line on this date.'}" + (f" Moved: {'; '.join(row['moved'])}." if row.get("moved") else ""))
        elif key == "ENTITIES":
            for group in brief.get("entities") or []:
                body.append(f"**{group['heading']}**")
                body += [f"- {item['name']}: {_md(item['text'])}" for item in group["items"] if item["text"]]
                quiet = [item["name"] for item in group["items"] if not item["text"]]
                if quiet:
                    body.append(f"- No new item found: {', '.join(quiet)}.")
        elif key == "COMPETITORS":
            body += [f"- {item['name']}: {_md(item['text'])}" for item in brief.get("competitors") or [] if item["status"] == "FOUND"] or ["- No registered competitor was named this week."]
        elif key in {"SUPPLIERS", "CUSTOMERS", "HAZARDS"}:
            body += [f"- {item['name']}: {_md(item['text'])}" for item in brief.get(key.lower()) or []]
        elif key == "SCENARIOS" and (brief.get("scenarios") or {}).get("shifts"):
            outlook = brief["scenarios"]
            body += ["| " + " | ".join(SCENARIO_HEADERS) + " |", "|" + "---|" * len(SCENARIO_HEADERS)]
            for reading in outlook["shifts"]:
                cells = ["<br>".join(lines) for lines in scenario_cells(reading)]
                cells[0] = cells[0].replace(reading["name"], f"**{reading['name']}**", 1)
                body.append("| " + " | ".join(cell.replace("|", "/").replace("\n", " ") for cell in cells) + " |")
            body += ["", f"*{SCENARIO_NOTE} Scenarios v{outlook['version']}, reviewed by {outlook['reviewed_by']} on {outlook['reviewed_on']}.*"]
            if outlook.get("indicators"):
                body += ["", "Leading indicators. Green: no action. Amber: BAs review exposure and plans. Red: BAs act on the planned responses. The bounds are proposed by GRIT-Scan until Wilson confirms them."]
                body += [f"- **{item['name']}.** {indicator_line(item)}" for item in outlook["indicators"]]
            if outlook.get("risk_links"):
                body += ["", "| Group risk | BA risks | LOB risks | Status |", "|---|---|---|---|"]
                for row in outlook["risk_links"]:
                    ba = "; ".join(f"{item['id']} {item['name']}" for item in row["ba"]) or ""
                    lob = "; ".join(item["name"] for item in row["lob"]) or ""
                    body.append(f"| {row['group_risk']} {row['name']} | {ba} | {lob} | {row['status']} |".replace("\n", " "))
        elif key == "CONDITIONS" and brief.get("conditions") and meta.get("level") == "GROUP":
            options = any(row.get("options") for row in brief["conditions"]) or "RESPONSE_OPTIONS" in meta["sections"]
            headers = radar_headers(options=options)
            body += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
            for row in brief["conditions"]:
                cells = ["<br>".join(lines) for lines in radar_cells(row, options=options)]
                cells[0] = cells[0].replace(f"{row['number']}. {row['name']}", f"**{row['number']}. {row['name']}**", 1)
                body.append("| " + " | ".join(cell.replace("|", "/").replace("\n", " ") for cell in cells) + " |")
            if options:
                body += ["", f"*{OPTIONS_NOTE}*"]
        elif key == "CONDITIONS" and brief.get("conditions"):
            full = [row for row in brief["conditions"] if row.get("full")]
            if full:
                options = any(row.get("options") for row in full) or "RESPONSE_OPTIONS" in meta["sections"]
                headers = key_risk_headers(meta, options=options)
                body += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
                for row in full:
                    refs = row.get("sources") or []
                    cells = [
                        f"**{row['number']}. {row['name']}**<br>{row['development']}",
                        "<br>".join(f"{number}. [{ref['outlet']}]({ref['url']})" if ref.get("url") else f"{number}. {ref['outlet']}" for number, ref in enumerate(refs, start=1)) or "No source this week.",
                        "<br>".join(f"{number}. {ref.get('when') or ref.get('date') or 'Date not stated'}" for number, ref in enumerate(refs, start=1)) or "Not applicable.",
                        implication_text(row),
                    ]
                    if options:
                        cells.append("<br>".join(f"• {option} *{detail}*" for option, detail in response_lines(row)) or "No option proposed.")
                    cells.append("<br>".join(priority_trend(row)))
                    body.append("| " + " | ".join(cell.replace("|", "/").replace("\n", " ") for cell in cells) + " |")
                if options:
                    body += ["", f"*{OPTIONS_NOTE}*"]
                body.append("")
            rest = [row for row in brief["conditions"] if row not in full]
            if rest:
                if full:
                    body += ["### Other risk themes", ""]
                body += ["| # | Condition | Trend | Confidence | Basis |", "|---|---|---|---|---|"]
                body += [f"| {row['number']} | {row['name']}{' (' + row['group'] + ')' if row.get('group') else ''} | {row['trend']} | {row['confidence']} | {_md(row['basis'])} |" for row in rest]
        elif key == "PRIORITY_FINDINGS":
            for item in brief.get("findings") or []:
                parts = [line for line in item["why"] if line]
                card = item.get("card") or {}
                if card.get("what_happened"):
                    parts.insert(0, str(card["what_happened"]))
                if card.get("hypothesis"):
                    parts.append(f"Hypothesis, not a forecast: {card['hypothesis']} Link confidence: {card['confidence']}.")
                parts.extend(suggestions_for_signal(brief, item["signal_id"]))
                body.append(f"- [{item['priority'] + ' · ' if item['priority'] else ''}tier {item['tier']}] [{item['title']}]({item['url']}) ({item['outlet']}, {item['date'] or 'n.d.'}). {' '.join(parts)}".rstrip())
        elif key == "OTHER_SIGNALS":
            body += [f"- {'[' + item['priority'] + '] ' if item['priority'] else ''}[{item['title']}]({item['url']}) ({item['outlet']}, {item['date'] or 'n.d.'})" for item in brief.get("other_signals") or []]
        elif key == "QA_LOG":
            body += [f"- **{row['item']}**: {row['prior'] or ''} → {_md(row['correction'])}" for row in brief.get("qa") or []] or ["- No supported issue this week."]
        elif key == "WATCHPOINTS":
            body += [f"- **{row['title']}** ({row['status'].lower()}): {row['detail']} {_md(row['update'], '')}" for row in brief.get("watchpoints") or [] if row["status"] != "CLOSED"]
            if not v2:
                body.append(f"\nRecommended action posture: {bottom.get('escalation') or ''}")
        if body:
            (appendix if v2 and key in V2_APPENDIX else sections).append((key, v2_title(key, meta) if v2 else section_title(meta, key), body))
    number = 1 if v2 else 0
    if v2:
        sections += [(title, title, body) for title, body in _v2_closing(brief)]
        posture = next(number + index for index, (key, *_rest) in enumerate(sections) if key == POSTURE_TITLE)
        box = next(index for index, line in enumerate(lines) if line.startswith(f"> **{bottom['label']} — Bottom line.**"))
        lines[box] = f"> **{bottom['label']} — Bottom line.** {bottom['text']} The principal ask and when to raise to RED are in section {posture}, {POSTURE_TITLE}."
    for key, title, body in sections:
        lines += [f"## {number}. {title}", "", *body, ""]
        number += 1
    if v2:
        lines += _v2_tail(brief, number)
    for letter, (key, title, body) in zip(APPENDIX_LETTERS, appendix):
        lines += [f"## Appendix {letter}. {title}", "", *body, ""]
    lines += [DISCLAIMER, "", f"*Content hash {brief['content_hash'][:16]}. Run {meta['run_id']}.*"]
    return dedupe_markdown("\n".join(lines) + "\n")
