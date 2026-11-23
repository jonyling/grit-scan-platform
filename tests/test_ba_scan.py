"""The business-area reader scan, offline: URS-TW runs first, then URS on the same date. No network."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from grit.acquisition.brave_plan import BraveResponse
from grit.agents.reader_prompts import EVENT_ROW_SYSTEM, REPORTER_BOTTOM_LINE_SYSTEM, REPORTER_ITEMS_SYSTEM
from grit.pipeline import reader_scan
from grit.pool import Pool
from tests.test_reader_scan import ARTICLES, _Embedder

RUN_DATE = date(2026, 10, 7)
REASONING = "EVENT-ASSESSOR-WORKING-NOT-FOR-THE-BRIEF."
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
    if system == EVENT_ROW_SYSTEM:
        source = payload["sources"]["S1"]
        anchor = next(iter(payload["business_area"].get("lines_of_business") or []), payload["business_area"]["name"])
        step = {"date": source["date"], "text": f"{source['title']}.", "source_id": "S1"}
        return {"reasoning": REASONING, "item": source["title"][:60].rsplit(" ", 1)[0], "summary": f"On {source['date']}, the report said: {source['title']}. [S1]",
                "sequence": [step, step], "implications": f"A similar development could affect {anchor}. [S1]", "opportunity": None,
                "response": {"action": "Consider checking the exposure with the business.", "owner": payload["owners"][0]}, "source_ids": ["S1"]}
    if system == REPORTER_ITEMS_SYSTEM:
        items = []
        for item in payload["items"]:
            source = payload["sources"][item["source_ids"][0]]
            items.append({"item_id": item["item_id"], "summary": f"{source['title']}. [{item['source_ids'][0]}]", "source_ids": [item["source_ids"][0]],
                          "lesson": "Consider reviewing how the business tracks this customer's awards.",
                          "quotes": [{"source_id": item["source_ids"][0], "quote": source["title"]}]})
        return {"items": items}
    if system == REPORTER_BOTTOM_LINE_SYSTEM:
        assert "this_week_items" in payload["facts"]
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

    from grit.report.package import write_pack

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool = Pool(tmp_path / "grit.sqlite")
    _run(pool, "urs-tw")
    run = _run(pool, "urs")

    requests = {row["request_id"]: row for row in pool.management_requests()}
    assert requests["MR-URS-WB-R1"]["status"] == "ACTIVE" and requests["MR-URS-WB-F04"]["status"] == "ACTIVE", "every workbook topic is switched on"
    rows = {row["title"]: row for row in pool.assessments(run.run_id, kept_only=True)}
    lta = rows["Land Transport Authority awards new rail contract in Singapore"]
    assert any(hit["item_type"] == "CUSTOMER" and hit["strong"] for hit in lta["exposure"]), "a BA pack names customers through the typed graph"
    cyber = rows["European rail operator hit by ransomware cyberattack"]
    assert "URS-KEY-04" in cyber["condition_ids"], "a theme keyword counts on a page that theme's own query found"
    cbtc = rows["CBTC resignalling of the North-South line delayed again"]
    assert any(hit["item_type"] == "REQUEST" for hit in cbtc["link"]["reader_hits"])

    assert pool.conditions(run.run_id) == [], "a business-area brief is event-led: no per-risk Assessor runs"
    checks = {row["item_id"]: row for row in pool.exposure_checks(run.run_id)}
    assert checks["CUSTOMER-land-transport-authority"]["status"] == "FOUND"
    assert checks["CUSTOMER-land-transport-authority"]["lesson"].startswith("Consider")
    assert sum(1 for row in checks.values() if row["item_type"] == "ENTITY") >= 41, "every STE entity is listed, quiet or not"
    drafted = pool.run(run.run_id)["config"]["event_rows"]
    sections = {row["item"]: row["section"] for row in drafted.values()}
    assert sections[next(item for item in sections if item.startswith("CBTC"))] == "MANAGEMENT_FOCUS", "a request match goes to Management Focus"
    assert sections[next(item for item in sections if item.startswith("European rail"))] == "REGISTER_RISK", "a register risk goes to table 2a"
    assert not any(row["actor"] in {"Assessor", "Proposer"} for row in pool.execution_log(run.run_id))

    from grit.profiles import load_profiles
    from grit.report.docx_brief import SECTION_HEADERS, WATCH_HEADERS

    profiles = load_profiles()
    pool.add_qa(run.run_id, item="REPORT:gareth", correction="An objection to an earlier draft of another report.", origin="WEEKLY_CRITIC")
    wilson = write_pack(pool, run.run_id, profiles["wilson"], folder=tmp_path / "wilson")
    assert set(wilson["files"]) == {"docx", "pptx", "xlsx"}, "a business-area pack writes no Markdown or JSON file"
    document = Document(wilson["files"]["docx"])
    assert document.tables[0].rows[0].cells[0].paragraphs[0].text == "SUMMARY"
    assert "Bottom line for Urban Solutions weekly risk scan" in document.tables[0].rows[0].cells[0].text
    headings = [paragraph.text for paragraph in document.paragraphs if paragraph.style.name.startswith("Heading")]
    assert headings == ["Exposure & Ask", "1. Management Focus Topics", "2. URS Risks", "a. Key, T2 & Emerging", "b. Other Risk Themes", "3. Trends/Developments",
                        "Appendix A", "1. Look-ahead: the next 30 days", "2. Scenario outlook: best, medium and worst case", "3. Jurisdiction coverage",
                        "4. Named supplier, customer and competitor watch", "Appendix B: Source Register"]
    tables = [[[cell.text for cell in row.cells] for row in table.rows] for table in document.tables[1:]]
    sections = [table for table in tables if table[0] == list(SECTION_HEADERS)]
    assert len(sections) == 4, "the four tables share one set of columns"
    focus = sections[0]
    assert any("CBTC" in row[1] and "[MF: Rail signalling and CBTC project delivery]" in row[1] for row in focus[1:])
    assert all(row[4].startswith("Consider") and "Owner: " in row[4] and row[5].startswith("S") for table in sections for row in table[1:])
    watch = next(table for table in tables if table[0] == list(WATCH_HEADERS))
    assert any(row[0] == "LAND TRANSPORT AUTHORITY" and row[3].startswith("Consider") for row in watch[1:])
    text = "\n".join([paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    assert REASONING not in text and "earlier draft of another report" not in text
    for gone in ("Exposure snapshot", "Coverage matrix", "Completeness gate", "Recommended action posture", "Entity reader packs", "QA and correction log"):
        assert gone not in text, gone

    from openpyxl import load_workbook

    workbook = load_workbook(wilson["files"]["xlsx"])
    assert workbook.sheetnames[:5] == [
        "Exposure & Ask", "Management Focus Topics", "Key, T2 & Emerging",
        "Other Risk Themes", "Trends-Developments",
    ]
    assert [cell.value for cell in workbook["Named watch"][1]] == list(WATCH_HEADERS)

    from pptx import Presentation

    texts = ["\n".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame) for slide in Presentation(wilson["files"]["pptx"]).slides]
    assert "GRIT-Scan" in texts[0] and "SCAN WINDOW" in texts[0] and "AI-generated suggestions for consideration" in texts[0]
    assert "SUMMARY" in texts[1] and "Bottom line for Urban Solutions" in texts[1]
    assert any("MANAGEMENT FOCUS TOPICS" in text for text in texts[2:])
    assert not any(REASONING in text for text in texts)
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


def test_jurisdictions_list_where_the_business_area_has_entities() -> None:
    found = reader_scan.jurisdictions(reader_scan._load_pack("urs"))
    assert "TransCore ITS, LLC Saudi Branch" in found["Saudi Arabia"] and "Taiwan" in found
