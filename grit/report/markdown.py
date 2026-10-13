"""Render a brief model as Markdown, for the dashboard preview and for review in a text diff."""

from __future__ import annotations

from typing import Any, Mapping

from grit.report.docx_brief import OPTIONS_NOTE, SECTION_TITLES, option_line, section_title


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
        f"*Scan window: {meta['window']} | Prepared for: {meta['prepared_for']} | Status: draft for review, not approved.*", "",
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
        elif key == "CONDITIONS" and brief.get("conditions"):
            full = [row for row in brief["conditions"] if row.get("full")]
            for row in full:
                body += [f"### {row['number']}. {row['name']}", "", f"*{row.get('group') or 'Risk theme'}. Trend: {row['trend']}. Confidence: {row['confidence']}.*", "", _md(row["basis"]), ""]
                if row.get("implication"):
                    body += [f"**What it could mean for {meta.get('pack_label') or 'the business area'}:** {_md(row['implication'])}", ""]
                if row.get("options"):
                    body += ["**Options for management consideration**", ""]
                    body += [f"- {_md(option['text'])} *{option_line(option)}*" for option in row["options"]]
                    body += ["", f"*{OPTIONS_NOTE}*", ""]
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
