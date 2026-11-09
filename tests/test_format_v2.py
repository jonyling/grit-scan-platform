"""Brief format v2 (spec 44): the event register, the escalating-items row checks, the look-ahead, and the rule-built tables."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grit.agents.reader import check_event_row, specificity_problems
from grit.pipeline.events import DOWN, SAME, UP, event_register, risk_labels
from grit.pool import Pool
from grit.report.format_v2 import NIL, NOT_SEARCHED, category_of_name, clean_title, source_tier
from grit.report.lookahead import dates_in, look_ahead

SOURCES = {"S1": {"title": "Houthis fire five missiles at Riyadh",
                  "text": "Saudi forces intercepted five ballistic missiles on 27 Oct over Riyadh. The barrage reached the capital's air-defence belt.",
                  "date": "27 Oct 2026"}}
ANCHORS = ["ST Engineering Saudi Limited", "Mobility Rail"]
OWNERS = ["Business area head", "Human Resources"]


def _row(**changes) -> dict:
    row = {
        "item": "Houthi missile barrages on Riyadh",
        "summary": "Saudi forces intercepted five ballistic missiles over Riyadh on 27 Oct 2026 [S1].",
        "sequence": [
            {"date": "27 Oct", "text": "Saudi forces intercepted five ballistic missiles over Riyadh.", "source_id": "S1"},
            {"date": "27 Oct", "text": "The barrage reached the capital's air-defence belt.", "source_id": "S1"},
        ],
        "implications": "Staff of ST Engineering Saudi Limited in Riyadh could face disruption [S1].",
        "response": {"action": "Consider reconfirming the safety of Riyadh staff.", "owner": "Human Resources"},
        "source_ids": ["S1"],
    }
    return {**row, **changes}


def test_an_escalating_row_passes_only_when_specific_grounded_and_conditional() -> None:
    assert check_event_row(_row(), SOURCES, ANCHORS, OWNERS) == []
    vague = check_event_row(_row(summary="Tensions rose in the Gulf [S1]."), SOURCES, ANCHORS, OWNERS)
    assert any(item.startswith("NO_DATE") for item in vague) and any(item.startswith("NO_FIGURE") for item in vague)
    assert any(item.startswith("UNGROUNDED_NUMBER") for item in check_event_row(_row(summary="Saudi forces intercepted 12 missiles on 27 Oct [S1]."), SOURCES, ANCHORS, OWNERS))
    certain = check_event_row(_row(implications="Staff of ST Engineering Saudi Limited will be hurt [S1]."), SOURCES, ANCHORS, OWNERS)
    assert any("NOT_CONDITIONAL" in item for item in certain)
    assert any(item.startswith("UNKNOWN_OWNER") for item in check_event_row(_row(response={"action": "Consider it.", "owner": "CEO"}), SOURCES, ANCHORS, OWNERS))
    assert any(item.startswith("BAD_ITEM") for item in check_event_row(_row(item="One two three four five six seven eight nine ten eleven"), SOURCES, ANCHORS, OWNERS))
    assert specificity_problems("Riyadh was hit on 2026-10-27 by 5 missiles [S1].", SOURCES, ["S1"]) == [], "an ISO date counts"


def test_risk_labels_follow_the_register_numbering() -> None:
    assert risk_labels(["URS-KEY-05", "URS-KEY-03", "URS-TIER_2-07", "TW-GLOBAL-03"], {"TW-GLOBAL-03": "Taiwan Strait pressure"}) == "Key Risks 5, 3; Tier-2 Risk 7; Taiwan Strait pressure"


def _signal(pool: Pool, sid: str, title: str, run: str, *, tier: int, day: str, rung: str | None = "DIRECT", themes: tuple = ("URS-KEY-05",), publisher: str = "reuters.com") -> None:
    signal_id = pool.add_signal(url=f"https://{publisher}/{sid}", title=title, text=title, publisher=publisher, published_at=f"{day}T08:00:00Z", lane="BRAVE", content_hash=sid)
    pool.upsert("assessments", {"signal_id": signal_id, "run_id": run, "pack": "urs", "decision": "KEEP", "tier": tier, "rung": rung, "red": 0, "assessed_at": day,
                                "themes_json": [{"theme_id": theme, "strong": True} for theme in themes], "condition_ids_json": list(themes)}, ["signal_id", "run_id", "pack"])


def test_the_event_register_bands_new_continuing_and_quiet_events(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-10-22-urs", run_date="2026-10-22", pack="urs", kind="READER_SCAN", config={})
    pool.finish_run("2026-10-22-urs")
    pool.start_run("2026-10-29-urs", run_date="2026-10-29", pack="urs", kind="READER_SCAN", config={})
    pool.finish_run("2026-10-29-urs")
    _signal(pool, "old", "Gulf airport closed", "2026-10-22-urs", tier=1, day="2026-10-21")
    _signal(pool, "new", "Missiles hit Riyadh", "2026-10-29-urs", tier=1, day="2026-10-27")
    _signal(pool, "minor", "A trade fair opens", "2026-10-29-urs", tier=3, day="2026-10-28", themes=())
    events = {event["lead"]["title"]: event for event in event_register(pool, "2026-10-29-urs", names={})}
    assert events["Missiles hit Riyadh"]["band"] == UP and events["Missiles hit Riyadh"]["elevated"] and events["Missiles hit Riyadh"]["risk_label"] == "Key Risk 5"
    assert events["A trade fair opens"]["band"] == SAME and not events["A trade fair opens"]["elevated"]
    assert events["Gulf airport closed"]["band"] == DOWN and events["Gulf airport closed"]["members"] == []
    assert list(events)[0] == "Missiles hit Riyadh", "elevated new events come first"


def test_look_ahead_keeps_only_stated_future_dates_inside_the_window() -> None:
    run = date(2026, 10, 29)
    assert dates_in("Since the 1st of November 1998 the rule applied.", run) == [date(1998, 11, 1)]
    rows = [
        {"signal_id": "A", "title": "Midterms", "text": "The US midterm elections are on 3 November. Talks may follow.", "tier": 2},
        {"signal_id": "B", "title": "Rates", "text": "The next FOMC meeting is on Dec 9, 2026, after the window.", "tier": 2},
        {"signal_id": "C", "title": "History", "text": "The plant opened on 3 November 1998 and still runs today.", "tier": 2},
        {"signal_id": "D", "title": "Midterms again", "text": "US voters go to the polls for the midterm elections on 3 November.", "tier": 1},
    ]
    found = look_ahead(rows, run, event_of={"A": "E1", "D": "E1"})
    assert [(item["date"], item["signal_ids"]) for item in found] == [(date(2026, 11, 3), ["A", "D"])], "one row per event and date; past and far dates are left out"


def test_a_wire_dateline_dates_the_report_not_an_event() -> None:
    run = date(2026, 11, 2)
    assert dates_in("Riyadh [Saudi Arabia], November 3 (ANI): The US Embassy issued a security alert.", run) == []
    assert dates_in("WASHINGTON, Nov 3 (Reuters) - The Fed meets on November 20.", run) == [date(2026, 11, 20)]
    assert dates_in("The vote is on 9 November (Monday) - turnout is expected to be high.", run) == [date(2026, 11, 9)], "a weekday is not an agency"
    rows = [{"signal_id": "A", "title": "Embassy alert", "tier": 2,
             "text": "Riyadh [Saudi Arabia], November 3 (ANI): The US Embassy issued a security alert. WASHINGTON, Nov 3 (Reuters) - The Fed meets on November 20."}]
    assert [(item["date"], item["event"]) for item in look_ahead(rows, run)] == [(date(2026, 11, 20), "The Fed meets on November 20.")]
    placed = [{"signal_id": "B", "title": "Staff ordered out", "tier": 3,
               "text": "RIYADH — The moratorium on strikes approaches its November 3 expiry, officials said."}]
    assert look_ahead(placed, run)[0]["event"] == "The moratorium on strikes approaches its November 3 expiry, officials said."


def test_source_tiers_titles_and_categories() -> None:
    assert source_tier("https://www.mas.gov.sg/news", None, None) == "T1"
    assert source_tier("https://www.reuters.com/x", "HIGH", None) == "T2"
    assert source_tier("https://www.example.com/x", "MEDIUM", None) == "T3"
    assert source_tier("https://www.example.com/x", None, None) == "UNRATED", "an unrated outlet is not an aggregator"
    assert source_tier("https://finance.yahoo.com/x", None, None) == "T4"
    assert source_tier("https://example.org/notice", None, None, "OFFICIAL_GOVERNMENT") == "T1"
    assert clean_title("ST Engineering bags $5.6 billion | The Straits Times") == "ST Engineering bags $5.6 billion"
    assert clean_title("Critical Node.js flaw exposes servers < IT·Gaming < 기사본문") == "Critical Node.js flaw exposes servers"
    assert clean_title("World news » Asia » Typhoon Champi nears Taiwan") == "World news » Asia » Typhoon Champi nears Taiwan", "a leading breadcrumb stays"
    thai = "Red Hat เปิดศูนย์ AI CoE ภูมิภาคเอเชียแปซิฟิกในสิงคโปร์ ผนึก EDB และ DSTA"
    assert clean_title(thai) == "Red Hat AI CoE EDB DSTA"
    assert clean_title("เปิดศูนย์ภูมิภาคเอเชีย") == ""
    assert category_of_name("Financial volatility (inflation, interest rate)") == "Financial / macro"
    assert category_of_name("Violation of Trade Compliance") == "Regulatory / legal"
    assert category_of_name("Workplace health and safety") == "Operational"
    assert category_of_name("Uncertainty in key markets due to economic slowdown, geopolitical risks") == "Financial / macro"
    assert category_of_name("Sanctions, export controls and selective economic pressure") == "Regulatory / legal"
    assert NIL != NOT_SEARCHED


def test_the_word_brief_moves_links_to_a_numbered_source_register(tmp_path: Path) -> None:
    from docx import Document

    from grit.report import docx_brief

    document = Document()
    docx_brief._REGISTER = {"numbers": {}, "refs": {}, "tiers": {"SIG-1": "T2"}}
    paragraph = document.add_paragraph()
    docx_brief._runs(paragraph, {"runs": [{"text": "Missiles hit Riyadh"}, {"cite": [{"signal_id": "SIG-1", "outlet": "reuters.com", "date": "27 Oct 2026", "url": "https://reuters.com/a", "title": "Missiles"}]}]})
    docx_brief._source_register(document, {}, "9. Source register")
    docx_brief._REGISTER = None
    text = "\n".join(item.text for item in document.paragraphs)
    assert "Missiles hit Riyadh (reuters.com, 27 Oct 2026 [S1])" in text
    assert "[S1] reuters.com, Missiles, 27 Oct 2026 (T2)" in text
    assert document.paragraphs[0]._p.find(".//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}hyperlink") is None, "no link in the body"


def test_a_signpost_match_needs_a_candidate_a_verbatim_quote_and_a_shared_specific_word() -> None:
    from grit.agents.reader import check_signpost_matches

    items = {"WORST-S1": {"text": "Monitoring bodies publish reports documenting trillions in cumulative project cost overruns", "candidates": ["S1", "S2"]}}
    sources = {"S1": {"title": "Infrastructure projects show cost overrun of Rs 4.4 trillion", "text": "The ministry found 400 projects with a cost overrun of Rs 4.4 trillion."},
               "S2": {"title": "Airport urges unleaded fuel", "text": "The airport urged a national shift to unleaded aviation fuel this week."},
               "S3": {"title": "Other", "text": "Projects with a cost overrun of Rs 4.4 trillion were listed."}}
    draft = {"matches": [
        {"item": "WORST-S1", "source_id": "S1", "quote": "projects with a cost overrun of Rs 4.4 trillion"},
        {"item": "WORST-S1", "source_id": "S2", "quote": "urged a national shift to unleaded aviation fuel"},
        {"item": "WORST-S1", "source_id": "S3", "quote": "Projects with a cost overrun of Rs 4.4 trillion"},
        {"item": "WORST-S1", "source_id": "S1", "quote": "a cost overrun of Rs 9 trillion was found"},
    ]}
    kept, problems = check_signpost_matches(draft, items, sources)
    assert kept == [{"item": "WORST-S1", "source_id": "S1", "quote": "projects with a cost overrun of Rs 4.4 trillion"}]
    assert [problem.split(":")[0] for problem in problems] == ["OFF_TOPIC", "NOT_A_CANDIDATE", "QUOTE_NOT_VERBATIM"]


def test_the_tracker_counts_a_judged_signpost_as_seen(tmp_path: Path) -> None:
    from grit.pipeline.scenarios import track

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-11-02-urs", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"signposts_seen": {"URS-KEY-03": {"WORST-S1": ["SIG-x"]}}})
    pool.finish_run("2026-11-02-urs")
    register = {"shifts": [{"themeId": "URS-KEY-03", "name": "Costs", "cases": [
        {"case": case, "name": case, "signposts": [{"text": f"{case} signpost", "keywords": ["no such phrase anywhere"]}], "counter_indicators": [], "responses": []}
        for case in ("BEST", "MEDIUM", "WORST")]}]}
    (reading,) = track(pool, "2026-11-02-urs", register)
    assert reading["lean"] == "WORST" and reading["cases"][2]["signposts_seen"][0]["signal_ids"] == ["SIG-x"]


def test_a_signal_the_reviewed_selection_excluded_is_not_a_signpost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from grit.pipeline.scenarios import track

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-11-02-urs", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "signposts_seen": {"URS-KEY-03": {"BEST-S1": ["SIG-x", "SIG-y"]}}})
    pool.finish_run("2026-11-02-urs")
    monkeypatch.setattr(pool, "weekly_assessments", lambda run_id, include_excluded=False: [
        {"signal_id": "SIG-x", "editorial": {"decision": "EXCLUDE"}}, {"signal_id": "SIG-y", "editorial": {"decision": "INCLUDE"}}])
    register = {"shifts": [{"themeId": "URS-KEY-03", "name": "Costs", "cases": [
        {"case": case, "name": case, "signposts": [{"text": f"{case} signpost", "keywords": ["no such phrase anywhere"]}], "counter_indicators": [], "responses": []}
        for case in ("BEST", "MEDIUM", "WORST")]}]}
    (reading,) = track(pool, "2026-11-02-urs", register)
    assert reading["cases"][0]["signposts_seen"][0]["signal_ids"] == ["SIG-y"]


def test_a_public_body_counts_only_in_its_own_country_and_the_business_areas_own_business() -> None:
    from types import SimpleNamespace

    from grit.ontology.link import Involvement, Link
    from grit.pipeline.reader_scan import public_body_rule

    def involvement(node_id: str, name: str) -> Involvement:
        return Involvement(node_id, "Customer", name, name, "REGISTER", "LEAD", 1, True, False)

    graph = SimpleNamespace(nodes={"BCA": SimpleNamespace(attrs={"country": "Singapore"}), "MOE": SimpleNamespace(attrs={"country": "Singapore"}),
                                   "PTC": SimpleNamespace(attrs={"country": "USA"})})

    def link(node_id: str, name: str) -> Link:
        return Link("COUNTERPARTY", (involvement(node_id, name),), None, None, (), (), (), ())

    terms = ("cybersecurity", "software", "contract", "tolling", "travel information")
    malta = public_body_rule(link("BCA", "Building and Construction Authority"), "Man crushed to death at Hal Far, Malta. The Building and Construction Authority stopped works.", graph, terms)
    assert malta.rung is None and not malta.involves[0].strong and "Malta" in malta.reasons[-2]
    food = public_body_rule(link("MOE", "Ministry of Education"), "Gastroenteritis outbreak at two Ministry of Education kindergartens in Singapore.", graph, terms)
    assert food.rung is None and "outside the business area" in food.reasons[-2]
    tender = public_body_rule(link("MOE", "Ministry of Education"), "Singapore's Ministry of Education awards a software contract for schools.", graph, terms)
    assert tender.rung == "COUNTERPARTY" and tender.involves[0].strong
    turnpike = public_body_rule(link("PTC", "Pennsylvania Turnpike Commission"), "The Pennsylvania Turnpike Commission upgraded its travel information service.", graph, terms)
    assert turnpike.rung == "COUNTERPARTY", "a US state body is not rejected for not writing 'United States'"


def test_countries_are_named_by_common_name_and_alias() -> None:
    from grit.core.countries import canonical, named

    assert named("Blasts in Riyadh; Saudi forces intercept missiles") == {"Saudi Arabia"}
    assert named("Tell us about it") == set() and named("US carriers head to the Gulf") == {"United States"}
    assert canonical("USA") == "United States" and canonical("Republic of Korea") == "South Korea"


def test_coverage_counts_answered_searches_and_scopes_them_to_the_line_they_aim_at(tmp_path: Path) -> None:
    from grit.report.format_v2 import COMPETITOR, FINANCIAL, SUPPLY, _names_country, _searched

    pool = Pool(tmp_path / "pool.db")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={})
    pool.record_search("R", query_id="q1", engine="BRAVE", query="rates outlook", lane="CONDITION", target_id="T-FIN", canonical_lob=None,
                       results=0, fetched_at="2026-11-02T00:00:00Z")
    pool.record_search("R", query_id="q2", engine="BRAVE", query="Rail rival wins", lane="COMPETITOR", target_id="C1", canonical_lob="Rail",
                       results=3, fetched_at="2026-11-02T00:00:00Z")
    pool.record_search("R", query_id="q3", engine="BRAVE", query="Acme supply", lane="SUPPLIER", target_id="S1", canonical_lob=None,
                       results=1, fetched_at="2026-11-02T00:00:00Z")
    scope = _searched(pool, "R", {"T-FIN": "Financial volatility"}, {"S1": "Aviation"})
    assert scope["counts"] == {FINANCIAL: 1, COMPETITOR: 1, SUPPLY: 1}, "a search answered with nothing still counts"
    assert scope["scoped"] == {("Rail", COMPETITOR), ("Aviation", SUPPLY)}, "the wide financial search marks no line as specifically searched"
    assert _names_country("United Arab Emirates", "Port strike in the UAE") and not _names_country("United States", "Rail rival wins in Busan")
    pool.close()


def test_a_management_request_search_takes_its_category_from_the_request_title(tmp_path: Path) -> None:
    from grit.report.format_v2 import FINANCIAL, OPERATIONAL, _searched

    pool = Pool(tmp_path / "pool.db")
    pool.start_run("R", run_date="2026-11-06", pack="marine", kind="READER_SCAN", config={})
    for request_id, title in (("MR-FIN", "Financial and macro exposure: shipowner credit, currency and interest rates"), ("MR-OPS", "Yard utilisation and repair throughput")):
        pool.insert("management_requests", {
            "request_id": request_id, "description": None, "requested_by": "test", "request_date": "2026-11-06", "status": "ACTIVE", "origin": "WORKBOOK",
            "profile_id": None, "title": title, "kind": "TOPIC", "keywords_json": "[]", "queries_json": "[]", "country": None, "lob": None, "pack": "marine", "source": None,
        })
    for query_id, target in (("q1", "MR-FIN"), ("q2", "MR-OPS")):
        pool.record_search("R", query_id=query_id, engine="BRAVE", query=f"query {query_id}", lane="REQUEST", target_id=target, canonical_lob=None,
                           results=2, fetched_at="2026-11-06T00:00:00Z")
    assert _searched(pool, "R", {})["counts"] == {FINANCIAL: 1, OPERATIONAL: 1}
    pool.close()


def test_the_scenario_window_includes_the_run_being_reported_before_it_finishes(tmp_path: Path) -> None:
    from grit.pipeline.scenarios import window_rows

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-11-02-urs", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"weekly_mode": "reasoned"})
    signal = pool.add_signal(url="https://news.example/costs", title="Costs rise", text="Project costs rise.", publisher="News",
                             published_at="2026-11-01", lane="THEME", content_hash="costs")
    pool.add_sighting(signal, "2026-11-02-urs", query_id="q", query="costs", lane="THEME", target_id=None, rank=1)
    pool.save_assessment({"signal_id": signal, "run_id": "2026-11-02-urs", "pack": "urs", "decision": "KEEP"})
    assert [row["signal_id"] for row in window_rows(pool, "2026-11-02-urs")] == [signal]
