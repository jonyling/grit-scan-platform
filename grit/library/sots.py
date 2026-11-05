"""Build State of the Situation documents from the report library (spec 38).

For each topic and section: retrieve the best passages from the topic's reports, number them P1, P2, ... across the whole
document so one passage keeps one id, and have the Situation Analyst draft the section. The STE exposure section is written
by code from the business-area registers, never by the model. The output is a draft for Wilson to edit and sign off.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from grit.agents.situation import IN_BRIEF, NARRATIVE, SECTIONS, UNCERTAINTIES, Section, draft_section, marks, strip_marks
from grit.library.catalogue import PILOT, Topic, report, topic as get_topic
from grit.library.store import LIBRARY_DIR, Passage, retrieve
from grit.settings import CONFIG_DIR, OUTPUT_DIR

STATUS = "DRAFT_FOR_SIGN_OFF"
NOTE = "Drafted by GRIT-Scan from the report library. Every sentence cites a report page. Not reviewed. Wilson edits and signs it off before it is used."
BRIEF_SECTION = Section("IN_BRIEF", "In brief", IN_BRIEF, "The essential picture in three to five sentences, from the drafted sections only.", "")


def _theme_names() -> dict[str, str]:
    from grit.ontology.group_seed import CONDITIONS, PREFIX, SHIFTS

    names = {row["id"]: row["name"] for row in CONDITIONS}
    names |= {f"{PREFIX}-SHIFT-{number:02d}": name for number, (name, _keywords) in enumerate(SHIFTS, start=1)}
    return names


def _keyword(keyword: str) -> re.Pattern[str]:
    """A topic keyword starts a word. A short one, such as 'AI', is a whole word, so it does not match 'supply chain' or
    'aircraft'. A longer one can be a stem, such as 'cryptograph'."""
    end = r"(?![a-z0-9])" if len(keyword) <= 3 else ""
    return re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}{end}", 0 if len(keyword) <= 3 and keyword.isupper() else re.IGNORECASE)


def exposure(item: Topic, config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """What the business-area registers state about STE's exposure to the topic: entities in its countries, suppliers and
    customers it names, and the business areas' own risk themes that share its vocabulary. Written by code, so it never claims
    more than the registers hold."""
    from grit.ontology.pack import load_ontology_pack, manifests

    wanted = {country.casefold() for country in item.countries}
    fragments = [text.casefold() for text in (*item.counterparties, *item.countries)]

    def named(name: str) -> bool:
        folded = name.casefold()
        return any(re.search(rf"(?<![a-z]){re.escape(fragment)}(?![a-z])", folded) for fragment in fragments)

    areas, table = [], []
    for name, manifest in sorted(manifests(config_dir).items()):
        if manifest.get("level") != "BA":
            continue
        pack = load_ontology_pack(config_dir, name)
        table.append(_exposure_row(name, pack, item))
        entities = [row for row in pack.exposure.get("entities") or [] if wanted & _entity_places(row)]
        suppliers = [row["supplierName"] for row in pack.exposure.get("suppliers") or [] if named(row["supplierName"])]
        customers = [row["customerName"] for row in pack.exposure.get("customers") or [] if named(row["customerName"])]
        themes = [row for row in (pack.themes or {}).get("themes") or [] if any(_keyword(keyword).search(f"{row['themeName']} {' '.join(row.get('keywords') or [])}") for keyword in item.keywords)]
        if entities or suppliers or customers or themes:
            areas.append({
                "pack": name, "label": pack.label,
                "entities": [{"name": row["entityName"], "country": row.get("countryOfIncorporation"), "lob": row.get("lob"),
                              **({"assetCountries": [country for country in row["assetCountries"] if country.casefold() in wanted]} if row.get("assetCountries") else {})}
                             for row in entities],
                "suppliers": suppliers, "customers": customers,
                "themes": [{"id": row["themeId"], "name": row["themeName"], "tier": row.get("tier")} for row in themes],
            })
    return {"countries": list(item.countries), "counterparties": list(item.counterparties), "business_areas": areas, "table": table,
            "basis": "The business-area registers in config/. A filled cell is proposed until the business area confirms it. A blank cell is a fact the register does not hold."}


_TAIWAN = {"taiwan", "chinese taipei"}


def _entity_places(row: Mapping[str, Any]) -> set[str]:
    """Where an entity is incorporated and where it holds assets, folded for comparison."""
    return {str(country or "").strip().casefold() for country in [row.get("countryOfIncorporation"), *(row.get("assetCountries") or [])] if country}
_CHINA = {"china", "prc", "people's republic of china", "mainland china"}


def _cell(text: str, confirmed: bool) -> dict[str, str]:
    text = " ".join(str(text or "").split())
    if not text:
        return {"text": "", "mark": ""}
    return {"text": text, "mark": "BA-confirmed" if confirmed else "proposed"}


def _stated(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _exposure_row(name: str, pack: Any, item: Topic) -> dict[str, Any]:
    """One business area. Taiwan and China entities come from the register's country. Staff, dependence, concentration, and
    scenario ratings are copied only when the register states them."""
    exposure_register = pack.exposure or {}
    confirmed = str(exposure_register.get("exposureStatus") or "").casefold() == "ba-confirmed"
    entities = list(exposure_register.get("entities") or [])
    fragments = [text.casefold() for text in (*item.counterparties, *item.countries)]

    def named(label: str) -> bool:
        folded = label.casefold()
        return any(re.search(rf"(?<![a-z]){re.escape(fragment)}(?![a-z])", folded) for fragment in fragments)

    def place(country: str) -> str:
        return str(country or "").strip().casefold()

    def located(places: set[str]) -> list[str]:
        """Incorporated there, or holding an asset there (a finance asset listing), marked so the two are not confused."""
        names = []
        for row in entities:
            if place(row.get("countryOfIncorporation")) in places:
                names.append(row["entityName"])
            elif any(place(country) in places for country in row.get("assetCountries") or []):
                names.append(f"{row['entityName']} (asset there)")
        return names

    taiwan, china = located(_TAIWAN), located(_CHINA)
    staff = [f"{row['entityName']}: {_stated(row, 'staff', 'staffCount', 'headcount')}" for row in entities if _stated(row, "staff", "staffCount", "headcount")]
    suppliers = [row["supplierName"] for row in exposure_register.get("suppliers") or [] if named(row["supplierName"])]
    ratings = exposure_register.get("scenarioRatings") if isinstance(exposure_register.get("scenarioRatings"), Mapping) else {}
    rating_cells = {case: _cell(ratings.get(case) if ratings.get(case) in {"High", "Medium", "Low"} else "", confirmed) for case in ("BEST", "MEDIUM", "WORST")}
    filled = bool(taiwan or china or staff or suppliers or any(cell["text"] for cell in (
        _cell(_stated(exposure_register, "chipDependence", "componentDependence"), confirmed),
        _cell(_stated(exposure_register, "revenueConcentration"), confirmed),
        _cell(_stated(exposure_register, "assetConcentration"), confirmed),
        *rating_cells.values(),
    )))
    return {
        "pack": name, "label": pack.label,
        "taiwan_entities": _cell("; ".join(taiwan), confirmed),
        "china_entities": _cell("; ".join(china), confirmed),
        "staff": _cell("; ".join(staff), confirmed),
        "key_suppliers": _cell("; ".join(suppliers), confirmed),
        "chip_dependence": _cell(_stated(exposure_register, "chipDependence", "componentDependence"), confirmed),
        "revenue_concentration": _cell(_stated(exposure_register, "revenueConcentration"), confirmed),
        "asset_concentration": _cell(_stated(exposure_register, "assetConcentration"), confirmed),
        "ratings": rating_cells,
        "status": "BA-confirmed" if confirmed and filled else ("proposed" if filled else ""),
    }


def gains_for(item: Topic, config_dir: Path = CONFIG_DIR) -> list[dict[str, str]]:
    """Gain hypotheses already stored on the reviewed scenarios for this topic. An absent line stays absent."""
    from grit.pipeline.scenarios import load_register

    register = load_register("ste", config_dir)
    if not register:
        return []
    wanted = set(item.themes)
    lines = []
    for shift in register.get("shifts") or []:
        if shift.get("themeId") not in wanted:
            continue
        for case in shift.get("cases") or []:
            text = " ".join(str(case.get("where_ste_could_gain") or "").split())
            if text:
                lines.append({"shift": shift.get("name") or shift["themeId"], "case": case.get("title") or case.get("case") or "", "text": text})
    return lines


def _passages(item: Topic, section: Section, *, library_dir: Path, embedder: Any | None, k: int) -> list[tuple[Passage, float]]:
    """Passages for one section. History asks a second question and keeps more passages, still only from this topic's reports."""
    if not item.reports:
        return []
    queries = [f"{item.name}. {item.question} {section.angle}"]
    limit = k
    if section.key == "HISTORY":
        queries.append(f"{item.name}. earlier history official statistics turning points before the current decade")
        limit = max(k, 20)
    found: list[tuple[Passage, float]] = []
    seen: set[str] = set()
    for query in queries:
        for passage, score in retrieve(query, item.reports, k=k, library_dir=library_dir, embedder=embedder):
            if passage.passage_id in seen:
                continue
            seen.add(passage.passage_id)
            found.append((passage, score))
            if len(found) >= limit:
                return found
    return found


def _table(found: Sequence[tuple[Passage, float]], numbering: dict[str, str], table: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The section's passages, numbered across the whole document."""
    local = {}
    for passage, score in found:
        if passage.passage_id not in numbering:
            numbering[passage.passage_id] = f"P{len(numbering) + 1}"
            source = report(passage.report_id)
            table[numbering[passage.passage_id]] = {
                "passage_id": passage.passage_id, "report_id": passage.report_id, "report": source.title, "publisher": source.publisher, "edition": source.edition,
                "page": passage.page, "page_label": passage.page_label, "access": source.access, "aliases": list(source.aliases), "text": passage.text, "score": round(score, 4),
            }
        local[numbering[passage.passage_id]] = table[numbering[passage.passage_id]]
    return local


def redraft_sections(
    document: Mapping[str, Any], item: Topic, keys: Sequence[str], *, client: Any, model: str, embedder: Any | None = None,
    library_dir: Path = LIBRARY_DIR, k: int = 12, log: Callable[[str], None] = print, record: Callable[[dict[str, Any]], None] = lambda entry: None,
) -> dict[str, Any]:
    """Draft the named sections of a saved document again. The other sections and the In brief stay as they were, and a
    passage keeps the id it already has."""
    table = {key: dict(value) for key, value in document["passages"].items()}
    numbering = {row["passage_id"]: key for key, row in table.items()}
    brief = {"id": item.topic_id, "name": item.name, "question": item.question}
    sections = []
    for row in document["sections"]:
        section = next((entry for entry in SECTIONS if entry.key == row["key"]), None)
        if section is None or row["key"] not in keys:
            sections.append(row)
            continue
        found = _passages(item, section, library_dir=library_dir, embedder=embedder, k=k)
        local = _table(found, numbering, table)
        result, call = draft_section(client, model, topic=brief, section=section, passages=local)
        record(call.as_record())
        log(f"  {item.topic_id:22} {section.key:14} {result['status']:12} passages {len(local)} (redrafted)")
        sections.append({"key": section.key, "title": section.title, "kind": section.kind, **result})
    return {**document, "sections": sections, "passages": table}


def build_topic(
    item: Topic, *, client: Any, model: str, embedder: Any | None = None, library_dir: Path = LIBRARY_DIR, config_dir: Path = CONFIG_DIR,
    k: int = 12, log: Callable[[str], None] = print, record: Callable[[dict[str, Any]], None] = lambda entry: None,
) -> dict[str, Any]:
    numbering: dict[str, str] = {}
    table: dict[str, dict[str, Any]] = {}
    brief = {"id": item.topic_id, "name": item.name, "question": item.question}
    sections = []
    for section in SECTIONS:
        found = _passages(item, section, library_dir=library_dir, embedder=embedder, k=k)
        local = _table(found, numbering, table)
        result, call = draft_section(client, model, topic=brief, section=section, passages=local)
        record(call.as_record())
        log(f"  {item.topic_id:22} {section.key:14} {result['status']:12} passages {len(local)}")
        sections.append({"key": section.key, "title": section.title, "kind": section.kind, **result})
    usable = [row for row in sections if row["status"] == "DRAFTED"]
    cited = sorted({mark for row in usable for mark in marks(json.dumps(row["draft"]))}, key=lambda mark: int(mark[1:]))
    summary_input = [{"title": row["title"], "draft": {key: row["draft"].get(key) for key in ("paragraphs", "items")}} for row in usable]
    in_brief, call = draft_section(client, model, topic=brief, section=BRIEF_SECTION, passages={mark: table[mark] for mark in cited}, drafted=summary_input)
    record(call.as_record())
    log(f"  {item.topic_id:22} {'IN_BRIEF':14} {in_brief['status']:12} passages {len(cited)}")
    names = _theme_names()
    return {
        "topicId": item.topic_id, "name": item.name, "question": item.question, "status": STATUS, "note": NOTE, "coverage": item.coverage,
        "covers": [{"id": theme, "name": names.get(theme, theme)} for theme in item.themes], "gapSources": list(item.gap_sources),
        "inBrief": in_brief, "sections": sections, "gains": gains_for(item, config_dir), "exposure": exposure(item, config_dir), "passages": table,
        "reports": [{"id": report(report_id).report_id, "title": report(report_id).title, "publisher": report(report_id).publisher, "edition": report(report_id).edition,
                     "access": report(report_id).access, "url": report(report_id).url} for report_id in item.reports],
    }


# Rendering -------------------------------------------------------------------------------------------------------


_MONTHS = {name: name[:3] for name in ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")}


def _show_cell(cell: Mapping[str, str]) -> str:
    if not cell.get("text"):
        return ""
    return f"{cell['text']} ({cell['mark']})" if cell.get("mark") else cell["text"]


def _edition(text: str) -> str:
    return " ".join(_MONTHS.get(word, word) for word in str(text).split())


def render_text(text: str, table: Mapping[str, Mapping[str, Any]]) -> str:
    """[P3, P7] becomes (PwC Semiconductor, Nov 2025, pp. 18, 64; McKinsey Tech Trends, Sep 2026, p. 45). The Sources list
    gives each report's full title."""
    from grit.library.catalogue import report as get_report, short_name

    def swap(match: re.Match[str]) -> str:
        pages: dict[str, list[tuple[str, int]]] = {}
        for part in match.group(1).split(","):
            row = table.get(part.strip())
            if row:
                pages.setdefault(row["report_id"], [])
                if (row["page_label"], row["page"]) not in pages[row["report_id"]]:
                    pages[row["report_id"]].append((row["page_label"], row["page"]))
        labels = []
        for report_id, found in pages.items():
            item = get_report(report_id)
            label, numbers = found[0][0], sorted(page for _label, page in found)
            plural = {"p.": "pp.", "slide": "slides", "part": "parts"}[label] if len(numbers) > 1 else label
            labels.append(f"{short_name(item)}, {_edition(item.edition)}, {plural} {', '.join(str(number) for number in numbers)}")
        return f"({'; '.join(labels)})" if labels else ""

    return re.sub(r"\s+([.,;])", r"\1", re.sub(r"\[(P\d{1,3}(?:\s*,\s*P\d{1,3})*)\]", swap, text)).strip()


def _cited_reports(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    used: dict[str, set[str]] = {}
    for mark in marks(json.dumps([document["inBrief"].get("draft"), *(row.get("draft") for row in document["sections"])])):
        row = document["passages"].get(mark)
        if row:
            used.setdefault(row["report_id"], set()).add(f"{row['page_label']} {row['page']}")
    return [{**item, "pages": sorted(used[item["id"]], key=lambda text: int(text.split()[-1]))} for item in document["reports"] if item["id"] in used]


def to_markdown(document: Mapping[str, Any]) -> str:
    table = document["passages"]
    lines = [f"# State of the Situation: {document['name']}", "", f"*Status: draft for sign-off. {document['note']}*", "", f"**Question.** {document['question']}", ""]
    lines += [f"**Covers:** {', '.join(f'{row['name']} ({row['id']})' for row in document['covers'])}.", ""]
    if document["coverage"] != "GOOD":
        lines += [f"**Library coverage: {document['coverage'].lower()}.** " + (f"Proposed sources to close the gap: {'; '.join(document['gapSources'])}." if document["gapSources"] else ""), ""]
    brief = document["inBrief"]
    lines += ["## In brief", "", *(render_text(text, table) for text in (brief.get("draft") or {}).get("paragraphs") or [])] if brief["status"] == "DRAFTED" else ["## In brief", "", "Withheld: the summary failed its checks."]
    lines.append("")
    for row in document["sections"]:
        lines += [f"## {row['title']}", ""]
        draft = row.get("draft") or {}
        if row["status"] == "WITHHELD":
            lines += ["Withheld: the draft failed its checks twice. " + "; ".join(row["violations"][:2]), ""]
        elif row["kind"] == NARRATIVE:
            lines += [paragraph for text in draft.get("paragraphs") or [] for paragraph in (render_text(text, table), "")]
        else:
            for entry in draft.get("items") or []:
                if row["kind"] == UNCERTAINTIES:
                    lines.append(f"- **{entry['uncertainty']}** {render_text(entry['why_it_matters'], table)}")
                else:
                    lines.append(f"- **{entry['indicator']}.** " + (f"Latest: {render_text(entry['reading'], table)}" if entry.get("reading") else "No reading in the library."))
            lines.append("")
            if draft.get("not_covered"):
                lines += [f"*Not covered by the library:* {strip_marks(draft['not_covered'])}", ""]
        if row["status"] != "WITHHELD" and row["kind"] == NARRATIVE and draft.get("not_covered"):
            lines += [f"*Not covered by the library:* {strip_marks(draft['not_covered'])}", ""]
        if row["key"] == "HISTORY" and document.get("gapSources"):
            lines += ["Authoritative sources proposed beyond this library. A named person adds the file before a sentence can cite one:", ""]
            lines += [f"- {source} (proposed)" for source in document["gapSources"]]
            lines.append("")
    lines += ["## Where STE could gain", ""]
    gains = document.get("gains") or []
    if gains:
        lines += ["Hypothesis: to validate with BAs.", ""]
        lines += [f"- **{item['shift']} — {item['case']}.** {item['text']}" for item in gains]
    else:
        lines.append("No reviewed scenario states where STE could gain.")
    lines.append("")
    lines += ["## STE exposure", "", f"*{document['exposure']['basis']}*", ""]
    table = document["exposure"].get("table") or []
    if table:
        headers = ("Business area", "Taiwan entities", "China entities", "Staff", "Key suppliers", "Chip or component dependence", "Revenue concentration", "Asset concentration", "Best", "Medium", "Worst", "Status")
        lines += ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        for area in table:
            cells = [
                area["label"], _show_cell(area["taiwan_entities"]), _show_cell(area["china_entities"]), _show_cell(area["staff"]),
                _show_cell(area["key_suppliers"]), _show_cell(area["chip_dependence"]), _show_cell(area["revenue_concentration"]),
                _show_cell(area["asset_concentration"]), _show_cell(area["ratings"]["BEST"]), _show_cell(area["ratings"]["MEDIUM"]),
                _show_cell(area["ratings"]["WORST"]), area.get("status") or "",
            ]
            lines.append("| " + " | ".join(cell.replace("|", "/") for cell in cells) + " |")
        lines.append("")
    if not document["exposure"]["business_areas"]:
        lines += ["No business-area register names an entity in the topic's countries or a risk theme that shares its vocabulary.", ""]
    for area in document["exposure"]["business_areas"]:
        lines.append(f"**{area['label']}.**")
        if area["entities"]:
            lines.append(f"- Entities in the topic's countries: {'; '.join(f'{row['name']} ({row['country']})' for row in area['entities'])}.")
        if area.get("suppliers"):
            lines.append(f"- Registered suppliers: {'; '.join(area['suppliers'])}.")
        if area.get("customers"):
            lines.append(f"- Registered customers: {'; '.join(area['customers'])}.")
        if area["themes"]:
            lines.append(f"- Related risk themes: {'; '.join(f'{row['name']} ({row['id']})' for row in area['themes'])}.")
        lines.append("")
    lines += ["## Sources", ""]
    for item in _cited_reports(document):
        lines.append(f"- {item['publisher']}, *{item['title']}*, {item['edition']}. {'Internal: cite inside STE only. ' if item['access'] == 'INTERNAL' else ''}Pages cited: {', '.join(item['pages'])}." + (f" {item['url']}" if item.get("url") else ""))
    lines += ["", "Signed off by: ____________________    Date: ____________", ""]
    return "\n".join(lines)


def to_docx(document: Mapping[str, Any], path: Path) -> None:
    """The same content as the Markdown, as a Word document Wilson can edit with tracked changes."""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    for line in to_markdown(document).splitlines():
        if line.startswith("# "):
            doc.add_heading(line[2:], level=0)
        elif line.startswith("## "):
            doc.add_heading(line[3:], level=1)
        elif line.startswith("- "):
            _runs(doc.add_paragraph(style="List Bullet"), line[2:])
        elif line.strip():
            paragraph = doc.add_paragraph()
            _runs(paragraph, line)
            if line.startswith("*") and line.endswith("*"):
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    doc.save(str(path))


def _runs(paragraph: Any, text: str) -> None:
    """Markdown bold and italics as Word runs."""
    for part in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text):
        if not part:
            continue
        if part.startswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("*") and part.endswith("*"):
            paragraph.add_run(part[1:-1]).italic = True
        else:
            paragraph.add_run(part)


def write(document: Mapping[str, Any], folder: Path, audit: Sequence[Mapping[str, Any]] = ()) -> dict[str, str]:
    folder.mkdir(parents=True, exist_ok=True)
    stem = folder / document["topicId"]
    stem.with_suffix(".json").write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    stem.with_suffix(".md").write_text(to_markdown(document), encoding="utf-8")
    to_docx(document, stem.with_suffix(".docx"))
    if audit:
        with (folder / "audit.jsonl").open("a", encoding="utf-8") as handle:
            for entry in audit:
                handle.write(json.dumps({"topic": document["topicId"], **entry}, ensure_ascii=False) + "\n")
    return {kind: str(stem.with_suffix(f".{kind}")) for kind in ("docx", "md", "json")}


def _main() -> None:
    import argparse

    from grit.cli import _client, _model

    parser = argparse.ArgumentParser(description="Draft State of the Situation documents from the report library, for sign-off.")
    chosen = parser.add_mutually_exclusive_group()
    chosen.add_argument("--topic", nargs="+", help="Topic ids, such as SOTS-MIDEAST.")
    chosen.add_argument("--all", action="store_true", help="All 12 topics. Default: the four pilot topics.")
    parser.add_argument("--model")
    parser.add_argument("--print", action="store_true", help="Print each document as Markdown.")
    parser.add_argument("--render-only", metavar="DATE", help="Re-render the drafts saved on DATE, with the exposure recomputed. No model call.")
    parser.add_argument("--redraft", nargs="+", metavar="SECTION", help="Draft only these sections again, such as ACTORS, in the drafts saved on --from.")
    parser.add_argument("--from", dest="saved", metavar="DATE", default=date.today().isoformat(), help="The date of the saved drafts for --redraft. Default today.")
    args = parser.parse_args()
    from grit.library.catalogue import TOPICS

    topics = [get_topic(item) for item in args.topic] if args.topic else list(TOPICS) if args.all else [get_topic(item) for item in PILOT]
    if args.render_only:
        folder = OUTPUT_DIR / "sots" / args.render_only
        for item in topics:
            path = folder / f"{item.topic_id}.json"
            if not path.is_file():
                print(f"{item.topic_id}: no draft on {args.render_only}.")
                continue
            document = json.loads(path.read_text(encoding="utf-8")) | {"exposure": exposure(item)}
            print(f"{item.topic_id}: re-rendered -> {write(document, folder)['docx']}")
            if args.print:
                print(to_markdown(document))
        return
    client, model = _client(), _model(args.model)
    if args.redraft:
        folder = OUTPUT_DIR / "sots" / args.saved
        for item in topics:
            path = folder / f"{item.topic_id}.json"
            if not path.is_file():
                print(f"{item.topic_id}: no draft on {args.saved}.")
                continue
            audit: list[dict[str, Any]] = []
            document = redraft_sections(json.loads(path.read_text(encoding="utf-8")), item, [key.upper() for key in args.redraft], client=client, model=model, record=audit.append)
            files = write(document | {"exposure": exposure(item)}, folder, audit)
            print(f"{item.topic_id}: redrafted -> {files['docx']}")
            if args.print:
                print(to_markdown(document | {"exposure": exposure(item)}))
        return
    folder = OUTPUT_DIR / "sots" / date.today().isoformat()
    for item in topics:
        audit: list[dict[str, Any]] = []
        document = build_topic(item, client=client, model=model, record=audit.append)
        files = write(document, folder, audit)
        counts = {status: sum(1 for row in [document["inBrief"], *document["sections"]] if row["status"] == status) for status in ("DRAFTED", "NOT_COVERED", "WITHHELD")}
        print(f"{item.topic_id}: {counts} -> {files['docx']}")
        if args.print:
            print(to_markdown(document))


def _ingest_main() -> None:
    import argparse

    from grit.library.store import ingest

    parser = argparse.ArgumentParser(description="Read the reports in secrets/reports/ into the library and embed their passages.")
    parser.parse_args()
    summary = ingest()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _main()
