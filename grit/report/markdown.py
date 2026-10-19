"""Render a brief model as Markdown, for the dashboard preview and for review in a text diff."""

from __future__ import annotations

from typing import Any, Mapping

from grit.report.docx_brief import OPTIONS_NOTE, SECTION_TITLES, implication_text, key_risk_headers, priority_trend, radar_cells, radar_headers, response_lines, section_title


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


def render_markdown(brief: Mapping[str, Any]) -> str:
    meta = brief["meta"]
    bottom = brief["bottom_line"]
    lines = [
        f"# {meta['title']}", "", f"{meta['subtitle']}", "",
        f"*Scan window: {meta['window']} | Prepared for: {meta['prepared_for']}" + (" | Status: draft for review, not approved.*" if meta.get("show_review_status", True) else "*"), "",
        f"> **{bottom['label']} — Bottom line.** {bottom['text']} {bottom.get('escalation') or ''}", "",
    ]
    number = 0
    for key in meta["sections"]:
        if key == "BOTTOM_LINE" or key not in SECTION_TITLES:
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
                body.append(f"- **{row['title']}** ({row['source'] or row['requested_by']}): " + ("; ".join(f"[{item['title']}]({item['url']}) ({item['outlet']}, {item['date'] or 'n.d.'})" for item in row["signals"]) or "no signal matched this week."))
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
            body += [f"- [{item['priority'] + ' · ' if item['priority'] else ''}tier {item['tier']}] [{item['title']}]({item['url']}) ({item['outlet']}, {item['date'] or 'n.d.'}). {' '.join(line for line in item['why'] if line)}" for item in brief.get("findings") or []]
        elif key == "OTHER_SIGNALS":
            body += [f"- {'[' + item['priority'] + '] ' if item['priority'] else ''}[{item['title']}]({item['url']}) ({item['outlet']}, {item['date'] or 'n.d.'})" for item in brief.get("other_signals") or []]
        elif key == "QA_LOG":
            body += [f"- **{row['item']}**: {row['prior'] or ''} → {_md(row['correction'])}" for row in brief.get("qa") or []] or ["- No supported issue this week."]
        elif key == "WATCHPOINTS":
            body += [f"- **{row['title']}** ({row['status'].lower()}): {row['detail']} {_md(row['update'], '')}" for row in brief.get("watchpoints") or [] if row["status"] != "CLOSED"]
            body.append(f"\nEscalation assessment: {bottom.get('escalation') or ''}")
        if body:
            lines += [f"## {number}. {section_title(meta, key)}", "", *body, ""]
            number += 1
    lines.append(f"*Content hash {brief['content_hash'][:16]}. Run {meta['run_id']}.*")
    return "\n".join(lines) + "\n"
