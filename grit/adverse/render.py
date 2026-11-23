"""Render the adverse news scan as an email and a reviewer worksheet (spec 46, section 8). Pure apart from writing files.

Outlet names, dates and links come from the pool, never from the model.
"""

from __future__ import annotations

import re
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Mapping, Sequence

from grit.adverse import checks
from grit.report.notices import DISCLAIMER

TO = "wilsonweimeng.yeo@stengg.com"
FROM = "khengaikjony.ling@stengg.com"
GREETING = "Dear Wilson,"
CLOSING = ("Kind Regards,", "Jony")
SUBJECT_PREFIX = "Adverse News & Lessons Learnt Scan | "
_SUBJECT = re.compile(r"^Subject: Adverse News & Lessons Learnt Scan \| \d{1,2} [A-Z][a-z]+ \d{4}$", re.M)


def long_date(value: date | str | None) -> str:
    if not value or value == "not stated":
        return "not stated"
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{day.day} {day:%B %Y}"


def subject(report_date: date) -> str:
    return SUBJECT_PREFIX + long_date(report_date)


class _Doc:
    def __init__(self, markdown: bool) -> None:
        self.md = markdown
        self.lines: list[str] = []

    def add(self, text: str = "") -> None:
        self.lines.append(text)

    def heading(self, text: str, level: int = 2) -> None:
        self.add("")
        self.add(f"{'#' * level} {text}" if self.md else text.upper() if level == 2 else text)

    def field(self, label: str, value: str) -> None:
        value = " ".join(str(value).split())
        self.add(f"**{label}:** {value}" if self.md else f"{label}: {value}")

    def bullet(self, text: str, indent: int = 0) -> None:
        self.add(f"{'  ' * indent}- {' '.join(str(text).split())}")

    def link(self, name: str, url: str) -> str:
        return f"[{name}]({url})" if self.md else f"{name}, {url}"


EXTRACT_NOTE = "search-engine text; the page did not open from the scan server"


def _sources(doc: _Doc, item: Mapping[str, Any]) -> None:
    refs = item.get("sources") or []
    if not refs:
        return
    first = refs[0]
    note = f" ({EXTRACT_NOTE})" if first.get("basis") == "SEARCH_EXTRACT" else ""
    doc.field("Source", doc.link(first["outlet"], first["url"]) + note)
    for ref in refs[1:]:
        note = f"; {EXTRACT_NOTE}" if ref.get("basis") == "SEARCH_EXTRACT" else ""
        doc.bullet(f"Also: {doc.link(ref['outlet'], ref['url'])} ({long_date(ref.get('published'))}{note})")


def _dates(doc: _Doc, item: Mapping[str, Any]) -> None:
    doc.field("Event date", long_date(item.get("event_date")))
    publication = item.get("publication_display") or long_date(item.get("publication_date"))
    doc.field("Source date", publication)


def _facts(doc: _Doc, item: Mapping[str, Any], label: str = "Facts confirmed by credible sources") -> None:
    doc.add(f"{label}:")
    outlets = {ref["source_id"]: ref["outlet"] for ref in item.get("sources") or []}
    for fact in item.get("facts") or []:
        doc.bullet(f"{fact['statement']} (\"{fact['quote'].strip()}\", {outlets.get(fact.get('source_id'), 'source')})", 1)


def _update(doc: _Doc, item: Mapping[str, Any]) -> None:
    if item.get("update_status") == "MATERIAL_UPDATE" and item.get("previous"):
        doc.field("Update", f"Material new development since the report of {long_date(item['previous'].get('run_date'))}: "
                            f"\"{str(item.get('new_development_quote') or '').strip()}\"")


def _corroboration(item: Mapping[str, Any]) -> str:
    if item.get("severity") not in {"Critical", "High"}:
        return ""
    if item.get("corroborated"):
        return " (corroborated by an official source or a second independent outlet)"
    return " (single source; corroboration not found)"


def _item_a(doc: _Doc, item: Mapping[str, Any]) -> None:
    doc.heading(f"[{item['item_id']}] {item['headline']}", 4)
    doc.field("Event", item["event"])
    doc.field("Affected entity", item["affected_entity"])
    doc.field("Relationship to ST Engineering", item["relationship"])
    _dates(doc, item)
    _sources(doc, item)
    doc.field("Why it is adverse", item["why_adverse"])
    _facts(doc, item)
    doc.field("Evidence-based assessment", item.get("assessment") or "None beyond the facts.")
    unverified = [u for u in item.get("unverified") or [] if isinstance(u, str) and u.strip()]
    doc.field("Unverified", "; ".join(unverified) if unverified else "Nothing further identified.")
    basis = (f" (the event targets {', '.join(item['targeted_sites'])}; from the internal staff log and entity register, not a news source)"
             if item.get("targeted_sites") else " (from the internal staff log, not a news source)" if item.get("exposure_basis") == checks.STAFF_LOG else "")
    doc.field("Exposure assessment", f"{item['exposure']}{basis}")
    impacts = item.get("impacts") or []
    if impacts:
        doc.add("Potential impact to ST Engineering:")
        for impact in impacts:
            doc.bullet(f"{impact['area']}: {checks.IMPACT_STATUS[impact['status']]}. {str(impact.get('basis') or '').strip()}".rstrip(), 1)
    else:
        doc.field("Potential impact to ST Engineering", checks.NO_IMPACT)
    doc.field("Severity", f"{item['severity']}{_corroboration(item)}")
    owner = f" ({item['owner']})" if item.get("owner") else ""
    doc.field("Recommended action", f"{item['action']}{owner}")
    doc.field("Rationale for recommended action", item.get("action_rationale") or "")
    _update(doc, item)
    _extras(doc, item, "A")


def _item_b(doc: _Doc, item: Mapping[str, Any]) -> None:
    doc.heading(f"[{item['item_id']}] {item['headline']}", 4)
    doc.field("Event", item["event"])
    doc.field("Affected entity", item["affected_entity"])
    _dates(doc, item)
    _sources(doc, item)
    doc.field("Why the incident is material", item["why_material"])
    _facts(doc, item)
    weakness = item["control_weakness"]
    if weakness != checks.NO_CONTROL_WEAKNESS and item.get("control_weakness_quote"):
        weakness += f" (\"{item['control_weakness_quote'].strip()}\")"
    doc.field("Key control weakness", weakness)
    doc.field("Risk category", ", ".join(item["risk_categories"]))
    doc.add("Lessons learnt for ST Engineering:")
    for lesson in item["lessons"]:
        doc.bullet(lesson, 1)
    exposure = item["existing_exposure"]
    if item.get("existing_exposure_note"):
        exposure += f". {item['existing_exposure_note']}"
    doc.field("Existing ST Engineering exposure", exposure)
    doc.field("Risk Radar, CSA and control coverage", item.get("coverage") or "Unable to determine")
    doc.field("Suggested internal review", ", ".join(item.get("internal_review") or []) or "No further action beyond monitoring")
    doc.field("Severity", f"{item['severity']}{_corroboration(item)}")
    _update(doc, item)
    _extras(doc, item, "B")


def _item_c(doc: _Doc, item: Mapping[str, Any]) -> None:
    doc.heading(f"[{item['item_id']}] {item['headline']}", 4)
    doc.field("Signal", item["event"])
    _dates(doc, item)
    _sources(doc, item)
    _facts(doc, item, "Evidence")
    doc.field("Why it matters", f"{item['why_it_matters']} {item.get('connection') or ''}".strip())
    doc.field("How it could evolve", item["how_it_could_evolve"])
    doc.add("Trigger points to watch:")
    for trigger in item["trigger_points"]:
        doc.bullet(trigger, 1)
    doc.field("Time horizon", item["time_horizon"])
    doc.field("Recommended response", item["response"])
    _update(doc, item)
    _extras(doc, item, "C")


def _extras(doc: _Doc, item: Mapping[str, Any], section: str) -> None:
    """What a same-topic merge brought in, and the 72-hour currency check."""
    related = item.get("related") or []
    if related:
        doc.add("Related reports on the same topic, merged into this item:")
        for row in related:
            headline = str(row["headline"]).rstrip(". ")
            title = doc.link(headline, row["url"]) if row.get("url") else headline
            doc.bullet(f"{title} ({long_date(row.get('publication_date'))}; first assessed as Section {row['section']})", 1)
    if section != "B" and item.get("lessons"):
        doc.add("Lessons learnt for ST Engineering (from a competitor or industry report on this topic):")
        for lesson in item["lessons"]:
            doc.bullet(lesson, 1)
    if section == "A" and item.get("trigger_points"):
        doc.add("Trigger points to watch:")
        for trigger in item["trigger_points"]:
            doc.bullet(trigger, 1)
    if item.get("pinned"):
        doc.field("Always reported", f"names {', '.join(item['pinned'])}; not merged or filtered out")
    if item.get("conflicts"):
        doc.add("Conflicting descriptions (confirm with the vendor advisory before acting):")
        for line in item["conflicts"]:
            doc.bullet(line, 1)
    reassessed = item.get("reassessed")
    if reassessed:
        text = {"UPDATED": "re-assessed with the newer report(s) below as sources; the item above reflects them",
                "NO_CHANGE": "re-assessed with the newer report(s) below; no material change to the facts above",
                "FAILED": "re-assessment failed its checks; the item above is as first assessed",
                "NOT_RUN": f"not re-assessed: {reassessed.get('reason')}"}[reassessed["outcome"]]
        doc.field("Currency re-assessment", f"{text} (" + "; ".join(reassessed["trigger"]) + ")")
    if item.get("people") and section != "B":
        doc.add("ST Engineering people in the country (internal staff log, R&A; country level, no site or city):")
        for line in item["people"]:
            doc.bullet(line, 1)
    currency = item.get("currency")
    if currency:
        if currency["latest"]:
            doc.add(f"Newer reports on {currency['entity']} since {long_date(currency['since'])} (headlines only, not assessed; read before acting):")
            for row in currency["latest"]:
                flag = f"; adverse terms: {', '.join(row['adverse_terms']).lower()}" if row.get("adverse_terms") else ""
                doc.bullet(f"{doc.link(row['title'], row['url'])} ({row['outlet']}, {long_date(row['published'])}{flag})", 1)
        else:
            doc.field("Currency check", f"No newer report on {currency['entity']} found since {long_date(currency['since'])}.")


def _repeats(doc: _Doc, repeats: Sequence[Mapping[str, Any]], section: str) -> None:
    for repeat in repeats:
        if repeat.get("section") == section:
            doc.add(f"{checks.NO_UPDATE} {repeat['headline']} (reported {long_date(repeat.get('previous_date'))}).")


def compose(report: Mapping[str, Any], *, markdown: bool = False) -> str:
    """The email body with its To and Subject lines, ready to paste."""
    doc = _Doc(markdown)
    run_date = date.fromisoformat(report["run_date"])
    window = report["window"]
    items = report["items"]
    doc.add(f"To: {TO}")
    doc.add(f"Subject: {subject(run_date)}")
    doc.add("")
    doc.add(GREETING)
    doc.add("")
    doc.add("Below is the Adverse News & Lessons Learnt Scan for ST Engineering, its subsidiaries, key customers, suppliers and competitors.")
    doc.add("")
    doc.field("Report date", long_date(run_date))
    basis = (f"since the previous completed scan of {long_date(window['previous'])}" if window.get("previous")
             else "no previous completed scan was accessible, so the seven days to the report date")
    doc.field("Reporting period", f"{long_date(window['start'])} to {long_date(window['end'])} ({basis})")
    counts = report["counts"]
    extracts = counts.get("search_extracts") or 0
    extract_text = f"{extracts} sources checked against search-engine text because the page did not open, " if extracts else ""
    doc.field("Coverage", f"{counts['pages']} pages screened, {counts['verified_pages']} source pages opened and checked, {extract_text}"
                          f"{sum(len(v) for v in items.values())} items reported")

    doc.heading("Immediate Escalation Summary")
    escalate = [i for i in items["A"] if i.get("escalate")]
    if escalate:
        for item in escalate:
            doc.bullet(f"[{item['item_id']}] {item['headline']} ({item['severity']}; {item['action']})")
    else:
        doc.add(checks.NO_ESCALATION)

    doc.heading("Section A: Adverse news directly relevant to ST Engineering")
    if items["A"]:
        for code, label in checks.CATEGORIES_A.items():
            chosen = [i for i in items["A"] if i["category"] == code]
            if chosen:
                doc.heading(f"{code}. {label}", 3)
                for item in chosen:
                    _item_a(doc, item)
    else:
        doc.add(checks.NO_DEVELOPMENTS)
    _repeats(doc, report.get("repeats") or [], "A")
    reviewed = report.get("own_reviewed") or []
    if reviewed:
        doc.add(f"ST Engineering mentions reviewed ({len(reviewed)}): none adverse. They are listed in the reviewer worksheet.")
    own = report.get("own_unreported") or []
    if own:
        doc.add("Items naming ST Engineering that could not be assessed in full (always listed; read the source):")
        for row in own:
            doc.bullet(f"{doc.link(row['headline'], row['url'])} (names {', '.join(row['names'])}; {row['reason']})", 1)

    doc.heading("Section B: Competitor and industry adverse news, lessons learnt")
    if items["B"]:
        for item in items["B"]:
            _item_b(doc, item)
    else:
        doc.add(checks.NO_DEVELOPMENTS)
    _repeats(doc, report.get("repeats") or [], "B")

    doc.heading("Section C: Emerging risk signals")
    if items["C"]:
        for item in items["C"]:
            _item_c(doc, item)
    else:
        doc.add(checks.NO_DEVELOPMENTS)
    _repeats(doc, report.get("repeats") or [], "C")

    attention = report["attention"]
    doc.heading("Management Attention Summary")
    from grit.core.overrides import READER_BAND_VOCAB_NOTE, product_band_alias

    doc.add(READER_BAND_VOCAB_NOTE)
    for label, key, empty in (("Immediate Attention", "immediate", checks.NO_IMMEDIATE), ("Enhanced Monitoring", "monitoring", checks.NO_MONITORING),
                              ("Competitor Lessons Learnt", "lessons", checks.NO_LESSONS)):
        alias = product_band_alias(label)
        heading = f"{label} (reader band: {alias}):" if alias else f"{label}:"
        doc.add(heading)
        for line in attention[key] or [empty]:
            doc.bullet(line, 1)
    doc.field("Overall Assessment", f"{report['overall'][0]}. {report['overall'][1]}")

    doc.heading("Final Validation")
    for line in report["validation"]:
        doc.bullet(line)
    doc.add("")
    doc.add(DISCLAIMER)
    doc.add("")
    doc.add(CLOSING[0])
    doc.add(CLOSING[1])
    return "\n".join(doc.lines).rstrip() + "\n"


def email_problems(text: str, run_date: date) -> list[str]:
    """The email accuracy check: recipient, subject line, greeting and closing."""
    found: list[str] = []
    lines = text.splitlines()
    if not lines or lines[0] != f"To: {TO}":
        found.append(f"The first line must be 'To: {TO}'.")
    if not _SUBJECT.search(text) or f"Subject: {subject(run_date)}" not in lines:
        found.append(f"The subject must be '{subject(run_date)}'.")
    if GREETING not in lines:
        found.append(f"The greeting must be '{GREETING}'.")
    if tuple(lines[-2:]) != CLOSING:
        found.append("The email must close with 'Kind Regards,' and 'Jony'.")
    return found


def eml(text: str) -> bytes:
    """The email as an unsent message, so Outlook opens it as a draft. The To and Subject lines move into the headers."""
    lines = text.splitlines()
    message = EmailMessage()
    message["To"] = lines[0].removeprefix("To: ")
    message["From"] = FROM
    message["Subject"] = lines[1].removeprefix("Subject: ")
    message["X-Unsent"] = "1"
    message.set_content("\n".join(lines[3:]) + "\n")
    return bytes(message)


def worksheet(report: Mapping[str, Any]) -> str:
    """The reviewer's worksheet: everything the email leaves out, and why."""
    lines = [f"# Adverse news scan review, {long_date(report['run_date'])}", ""]
    lines.append(f"**Status:** {report['status']}")
    for blocker in report.get("blockers") or []:
        lines.append(f"- BLOCKER: {blocker}")
    window = report["window"]
    lines += ["", f"**Reporting period:** {window['start']} to {window['end']}", "", "## Counts", ""]
    lines += [f"- {key}: {value}" for key, value in report["counts"].items()]
    lines += ["", "## Searches", ""]
    search = report.get("search") or {}
    lines.append(f"- Planned {search.get('planned', 0)}, sent {search.get('queries_sent', 0)}, already answered {search.get('skipped_answered', 0)}, "
                 f"corroboration {search.get('corroboration', 0)}, stopped: {search.get('stopped') or 'no'}.")
    lines += ["", "## Withheld", ""]
    withheld = report.get("withheld") or []
    lines += [f"- {w['headline']} ({w.get('section') or '-'}): {w['reason']}" + (f" {w['url']}" if w.get("url") else "") for w in withheld] or ["- None."]
    lines += ["", "## ST Engineering mentions reviewed, not adverse", ""]
    lines += [f"- {r['headline']} (names {', '.join(r['names'])}): {r['reason']} {r['url']}" for r in report.get("own_reviewed") or []] or ["- None."]
    lines += ["", "## Sources checked against search-engine text", ""]
    lines += [f"- [{item.get('item_id')}] {item.get('headline')}: {ref['outlet']} {ref['url']}"
              for section in ("A", "B", "C") for item in (report.get("items") or {}).get(section) or []
              for ref in item.get("sources") or [] if ref.get("basis") == "SEARCH_EXTRACT"] or ["- None."]
    lines += ["", "## Repeats with no material update", ""]
    lines += [f"- {r['headline']} (reported {r.get('previous_date')})" for r in report.get("repeats") or []] or ["- None."]
    lines += ["", "## Rule decisions and Critic objections", ""]
    notes = report.get("notes") or {}
    lines += [f"- {key}: {note}" for key, values in notes.items() for note in values] or ["- None."]
    lines += ["", "## Agent calls with violations", ""]
    lines += [f"- {c['agent']} {c['key']}: {c['status']}; " + "; ".join(c["violations"][:4]) for c in report.get("calls") or [] if c.get("violations")] or ["- None."]
    lines += ["", DISCLAIMER, ""]
    return "\n".join(lines)


CSV_COLUMNS = (
    "section", "item_id", "headline", "severity", "action", "country", "staffed", "Adverse",
    "url", "publisher", "why_adverse", "exposure_basis", "event_date", "publication_date",
)


def _item_countries(item: Mapping[str, Any]) -> list[str]:
    countries: list[str] = []
    for line in item.get("people") or []:
        name = str(line).split(":", 1)[0].strip()
        if name and name not in countries:
            countries.append(name)
    return countries


def signals_csv_rows(report: Mapping[str, Any]) -> list[list[str]]:
    """One row per included item's lead source (and further sources as extra rows with the same item fields)."""
    rows: list[list[str]] = []
    for section in ("A", "B", "C"):
        for item in (report.get("items") or {}).get(section) or []:
            countries = _item_countries(item)
            staffed = "Yes" if countries or item.get("exposure_basis") == checks.STAFF_LOG or item.get("people") else "No"
            why = item.get("why_adverse") or item.get("why_material") or item.get("why_it_matters") or ""
            refs = item.get("sources") or [{"url": "", "outlet": ""}]
            for ref in refs:
                rows.append([
                    section, str(item.get("item_id") or ""), str(item.get("headline") or ""),
                    str(item.get("severity") or ""), str(item.get("action") or item.get("response") or ""),
                    "; ".join(countries), staffed, "Yes",
                    str(ref.get("url") or ""), str(ref.get("outlet") or ""),
                    str(why), str(item.get("exposure_basis") or ""),
                    str(item.get("event_date") or ""), str(item.get("publication_date") or ""),
                ])
    return rows


def write_signals_csv(folder: Path, report: Mapping[str, Any]) -> Path:
    import csv

    path = folder / "adverse-signals.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(CSV_COLUMNS)
        out.writerows(signals_csv_rows(report))
    return path


def render_docx(report: Mapping[str, Any], path: Path) -> Path:
    """Word pack mirroring the email sections. Uses the shared navy table chrome."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    from grit.report.table_style import ADVERSE_SECTION, NAVY, header_row

    document = Document()
    style = document.styles["Normal"]
    style.font.name, style.font.size = "Calibri", Pt(10.5)
    run_date = date.fromisoformat(report["run_date"])
    title = document.add_heading(f"Adverse News & Lessons Learnt Scan | {long_date(run_date)}", level=0)
    for run in title.runs:
        run.font.color.rgb = NAVY
    document.add_paragraph(f"Status: {report.get('status')}")
    window = report.get("window") or {}
    document.add_paragraph(f"Reporting period: {long_date(window.get('start'))} to {long_date(window.get('end'))}")

    escalate = [i for i in (report.get("items") or {}).get("A") or [] if i.get("escalate")]
    document.add_heading("Immediate Escalation Summary", level=1)
    if escalate:
        for item in escalate:
            document.add_paragraph(f"[{item['item_id']}] {item['headline']} ({item['severity']}; {item['action']})", style="List Bullet")
    else:
        document.add_paragraph(checks.NO_ESCALATION)

    for section, heading in (
        ("A", "Section A: Adverse news directly relevant to ST Engineering"),
        ("B", "Section B: Competitor and industry adverse news, lessons learnt"),
        ("C", "Section C: Emerging risk signals"),
    ):
        document.add_heading(heading, level=1)
        items = (report.get("items") or {}).get(section) or []
        if not items:
            document.add_paragraph(checks.NO_DEVELOPMENTS)
            continue
        table = header_row(document, ADVERSE_SECTION, (7.0, 6.0, 3.5))
        for item in items:
            sources = item.get("sources") or []
            first = sources[0] if sources else {}
            source_line = f"{first.get('outlet') or 'source'}"
            if first.get("url"):
                source_line += f" — {first['url']}"
            event = f"[{item.get('item_id')}] {item.get('headline') or item.get('event') or ''}\n{source_line}\nEvent {long_date(item.get('event_date'))}; source {long_date(item.get('publication_date'))}"
            if item.get("people"):
                event += "\nStaff: " + "; ".join(item["people"])
            why = item.get("why_adverse") or item.get("why_material") or item.get("why_it_matters") or ""
            if item.get("exposure"):
                why += f"\nExposure: {item['exposure']}" + (f" ({item['exposure_basis']})" if item.get("exposure_basis") else "")
            if item.get("assessment"):
                why += f"\n{item['assessment']}"
            severity = f"{item.get('severity') or ''}"
            if item.get("action") or item.get("response"):
                severity += f"\n{item.get('action') or item.get('response')}"
            cells = table.add_row().cells
            for cell, value in zip(cells, (event, why, severity)):
                cell.text = ""
                run = cell.paragraphs[0].add_run(str(value))
                run.font.size = Pt(9)

    attention = report.get("attention") or {}
    document.add_heading("Management Attention Summary", level=1)
    from grit.core.overrides import READER_BAND_VOCAB_NOTE, product_band_alias

    document.add_paragraph(READER_BAND_VOCAB_NOTE)
    for label, key, empty in (("Immediate Attention", "immediate", checks.NO_IMMEDIATE),
                              ("Enhanced Monitoring", "monitoring", checks.NO_MONITORING),
                              ("Competitor Lessons Learnt", "lessons", checks.NO_LESSONS)):
        alias = product_band_alias(label)
        heading = f"{label} (reader band: {alias})" if alias else label
        document.add_paragraph(heading).runs[0].bold = True
        for line in attention.get(key) or [empty]:
            document.add_paragraph(line, style="List Bullet")
    overall = report.get("overall") or ("", "")
    document.add_paragraph(f"Overall Assessment: {overall[0]}. {overall[1]}")

    document.add_heading("Final Validation", level=1)
    for line in report.get("validation") or []:
        document.add_paragraph(line, style="List Bullet")
    note = document.add_paragraph(DISCLAIMER)
    note.runs[0].font.size, note.runs[0].font.color.rgb = Pt(9), RGBColor(0x55, 0x55, 0x55)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(path))
    return path


def write(folder: Path, report: Mapping[str, Any]) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    text = compose(report)
    ready = report["status"] == "READY"
    paths = {
        "email": folder / ("email.txt" if ready else "email-DRAFT-INCOMPLETE.txt"),
        "eml": folder / ("email.eml" if ready else "email-DRAFT-INCOMPLETE.eml"),
        "markdown": folder / "email.md",
        "review": folder / "review.md",
    }
    for stale in ("email.txt", "email.eml", "email-DRAFT-INCOMPLETE.txt", "email-DRAFT-INCOMPLETE.eml"):
        (folder / stale).unlink(missing_ok=True)
    paths["email"].write_text(text, encoding="utf-8")
    paths["eml"].write_bytes(eml(text))
    paths["markdown"].write_text(compose(report, markdown=True), encoding="utf-8")
    paths["review"].write_text(worksheet(report), encoding="utf-8")
    if ready:
        paths["docx"] = render_docx(report, folder / "adverse.docx")
        paths["csv"] = write_signals_csv(folder, report)
    return paths
