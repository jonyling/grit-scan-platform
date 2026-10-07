"""Render a brief model as Markdown, for the dashboard preview and for review in a text diff."""

from __future__ import annotations

from typing import Any, Mapping

from grit.report.docx_brief import SECTION_TITLES


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
        elif key in {"SUPPLIERS", "CUSTOMERS", "HAZARDS"}:
            body += [f"- {item['name']}: {_md(item['text'])}" for item in brief.get(key.lower()) or []]
        elif key == "CONDITIONS" and brief.get("conditions"):
            body += ["| # | Condition | Trend | Confidence | Basis |", "|---|---|---|---|---|"]
            body += [f"| {row['number']} | {row['name']} | {row['trend']} | {row['confidence']} | {_md(row['basis'])} |" for row in brief["conditions"]]
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
            lines += [f"## {number}. {SECTION_TITLES[key]}", "", *body, ""]
            number += 1
    lines.append(f"*Content hash {brief['content_hash'][:16]}. Run {meta['run_id']}.*")
    return "\n".join(lines) + "\n"
