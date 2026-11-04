"""The business-area reader scan, offline: URS-TW runs first, then URS on the same date. No network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from grit.acquisition.brave_plan import BraveResponse
from grit.agents.reader_prompts import ASSESSOR_SYSTEM, CRITIC_SYSTEM, PROPOSER_SYSTEM, REPORTER_BOTTOM_LINE_SYSTEM, REPORTER_ITEMS_SYSTEM
from grit.pipeline import reader_scan
from grit.pool import Pool
from tests.test_reader_scan import ARTICLES, _Embedder

RUN_DATE = date(2026, 10, 7)
REASONING = "PROPOSER-WORKING-NOT-FOR-THE-BRIEF."
BA_ARTICLES = {
    "CBTC resignalling project delay": [
        {"title": "CBTC resignalling of the North-South line delayed again", "url": "https://www.straitstimes.com/singapore/cbtc-resignalling-delay",
         "description": "The CBTC resignalling works on the line will finish later than planned, the operator said.", "page_age": "2026-10-05T03:00:00"},
    ],
    "LAND TRANSPORT AUTHORITY": [
        {"title": "Land Transport Authority awards new rail contract in Singapore", "url": "https://www.channelnewsasia.com/singapore/lta-awards-rail-contract",
         "description": "The Land Transport Authority awarded a railway signalling contract for the Cross Island Line.", "page_age": "2026-10-06T03:00:00"},
    ],
    "rail cyberattack": [
        {"title": "European rail operator hit by ransomware cyberattack", "url": "https://www.reuters.com/technology/rail-ransomware-2026-10-04",
         "description": "A ransomware cyberattack disrupted ticketing systems at a European rail operator.", "page_age": "2026-10-04T03:00:00"},
    ],
    "Thailand rail project tender": [
        {"title": "Thailand opens tender for Bangkok rail extension", "url": "https://www.bangkokpost.com/business/rail-extension-tender",
         "description": "Thailand opened bidding for a new rail extension in Bangkok worth 40 billion baht.", "page_age": "2026-10-03T03:00:00"},
    ],
}


def _getter(url: str, params: dict[str, str]) -> BraveResponse:
    query = params["q"].strip('"')
    found = BA_ARTICLES.get(query) or ARTICLES.get(query) or []
    return BraveResponse(200, {"results": [dict(item, meta_url={"hostname": item["url"].split("/")[2]}) for item in found]}, {"x-ratelimit-limit": "50"})


def _model(system: str, payload: dict) -> dict:
    if system == ASSESSOR_SYSTEM:
        sources = payload["sources"]
        if payload["item"]["id"] == "URS-KEY-04" and sources:
            lob = payload["business_area"]["lines_of_business"][0]
            return {"movement": "ESCALATING", "summary": "A ransomware cyberattack disrupted ticketing at a European rail operator. [S1]", "source_ids": ["S1"],
                    "quotes": [{"source_id": "S1", "quote": "A ransomware cyberattack disrupted ticketing systems"}], "change_from_prior": "First assessment.",
                    "implication": f"A similar attack could disrupt systems that {lob} delivers to rail operators. [S1]"}
        return {"movement": "NO_MATERIAL_CHANGE", "summary": "No new development was found this week in the checked sources.", "source_ids": [], "quotes": []}
    if system == CRITIC_SYSTEM:
        return {"issues": []}
    if system == PROPOSER_SYSTEM:
        owners, controls = payload["owner_roles"], payload["theme"]["key_controls"]
        return {
            "reasoning": f"{REASONING} The attack tests the key controls.",
            "options": [
                {"kind": "STRENGTHEN_CONTROL", "option": "Consider testing rail ticketing systems against ransomware. [S1]", "owner": owners[0],
                 "builds_on": controls[:40].strip(), "trigger": "", "source_ids": ["S1"]},
                {"kind": "ADDRESS_GAP", "option": "Consider asking key suppliers to confirm their ransomware recovery plans.", "owner": "Group Information Security Officer (GISO)",
                 "builds_on": "", "trigger": "", "source_ids": ["S1"]},
                {"kind": "MONITOR", "option": "Consider continuing to monitor ransomware reports from rail operators.", "owner": "R&A business partner",
                 "builds_on": "", "trigger": "A rail operator in a URS market reports an attack.", "source_ids": []},
            ],
        }
    if system == REPORTER_ITEMS_SYSTEM:
        items = []
        for item in payload["items"]:
            source = payload["sources"][item["source_ids"][0]]
            items.append({"item_id": item["item_id"], "summary": f"{source['title']}. [{item['source_ids'][0]}]", "source_ids": [item["source_ids"][0]],
                          "quotes": [{"source_id": item["source_ids"][0], "quote": source["title"]}]})
        return {"items": items}
    if system == REPORTER_BOTTOM_LINE_SYSTEM:
        return {"bottom_line": f"Bottom line for {payload['facts']['brief']}.", "escalation": "Nothing warrants escalation."}
    raise AssertionError(system[:40])


def _run(pool: Pool, pack: str) -> reader_scan.ReaderRun:
    run = reader_scan.new_run(pool, RUN_DATE, pack)
    specs = reader_scan.plan(pool, run)
    reader_scan.acquire(pool, run, specs, getter=_getter, usage_path=run.folder.parent / "usage.json", sleep=lambda _s: None, log=lambda _m: None)
    reader_scan.embed(pool, run, embedder=_Embedder())
    reader_scan.assess(pool, run, score_harm_fn=lambda *a, **k: {"status": "NOT_RUN", "reason": "test"})
    reader_scan.run_agents(pool, run, client=reader_scan.stub_client(_model), model="stub", log=lambda _m: None)
    pool.finish_run(run.run_id)
    return run


def test_business_area_pack_end_to_end(tmp_path: Path, monkeypatch) -> None:
    from docx import Document

    from grit.profiles import load_profiles
    from grit.report.package import write_pack

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool = Pool(tmp_path / "grit.sqlite")
    _run(pool, "urs-tw")
    run = _run(pool, "urs")

    requests = {row["request_id"]: row for row in pool.management_requests()}
    assert requests["MR-URS-WB-R1"]["status"] == "ACTIVE" and requests["MR-URS-WB-F04"]["status"] == "PROPOSED", "status follows the workbook's own wording"
    rows = {row["title"]: row for row in pool.assessments(run.run_id, kept_only=True)}
    lta = rows["Land Transport Authority awards new rail contract in Singapore"]
    assert any(hit["item_type"] == "CUSTOMER" and hit["strong"] for hit in lta["exposure"]), "a BA pack names customers through the typed graph"
    cyber = rows["European rail operator hit by ransomware cyberattack"]
    assert "URS-KEY-04" in cyber["condition_ids"], "a theme keyword counts on a page that theme's own query found"
    cbtc = rows["CBTC resignalling of the North-South line delayed again"]
    assert any(hit["item_type"] == "REQUEST" for hit in cbtc["link"]["reader_hits"])

    themes = {row["condition_id"]: row for row in pool.conditions(run.run_id)}
    assert len(themes) == 14
    assert themes["URS-KEY-04"]["trend_label"] == "Escalating"
    assert themes["URS-TIER_2-01"]["ranking"]["coveredBy"] == "urs-tw"
    checks = {row["item_id"]: row for row in pool.exposure_checks(run.run_id)}
    assert checks["CUSTOMER-land-transport-authority"]["status"] == "FOUND"
    assert sum(1 for row in checks.values() if row["item_type"] == "ENTITY") >= 41, "every STE entity is listed, quiet or not"

    key = themes["URS-KEY-04"]
    assert key["implication"] and "could" in key["implication"] and "[SIG-" in key["implication"], "a key risk carries a cited implication"
    assert [option["kind"] for option in key["options"]] == ["STRENGTHEN_CONTROL", "ADDRESS_GAP", "MONITOR"]
    assert all(source.startswith("SIG-") for option in key["options"] for source in option["source_ids"]), "option sources are stored as signal ids"
    assert not themes["URS-KEY-01"]["options"], "the Proposer runs only on a key risk with validated, escalating or material evidence"
    proposer = [row for row in pool.execution_log(run.run_id) if row["actor"] == "Proposer"]
    assert len(proposer) == 1 and proposer[0]["status"] == "VALIDATED"
    assert REASONING in proposer[0]["detail"]["reasoning"], "the chain of thought is kept in the execution log"
    assert REASONING not in str(pool.conditions(run.run_id)), "and nowhere else"

    import dataclasses

    from grit.report.brief import build_brief

    profiles = load_profiles()
    plain = build_brief(pool, run.run_id, profiles["wilson"])
    entry = next(row for row in plain["conditions"] if row["condition_id"] == "URS-KEY-04")
    assert entry["full"] and entry["implication"]["plain"] and entry["options"] == [], "a profile without RESPONSE_OPTIONS sees no options"
    assert not next(row for row in plain["conditions"] if row["condition_id"] == "URS-TIER_2-01")["full"], "a quiet tier-2 theme stays one row"
    briefs = dict(profiles["wilson"].briefs)
    briefs["urs"] = {**briefs["urs"], "sections": [*briefs["urs"]["sections"], "RESPONSE_OPTIONS"], "formats": ["docx", "pptx", "xlsx"]}
    with_options = dataclasses.replace(profiles["wilson"], briefs=briefs)
    rich = write_pack(pool, run.run_id, with_options, folder=tmp_path / "wilson-options", submit=False)
    short = build_brief(pool, run.run_id, with_options)["meta"]["pack_short"]
    headers = ["News/Developments", "News/Information Source", "Date and Time", f"Relevance/Potential Impact to {short}", "Proposed Response/Management Consideration", "Priority/Trend"]

    document = Document(rich["files"]["docx"])
    table = next([[cell.text for cell in row.cells] for row in table.rows] for table in document.tables if table.rows[0].cells[0].text == "News/Developments")
    assert table[0] == headers, "the key risks keep the R&A reviewer's six columns"
    cyber = next(row for row in table[1:] if row[3] and "could disrupt" in row[3])
    assert cyber[1].startswith("1. ") and "4 Oct 2026" in cyber[2]
    assert "Consider testing rail ticketing systems" in cyber[4] and "Owner: R&A business partner" in cyber[4]
    assert "Trend: Escalating" in cyber[5] and "Key risk" in cyber[5]
    assert any(section.page_width > section.page_height for section in document.sections), "the six-column table sits on a landscape page"
    text = "\n".join([paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    assert "Not reviewed advice." in text and REASONING not in text
    markdown = Path(rich["files"]["md"]).read_text(encoding="utf-8")
    assert "| " + " | ".join(headers) + " |" in markdown and REASONING not in markdown

    from openpyxl import load_workbook

    sheet = load_workbook(rich["files"]["xlsx"])["Key risks"]
    assert [cell.value for cell in sheet[1]] == headers

    from pptx import Presentation

    from grit.report.deck_template import CONTENT_LAYOUT, COVER_LAYOUT
    from grit.report.office import deck_items

    deck = Presentation(rich["files"]["pptx"])
    slides = list(deck.slides)
    deck_brief = build_brief(pool, run.run_id, with_options)
    items = deck_items(deck_brief)
    from grit.report.office import summary_slide_count

    summaries = summary_slide_count(len(items))
    extra = int(bool(deck_brief["v2"]["escalating"])) + int(bool((deck_brief.get("scenarios") or {}).get("shifts")))
    assert len(slides) == 1 + summaries + extra + len(items), "a cover, one to three summary slides, the escalating-items and scenario slides when present, and one appendix slide per key risk or escalating theme"
    assert slides[0].slide_layout.name == COVER_LAYOUT and all(slide.slide_layout.name == CONTENT_LAYOUT for slide in slides[1:])
    texts = ["\n".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame) for slide in slides]
    assert "GRIT-Scan" in texts[0] and "SCAN WINDOW" in texts[0] and "REPORT OWNER" in texts[0]
    assert "AI-generated suggestions for consideration" in texts[0]
    summary = "\n".join(texts[1:1 + summaries])
    assert "Weekly Risk Radar" in texts[1] and "BOTTOM LINE" in texts[1] and "NEW & ESCALATING" in summary
    assert "What it means for" in summary
    detail = next(text for text in texts[1 + summaries:] if "Consider testing rail ticketing systems" in text)
    for label in ("WHAT IT MEANS FOR", "WHAT HAPPENED", "OPTIONS FOR MANAGEMENT CONSIDERATION", "RISK LINKAGE", "OWNER", "SOURCES", "Back to Executive Summary"):
        assert label in detail, label
    targets = lambda slide: {deck.slides.index(shape.click_action.target_slide) for shape in slide.shapes if shape.click_action.target_slide is not None}  # noqa: E731
    first = 1 + summaries + extra
    assert first in targets(slides[1]) and all(targets(slide) == {1} for slide in slides[first:]), "summary links to the appendix, and each appendix slide links back"
    assert not any(REASONING in text for text in texts)

    wilson = write_pack(pool, run.run_id, profiles["wilson"], folder=tmp_path / "wilson")
    document = Document(wilson["files"]["docx"])
    key_table = next(table for table in document.tables if table.rows[0].cells[0].text == "News/Developments")
    assert "Proposed Response/Management Consideration" not in [cell.text for cell in key_table.rows[0].cells], "no response column without RESPONSE_OPTIONS"
    text = "\n".join([paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    assert "GRIT-Scan: Urban Solutions" in text
    for heading in ("Standing risk themes (URS annual risks)", "Management-requested topics", "Entity reader packs", "STE entities by line of business", "Named supplier, customer and competitor watch",
                    "Escalating and elevated items", "Coverage matrix", "Completeness gate", "Source register"):
        assert heading in text, heading
    assert "CBTC resignalling of the North-South line delayed again" in text, "a P1 request match is listed under its request"
    assert "Bottom line for URS Taiwan weekly risk scan" in text, "the BA brief carries the entity pack's bottom line"
    assert "Rail signalling and CBTC project delivery" in text
    assert write_pack(pool, run.run_id, profiles["ksyong"], folder=tmp_path / "ksyong").get("skipped"), "the URS Taiwan head receives no business-area brief"
    assert write_pack(pool, run.run_id, profiles["gareth"], folder=tmp_path / "gareth")["package"]["package_id"] == f"PKG-{run.run_id}-gareth"


def test_a_covered_theme_s_child_signals_join_the_business_area_pool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool = Pool(tmp_path / "grit.sqlite")
    _run(pool, "urs-tw")
    run = _run(pool, "urs")
    cited = {row["signal_id"] for row in reader_scan.child_sources(pool, run, "urs-tw", reviewed=False)}
    assert cited, "the fixture gives the Taiwan pack signals a covered theme can cite"
    added = reader_scan.cover_child_sources(pool, run)
    sighted = {row["signal_id"] for row in pool.sightings(run.run_id) if row["lane"] == "COVERED_BY_CHILD"}
    assert cited <= sighted and set(added) <= cited
    assert reader_scan.cover_child_sources(pool, run) == [], "a second call adds nothing, so the evidence does not change"
