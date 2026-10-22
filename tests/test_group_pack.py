"""Spec 37: the STE Group pack. No network, no model."""

from __future__ import annotations

from pathlib import Path

from grit.acquisition.brave_plan import BraveResponse
from grit.pipeline import reader_scan
from tests.test_reader_scan import _scan

CURRENTS_PAYLOAD = {
    "status": "ok",
    "news": [{"title": "Houthi missiles target Riyadh as Middle East security worsens", "url": "https://www.reuters.com/world/middle-east/houthi-riyadh-2026-10-10",
              "description": "Houthi forces fired missiles at Riyadh, Saudi Arabia.", "published": "2026-10-10 06:00:00 +0000"}],
}


def _silent_brave(url: str, params: dict[str, str]) -> BraveResponse:
    return BraveResponse(200, {"results": []}, {"x-ratelimit-limit": "50"})


def test_currents_answers_what_brave_left_empty_and_the_answer_is_not_repeated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool, run, specs = _scan(tmp_path)
    calls: list[str] = []

    def currents(url: str, params: dict[str, str]) -> dict:
        calls.append(params["keywords"])
        return CURRENTS_PAYLOAD

    found = reader_scan.acquire(pool, run, specs, getter=_silent_brave, currents_getter=currents, usage_path=tmp_path / "usage.json",
                                sleep=lambda _s: None, log=lambda _m: None, fallback_max=3)
    assert found["currents_fallback"] == {"sent": 3, "skipped": len(specs) - 3, "reason": "FALLBACK_LIMIT"}
    assert calls == [spec.query for spec in specs[:3]]
    signal = pool.one("SELECT * FROM signals WHERE url LIKE '%houthi-riyadh%'")
    assert signal and signal["lane"] == "CURRENTS"
    sightings = pool.rows("SELECT * FROM sightings WHERE signal_id = ?", (signal["signal_id"],))
    assert {row["query_id"] for row in sightings} == {spec.query_id for spec in specs[:3]}, "each Currents answer is a sighting of its own query"

    calls.clear()
    again = reader_scan.acquire(pool, run, specs[:3], getter=_silent_brave, currents_getter=currents, usage_path=tmp_path / "usage.json",
                                sleep=lambda _s: None, log=lambda _m: None, fallback_max=3)
    assert again["queries_sent"] == 0 and calls == [], "a query Currents answered is answered for the run"


def test_an_offline_brave_never_calls_the_live_currents(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    monkeypatch.setenv("CURRENTS_API_KEY", "a-key-that-must-not-be-used")
    pool, run, specs = _scan(tmp_path)
    found = reader_scan.acquire(pool, run, specs[:2], getter=_silent_brave, usage_path=tmp_path / "usage.json", sleep=lambda _s: None, log=lambda _m: None)
    assert "currents_fallback" not in found


def test_a_brave_error_hands_the_rest_to_currents(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool, run, specs = _scan(tmp_path)

    def broken(url: str, params: dict[str, str]) -> BraveResponse:
        return BraveResponse(500, None, {})

    found = reader_scan.acquire(pool, run, specs[:2], getter=broken, currents_getter=lambda url, params: CURRENTS_PAYLOAD,
                                usage_path=tmp_path / "usage.json", sleep=lambda _s: None, log=lambda _m: None)
    assert found["stopped"] == "BRAVE_ERROR" and found["currents_fallback"]["sent"] == 2


# The Group pack end to end ---------------------------------------------------------------------------------------

import json
from datetime import date

from grit.agents.reader_prompts import (
    ASSESSOR_SYSTEM,
    BASELINER_SYSTEM,
    CRITIC_SYSTEM,
    PROPOSER_SYSTEM,
    REPORTER_BOTTOM_LINE_SYSTEM,
    KEYWORDER_SYSTEM,
    REPORTER_ITEMS_SYSTEM,
    STRATEGIST_SYSTEM,
)
from grit.ontology import group_seed
from grit.pool import Pool
from grit.settings import CONFIG_DIR
from tests.test_reader_scan import _Embedder

GROUP_DATE = date(2026, 10, 31)
REASONING = "STRATEGIST-WORKING-NOT-FOR-THE-DRAFT."


def _group_config(tmp_path: Path, monkeypatch) -> Path:
    """A config folder with the real URS packs and risk registers linked in, not copied, and the Group pack written fresh."""
    from grit.report import brief

    config = tmp_path / "config"
    config.mkdir()
    for name in ("urs", "urs-tw", "risk", "registry_aliases.json", "red-patterns.json"):
        (config / name).symlink_to(CONFIG_DIR / name)
    result = group_seed.write_pack(config, children=["urs"])
    assert result["children"] == ["urs"] and not result["kept"]
    monkeypatch.setattr(reader_scan, "CONFIG_DIR", config)
    monkeypatch.setattr(brief, "CONFIG_DIR", config)
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path / "output")
    return config


def _articles(country: str) -> dict[str, list[dict]]:
    return {
        "Middle East security escalation Gulf": [
            {"title": f"Missile attack hits {country} as Gulf security worsens", "url": "https://www.reuters.com/world/missile-attack-2026-10-20",
             "description": f"A missile attack struck an industrial area in {country}, officials said. Air defence systems intercepted most drones.", "page_age": "2026-10-20T03:00:00"},
        ],
        "DRAM contract prices": [
            {"title": "DRAM price rises again as HBM demand squeezes memory supply", "url": "https://www.trendforce.com/news/2026/10/15/dram",
             "description": "DRAM contract prices rose for a third quarter as memory makers shifted capacity to HBM.", "page_age": "2026-10-15T03:00:00"},
        ],
    }


def _case(case: str, anchor: str, owner: str, number: int) -> dict:
    return {
        "case": case, "name": f"{case.title()} path", "narrative": f"Memory supply could move along the {case.lower()} path for STE.",
        "evidence": ["DRAM contract prices rose for a third quarter. [S1]", "According to PwC, HBM demand is projected to keep growing. [P1]"],
        "signposts": [f"{case.title()} signpost {item}" for item in ("one", "two", "three")], "counter_indicators": [f"{case.title()} counter-indicator"],
        "impact": [f"Memory costs could shift project margins for STE in {anchor}."],
        "responses": [{"action": "Consider reviewing memory supply terms.", "owner": owner, "trigger": 1 + number % 3}],
    }


def _sots(folder: Path) -> Path:
    """A State of the Situation fixture for the semiconductor shift, so the test never reads output/."""
    day = folder / "sots" / "2026-10-15"
    day.mkdir(parents=True)
    document = {
        "topicId": "SOTS-SEMIS", "name": "Semiconductor and GPU access", "status": "DRAFT_FOR_SIGN_OFF",
        "sections": [{"key": "UNCERTAINTIES", "title": "Key uncertainties", "status": "DRAFTED",
                      "draft": {"items": [{"uncertainty": "Whether HBM capacity catches up with AI demand", "why_it_matters": "Prices follow it. [P1]"}]}}],
        "passages": {"P1": {"report_id": "PWC-SEMI-2026", "report": "PwC Semiconductor and Beyond", "publisher": "PwC", "aliases": ["PwC"], "edition": "Nov 2025",
                            "page": 64, "page_label": "p.", "text": "HBM demand is projected to keep growing through 2030."}},
    }
    (day / "SOTS-SEMIS.json").write_text(json.dumps(document), encoding="utf-8")
    return folder / "sots"


def _group_model(system: str, payload: dict) -> dict:
    if system == ASSESSOR_SYSTEM:
        sources = payload["sources"]
        if payload["item"]["id"] == "STE-GC-02" and sources:
            area = payload["business_area"]
            anchor = area["countries"][0] if area.get("countries") else area["business_areas"][0]
            return {"movement": "ESCALATING", "summary": "A missile attack struck an industrial area. [S1]", "source_ids": ["S1"],
                    "quotes": [{"source_id": "S1", "quote": "A missile attack struck an industrial area"}], "change_from_prior": "First assessment.",
                    "implication": f"Attacks of this kind could disrupt STE staff and sites in {anchor}. [S1]", "opportunity": None,
                    "counter_indicator": "A durable ceasefire between the parties would show the risk is easing."}
        return {"movement": "NO_MATERIAL_CHANGE", "summary": "No new development was found this week in the checked sources.", "source_ids": [], "quotes": []}
    if system == CRITIC_SYSTEM:
        return {"issues": []}
    if system == PROPOSER_SYSTEM:
        owners = payload["owner_roles"]
        return {"reasoning": "The attack tests site security.", "options": [
            {"kind": "ADDRESS_GAP", "option": "Consider reviewing site security plans for Gulf sites.", "owner": "Group Risk & Assurance", "builds_on": "", "trigger": "", "source_ids": ["S1"]},
            {"kind": "MONITOR", "option": "Consider continuing to monitor Gulf security.", "owner": owners[-1], "builds_on": "", "trigger": "Attacks reach a city where STE staff work.", "source_ids": []},
        ]}
    if system == BASELINER_SYSTEM:
        if payload["sources"]:
            indicator = payload["condition"]["watchIndicators"][0]
            return {"position": "A missile attack struck an industrial area. [S1]", "indicators": [{"indicator": indicator, "reading": "A missile attack struck an industrial area. [S1]"}],
                    "gaps": "The other indicators are not covered.", "source_ids": ["S1"], "quotes": [{"source_id": "S1", "quote": "A missile attack struck an industrial area"}]}
        return {"position": "The sources in this run do not establish the current position.", "indicators": [], "gaps": "None.", "source_ids": [], "quotes": []}
    if system == STRATEGIST_SYSTEM:
        area, owners = payload["business_area"], payload["owners"]
        anchor = area["countries"][0] if area.get("countries") else area["business_areas"][0]
        if "S1" not in payload["sources"]:
            return {"reasoning": REASONING, "builds_on": [], "cases": []}
        return {"reasoning": REASONING, "builds_on": payload["uncertainties"][:1], "cases": [_case(name, anchor, owners[-1], number) for number, name in enumerate(("BEST", "MEDIUM", "WORST"))]}
    if system == KEYWORDER_SYSTEM:
        return {"keywords": {item["id"]: [" ".join(item["text"].split()[:2]), " ".join(item["text"].split()[-2:])] for item in payload["items"]}}
    if system == REPORTER_ITEMS_SYSTEM:
        return {"items": []}
    if system == REPORTER_BOTTOM_LINE_SYSTEM:
        return {"bottom_line": f"Bottom line for {payload['facts']['brief']}.", "escalation": "Nothing warrants escalation."}
    raise AssertionError(system[:40])


def test_the_group_pack_runs_end_to_end(tmp_path: Path, monkeypatch) -> None:
    from docx import Document
    from pptx import Presentation

    from grit.ontology.link import FOOTPRINT
    from grit.pipeline.group import draft_baseline, propose_scenarios
    from grit.profiles import load_profiles
    from grit.report.package import write_pack

    config = _group_config(tmp_path, monkeypatch)
    pack = reader_scan._load_pack("ste")
    assert pack.level == "GROUP" and pack.children == ["urs"]
    graph = reader_scan.pack_graph(pack)
    countries = [node.name for node in graph.of_class("Place") if graph.entities_in_place(node.id)]
    assert countries, "the footprint is every country where a registered entity sits"
    assert {node.id for node in graph.of_class("Theme")} == {f"STE-SHIFT-{number:02d}" for number in range(1, 9)}, "the Group themes replace the BA themes"
    articles = _articles(countries[0])

    pool = Pool(tmp_path / "grit.sqlite")
    run = reader_scan.new_run(pool, GROUP_DATE, "ste")
    specs = reader_scan.plan(pool, run)
    assert len(specs) == 48, "four queries for each of the 12 conditions"

    def brave(url: str, params: dict[str, str]) -> BraveResponse:
        found = articles.get(params["q"].strip('"'), [])
        return BraveResponse(200, {"results": [dict(item, meta_url={"hostname": item["url"].split("/")[2]}) for item in found]}, {"x-ratelimit-limit": "50"})

    reader_scan.acquire(pool, run, specs, getter=brave, usage_path=tmp_path / "usage.json", sleep=lambda _s: None, log=lambda _m: None)
    reader_scan.embed(pool, run, embedder=_Embedder())
    reader_scan.assess(pool, run, score_harm_fn=lambda *a, **k: {"status": "NOT_RUN", "reason": "test"})
    rows = {row["title"]: row for row in pool.assessments(run.run_id, kept_only=True)}
    attack = rows[articles["Middle East security escalation Gulf"][0]["title"]]
    assert attack["rung"] == FOOTPRINT and "STE-GC-02" in attack["condition_ids"], "a footprint country and a strong condition hit link a Group signal"

    result = reader_scan.run_agents(pool, run, client=reader_scan.stub_client(_group_model), model="stub", log=lambda _m: None)
    pool.finish_run(run.run_id)
    assert result["options"] == {"STE-GC-02": 2}
    condition = {row["condition_id"]: row for row in pool.conditions(run.run_id)}["STE-GC-02"]
    assert condition["trend_label"] == "Escalating" and "[SIG-" in condition["implication"] and condition["opportunity"] is None
    assert condition["counter_indicator"].startswith("A durable ceasefire")

    baseline = draft_baseline(pool, run, client=reader_scan.stub_client(_group_model), model="stub", log=lambda _m: None)
    assert (baseline["DRAFTED"], baseline["WITHHELD"], baseline["NO_EVIDENCE"]) == (1, 1, 10), "a quote not in its source withholds the draft"
    drafted = json.loads(Path(baseline["files"]["json"]).read_text(encoding="utf-8"))
    entry = next(item for item in drafted["conditions"] if item["id"] == "STE-GC-02")
    withheld = next(item for item in drafted["conditions"] if item["id"] == "STE-GC-06")
    assert withheld["currentAssessment"] is None and "QUOTE_NOT_VERBATIM" in withheld["violations"][0]
    assert entry["currentAssessment"].startswith("A missile attack struck an industrial area") and "SIG-" not in entry["currentAssessment"]
    assert entry["sources"][0]["url"].startswith("https://www.reuters.com/") and Path(baseline["files"]["docx"]).is_file()

    scenarios = propose_scenarios(pool, run, client=reader_scan.stub_client(_group_model), model="stub", log=lambda _m: None, sots_dir=_sots(tmp_path))
    proposed = json.loads(Path(scenarios["files"]["json"]).read_text(encoding="utf-8"))
    memory = next(item for item in proposed["shifts"] if item["name"] == "Semiconductor/GPU access")
    assert memory["status"] == "PROPOSED" and [item["case"] for item in memory["cases"]] == ["BEST", "MEDIUM", "WORST"]
    assert memory["builds_on"] == ["Whether HBM capacity catches up with AI demand"] and memory["situation"] == "Semiconductor and GPU access"
    best = memory["cases"][0]
    assert best["title"] == "Best case for STE" and best["signals"][0]["url"].startswith("https://www.trendforce.com/")
    assert "(trendforce.com," in best["evidence"][0] and "PwC" in best["evidence"][1] and "[P1]" not in best["evidence"][1], "marks become readable citations"
    assert scenarios["proposed"] == 1, "only the shift with a State of the Situation or signals gets cases"
    assert REASONING not in json.dumps(proposed), "the Strategist's reasoning stays in the execution log"
    assert any(REASONING in str((row.get("detail") or {}).get("reasoning")) for row in pool.execution_log(run.run_id) if row["actor"] == "Strategist")
    assert Path(scenarios["files"]["docx"]).is_file()
    asked: list[str] = []

    def counting(system: str, payload: dict) -> dict:
        if system == STRATEGIST_SYSTEM:
            asked.append(payload["shift"]["themeId"])
        return _group_model(system, payload)

    again = propose_scenarios(pool, run, client=reader_scan.stub_client(counting), model="stub", log=lambda _m: None, sots_dir=_sots(tmp_path / "again"), redraft=True)
    assert asked == [], "a proposed shift is kept; the others have no evidence in this fixture, so no model call is made"
    kept = json.loads(Path(again["files"]["json"]).read_text(encoding="utf-8"))
    assert next(item for item in kept["shifts"] if item["themeId"] == memory["themeId"]) == memory, "--redraft keeps a proposed shift unchanged"

    from grit.pipeline.scenarios import approve, load_register, outlook

    approved = approve(pool, "ste", GROUP_DATE, reviewed_by="Wilson Yeo", client=reader_scan.stub_client(_group_model), model="stub", config_dir=config, log=lambda _m: None)
    assert approved["version"] == 1 and approved["shifts"] == 1 and approved["keywords_withheld"] == []
    register = load_register("ste", config)
    case = register["shifts"][0]["cases"][2]
    assert register["reviewedBy"] == "Wilson Yeo" and case["case"] == "WORST" and case["signposts"][0] == {"text": "Worst signpost one", "keywords": ["Worst signpost", "signpost one"]}
    assert outlook(pool, run.run_id, reader_scan._load_pack("ste"))["shifts"][0]["lean"] == "NONE", "no signal names a signpost yet"
    signal = pool.add_signal(url="https://example.com/worst", title="Worst signpost three is reported", text="", publisher="example.com", published_at="2026-10-30T00:00:00Z", lane="BRAVE", content_hash="w")
    pool.save_assessment({"signal_id": signal, "run_id": run.run_id, "pack": "ste", "decision": "KEEP", "tier": 3, "themes_json": [{"theme_id": memory["themeId"], "strong": True}]})
    reading = outlook(pool, run.run_id, reader_scan._load_pack("ste"))["shifts"][0]
    worst = reading["cases"][2]
    assert reading["lean"] == "WORST" and [item["number"] for item in worst["signposts_seen"]] == [3], "two keywords must match, so only signpost 3 is seen"
    assert worst["signposts_seen"][0]["signal_ids"] == [signal] and worst["signposts_seen"][0]["sources"][0]["outlet"] == "example.com"
    assert [item["trigger"] for item in worst["responses_triggered"]] == [3], "the worst case's response triggered by signpost 3 fires"
    off_shift = pool.add_signal(url="https://example.com/other", title="Best signpost one elsewhere", text="", publisher="example.com", published_at="2026-10-30T00:00:00Z", lane="BRAVE", content_hash="o")
    pool.save_assessment({"signal_id": off_shift, "run_id": run.run_id, "pack": "ste", "decision": "KEEP", "tier": 3, "themes_json": []})
    assert outlook(pool, run.run_id, reader_scan._load_pack("ste"))["shifts"][0]["lean"] == "WORST", "a signal that does not touch the shift is not counted"

    from grit.copilot.scope import scope_for
    from grit.copilot.tools import Toolbox

    labels = {"ste": {"label": "ST Engineering Group", "level": "GROUP"}, "urs": {"label": "Urban Solutions", "level": "BA"}}
    tools = Toolbox(pool, scope_for(load_profiles(config / "profiles")["vschong"], labels=labels), config_dir=config)
    answer = tools.call("get_scenarios", {"shift": "semiconductor"})
    assert answer["shifts"][0]["lean"].startswith("Leaning towards the worst case (1 of 3") and answer["shifts"][0]["cases"][2]["signposts_seen"][0]["signals"] == ["S1"]

    refreshed = propose_scenarios(pool, run, client=reader_scan.stub_client(_group_model), model="stub", log=lambda _m: None, sots_dir=_sots(tmp_path / "refresh"))
    shift = next(item for item in json.loads(Path(refreshed["files"]["json"]).read_text(encoding="utf-8"))["shifts"] if item["themeId"] == memory["themeId"])
    assert shift["changes"] == ["No change from the reviewed version."]

    ceo = load_profiles(config / "profiles")["vschong"]
    files = write_pack(pool, run.run_id, ceo, folder=tmp_path / "ceo", submit=False)["files"]
    document = Document(files["docx"])
    radar = next([[cell.text for cell in row.cells] for row in table.rows] for table in document.tables if table.rows[0].cells[0].text == "Theme and what changed")
    assert radar[0] == ["Theme and what changed", "Sources, date and time", "Risk and opportunity for STE", "Business areas", "Counter-indicator", "Proposed response", "Priority and trend"]
    assert len(radar) == 13, "one row for each of the 12 conditions"
    row = next(item for item in radar[1:] if item[0].startswith("2. "))
    assert "Urban Solutions" in row[3] and row[4].startswith("A durable ceasefire") and "Consider reviewing site security plans" in row[5]
    outlook_table = next([[cell.text for cell in row.cells] for row in table.rows] for table in document.tables if table.rows[0].cells[0].text == "Strategic shift")
    assert len(outlook_table) == 2 and outlook_table[1][1].startswith("Leaning towards the worst case") and "example.com" in outlook_table[1][2]
    deck = Presentation(files["pptx"])
    assert len(deck.slides) == 4, "a cover, the summary, the scenario outlook, and one appendix slide for the one condition with evidence"
    assert any("SCENARIO OUTLOOK" in shape.text_frame.text for shape in deck.slides[2].shapes if shape.has_text_frame)
    from openpyxl import load_workbook

    from grit.report.brief import build_brief
    from grit.report.markdown import render_markdown
    from grit.report.office import render_xlsx

    model = build_brief(pool, run.run_id, ceo)
    assert model["meta"]["sections"].index("SCENARIOS") == model["meta"]["sections"].index("CONDITIONS") + 1 and signal in model["membership"]
    assert "Scenario outlook" in load_workbook(render_xlsx(model, [], tmp_path / "radar.xlsx")).sheetnames
    text = render_markdown(model)
    assert "## 1. Scenario outlook: best, medium and worst case for STE" in text and "| **Semiconductor/GPU access**<br>Best case: Best path<br>" in text


def test_a_country_alone_or_outside_a_group_pack_is_not_on_the_footprint_rung(tmp_path: Path, monkeypatch) -> None:
    import dataclasses

    from grit.ontology.link import FOOTPRINT, PERIMETER
    from grit.ontology.reader_index import CONDITION, ReaderHit

    _group_config(tmp_path, monkeypatch)
    graph = reader_scan.pack_graph(reader_scan._load_pack("ste"))
    place = next(node.id for node in graph.of_class("Place") if graph.entities_in_place(node.id))
    hit = ReaderHit(CONDITION, "STE-GC-02", "Middle East security developments", "missile attack", "TITLE", 1, True)
    weak = ReaderHit(CONDITION, "STE-GC-02", "Middle East security developments", "missile attack", "BODY", 1, False)

    @dataclasses.dataclass(frozen=True)
    class Link:
        rung: str | None
        place_ids: tuple
        reasons: tuple

    assert reader_scan.footprint_rule(Link(None, (place,), ()), [hit], graph).rung == FOOTPRINT
    assert reader_scan.footprint_rule(Link(None, (place,), ()), [], graph).rung is None, "a country alone is not a link"
    assert reader_scan.footprint_rule(Link(None, (place,), ()), [weak], graph).rung is None, "a passing condition mention is not enough"
    assert reader_scan.footprint_rule(Link(None, (), ()), [hit], graph).rung is None, "a condition alone is not a footprint link"
    assert reader_scan.footprint_rule(Link(PERIMETER, (place,), ()), [hit], graph).rung == PERIMETER, "a stronger link stays"


def test_group_extras_are_checked_one_by_one() -> None:
    from grit.agents.reader import check_extras

    sources = {"S1": {"title": "Missile attack hits Riyadh", "text": "A missile attack struck an industrial area of Riyadh in Saudi Arabia."}}
    anchors = ["Saudi Arabia", "Urban Solutions"]
    good = {"implication": "Attacks could disrupt STE sites in Saudi Arabia. [S1]", "opportunity": "Demand for air defence could open work for Urban Solutions. [S1]",
            "counter_indicator": "A durable ceasefire would show the risk is easing."}
    accepted, failing = check_extras(good, ["S1"], sources, anchors, True)
    assert failing == {} and set(accepted) == {"implication", "opportunity", "counter_indicator"}
    accepted, failing = check_extras({**good, "opportunity": None}, ["S1"], sources, anchors, True)
    assert accepted["opportunity"] is None and not failing, "an opportunity is optional"
    accepted, failing = check_extras({**good, "opportunity": "It will open work somewhere. [S1]", "counter_indicator": ""}, ["S1"], sources, anchors, True)
    assert set(failing) == {"opportunity", "counter_indicator"} and accepted["implication"], "a failing field is dropped without the others"
    accepted, failing = check_extras({**good, "counter_indicator": "A durable ceasefire would show the risk is easing. [S1]"}, ["S1"], sources, anchors, True)
    assert accepted["counter_indicator"] == "A durable ceasefire would show the risk is easing.", "a local [S#] mark never reaches the brief"
    accepted, failing = check_extras({**good, "counter_indicator": "A fall of 40 percent in attacks."}, ["S1"], sources, anchors, True)
    assert "UNGROUNDED_NUMBER" in " ".join(failing["counter_indicator"])
    accepted, failing = check_extras(good, ["S1"], sources, anchors, False)
    assert set(accepted) == {"implication"}, "a business-area key risk has no opportunity or counter-indicator"


def test_each_baseline_and_scenario_check_names_its_violation() -> None:
    from grit.agents.reader import check_baseline, check_scenarios

    sources = {"S1": {"title": "DRAM price rises", "text": "DRAM contract prices rose 8 percent this quarter as makers shifted capacity to HBM."}}
    good = {"position": "DRAM contract prices rose 8 percent this quarter. [S1]", "indicators": [{"indicator": "DRAM and NAND contract prices", "reading": "Up 8 percent. [S1]"}],
            "gaps": "None.", "source_ids": ["S1"], "quotes": [{"source_id": "S1", "quote": "DRAM contract prices rose 8 percent"}]}
    indicators = ["DRAM and NAND contract prices"]
    assert check_baseline(good, sources, indicators) == []
    assert "UNKNOWN_INDICATOR" in " ".join(check_baseline({**good, "indicators": [{"indicator": "Something else", "reading": "x [S1]"}]}, sources, indicators))
    assert "UNGROUNDED_NUMBER" in " ".join(check_baseline({**good, "position": "Prices rose 12 percent. [S1]"}, sources, indicators))
    assert "NO_EVIDENCE" in " ".join(check_baseline({**good, "quotes": []}, sources, indicators))

    anchors, owners = ["Digital Systems & Cyber"], ["Group Risk & Assurance"]
    uncertainties = ["Whether HBM capacity catches up"]
    sources = {**sources, "P1": {"title": "PwC report, p. 64", "publisher": "PwC", "aliases": ["PwC"], "text": "HBM demand is projected to double by 2028."}}

    def case(name: str, number: int) -> dict:
        return {"case": name, "name": f"{name.title()} path", "narrative": "HBM could stay scarce.",
                "evidence": ["DRAM contract prices rose 8 percent this quarter. [S1]", "According to PwC, HBM demand is projected to double by 2028. [P1]"],
                "signposts": [f"{name} signpost {item}" for item in ("one", "two", "three")], "counter_indicators": ["Prices fall"],
                "impact": ["Costs could rise for Digital Systems & Cyber."], "responses": [{"action": "Consider reviewing supply terms.", "owner": "Group Risk & Assurance", "trigger": 1}]}

    cases = [case(name, number) for number, name in enumerate(("BEST", "MEDIUM", "WORST"))]
    draft = {"reasoning": "Prices rose.", "builds_on": uncertainties, "cases": cases}
    assert check_scenarios(draft, sources, anchors, owners, uncertainties) == []

    def problems(**changes) -> str:
        return " | ".join(check_scenarios({**draft, "cases": [{**cases[0], **changes}, *cases[1:]]}, sources, anchors, owners, uncertainties))

    assert "NOT_CONDITIONAL" in problems(narrative="HBM stays scarce.")
    assert "CERTAINTY_CLAIM" in problems(narrative="HBM could and will stay scarce.")
    assert "UNATTRIBUTED_FORECAST" in problems(evidence=["HBM demand is projected to double by 2028. [P1]"])
    assert "UNCITED" in problems(evidence=["DRAM contract prices rose."])
    assert "UNKNOWN_SOURCE" in problems(evidence=["DRAM prices rose. [S9]"])
    assert "UNGROUNDED_NUMBER" in problems(evidence=["DRAM contract prices rose 30 percent. [S1]"])
    assert "UNGROUNDED_NUMBER" in problems(narrative="HBM could stay 30 percent dearer.")
    assert "BAD_COUNT" in problems(signposts=["Only one"])
    assert "SHARED_SIGNPOST" in problems(signposts=cases[1]["signposts"])
    assert "NO_ANCHOR" in problems(impact=["Costs could rise for the company."])
    assert "UNKNOWN_OWNER" in problems(responses=[{"action": "Consider it.", "owner": "Someone", "trigger": 1}])
    assert "BAD_TRIGGER" in problems(responses=[{"action": "Consider it.", "owner": "Group Risk & Assurance", "trigger": 9}])
    assert "MARK_IN_TEXT" in problems(narrative="HBM could stay scarce. [S1]")
    assert "BAD_CASES" in " ".join(check_scenarios({**draft, "cases": cases[:2]}, sources, anchors, owners, uncertainties))
    assert "BAD_CASES" in " ".join(check_scenarios({**draft, "cases": [cases[2], cases[1], cases[0]]}, sources, anchors, owners, uncertainties))
    assert "UNKNOWN_UNCERTAINTY" in " ".join(check_scenarios({**draft, "builds_on": ["Something else"]}, sources, anchors, owners, uncertainties))
    assert "NO_REASONING" in " ".join(check_scenarios({**draft, "reasoning": ""}, sources, anchors, owners, uncertainties))
    assert check_scenarios({**draft, "builds_on": []}, sources, anchors, owners, []) == [], "a shift without key uncertainties builds on none"
    assert "UNKNOWN_UNCERTAINTY" in " ".join(check_scenarios({**draft, "builds_on": ["anything"]}, sources, anchors, owners, []))
    assert "BAD_SHAPE" in " ".join(check_scenarios({**draft, "builds_on": "none"}, sources, anchors, owners, []))
    assert "BAD_SHAPE" in " ".join(check_scenarios({**draft, "builds_on": [{"a": 1}]}, sources, anchors, owners, []))


def test_pack_init_never_overwrites_a_file(tmp_path: Path) -> None:
    first = group_seed.write_pack(tmp_path, children=["urs"])
    assert len(first["written"]) == 9 and not first["kept"], "seven pack files and two profiles"
    baseline = tmp_path / "ste" / "STE-Global-Baseline.json"
    baseline.write_text('{"signed": true}', encoding="utf-8")
    second = group_seed.write_pack(tmp_path, children=["urs"])
    assert not second["written"] and len(second["kept"]) == 9
    assert baseline.read_text(encoding="utf-8") == '{"signed": true}', "a signed-off register is never replaced"
    plan = json.loads((tmp_path / "ste" / "STE-Scan-Plan.json").read_text(encoding="utf-8"))
    assert len(plan["conditions"]) == 12 and all(set(row["businessAreas"]) <= {"urs"} for row in plan["conditions"]), "relevance is limited to the packs that exist"
    themes = json.loads((tmp_path / "ste" / "STE-Group-Themes.json").read_text(encoding="utf-8"))
    assert len(themes["themes"]) == 8
    assert all(len(keyword) >= 6 or " " in keyword for theme in themes["themes"] for keyword in theme["keywords"]), "every shift keyword can match"


def test_a_signpost_needs_a_distinctive_keyword_not_only_a_shared_place() -> None:
    from grit.pipeline.scenarios import matches_item

    shared = frozenset({"saudi arabia", "vision 2030"})
    keywords = ["Saudi Arabia", "Vision 2030", "mega-projects", "deferrals"]
    assert not matches_item("Saudi Arabia says Vision 2030 programmes hit 93% of targets", keywords, shared), "two shared phrases are not enough"
    assert matches_item("Saudi Arabia announces deferrals of Vision 2030 mega-projects", keywords, shared)
    assert matches_item("Saudi Arabia defers luxury mega-projects", keywords, shared), "a plural or one distinctive phrase with a shared one counts"
    assert matches_item("Saudi Arabia and Vision 2030", ["Saudi Arabia", "Vision 2030"], shared), "an item with only shared phrases needs all of them"
    assert not matches_item("Saudi Arabia alone", ["Saudi Arabia", "Vision 2030"], shared)


def test_news_wide_keywords_do_not_count_as_distinctive_and_failed_keywords_carry_forward(tmp_path: Path) -> None:
    from grit.pipeline.scenarios import _earlier_keywords, _everyday, register_path

    shift = {"cases": [{"signposts": [{"text": "a", "keywords": ["Vision 2030", "deferrals"]}], "counter_indicators": []}]}
    texts = [f"Vision 2030 story {number}" for number in range(10)] + ["Deferrals announced"]
    assert _everyday(shift, texts) == frozenset({"vision 2030"}), "a keyword in 10 of 11 signals is everyday news"
    path = register_path("ste", tmp_path)
    (path.parent / "history").mkdir(parents=True)
    (path.parent / "history" / f"{path.stem}_v1.json").write_text(json.dumps({"shifts": [{"cases": [{"signposts": [{"text": "a", "keywords": ["old"]}]}]}]}))
    path.write_text(json.dumps({"shifts": [{"cases": [{"signposts": [{"text": "a", "keywords": []}, {"text": "b", "keywords": ["new"]}]}]}]}))
    assert _earlier_keywords("ste", tmp_path) == {"a": ["old"], "b": ["new"]}, "an empty list never hides the last good keywords"


def test_approval_keeps_reviewed_shifts_a_refresh_could_not_redraft_and_never_overwrites_history(tmp_path: Path, monkeypatch) -> None:
    from grit.pipeline import scenarios

    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path / "output")
    pool = Pool(tmp_path / "grit.sqlite")
    folder = tmp_path / "output" / "runs" / "2026-10-31-ste" / "scenarios"
    folder.mkdir(parents=True)

    def draft(status: str) -> None:
        cases = [{"case": name, "title": name, "name": f"{name} path", "narrative": "x", "evidence": [], "impact": [], "responses": [],
                  "signposts": ["Alpha signpost one"], "counter_indicators": ["Alpha counter one"]} for name in ("BEST", "MEDIUM", "WORST")]
        (folder / "STE-Scenarios-BMW-Proposed_2026-10-31.json").write_text(json.dumps({"shifts": [{"themeId": "STE-SHIFT-01", "name": "AI", "status": status, "cases": cases if status == "PROPOSED" else []}]}))

    client = reader_scan.stub_client(_group_model)
    draft("PROPOSED")
    first = scenarios.approve(pool, "ste", GROUP_DATE, reviewed_by="Wilson Yeo", client=client, model="stub", config_dir=tmp_path, log=lambda _m: None)
    draft("WITHHELD")
    second = scenarios.approve(pool, "ste", GROUP_DATE, reviewed_by="Wilson Yeo", client=client, model="stub", config_dir=tmp_path, log=lambda _m: None)
    assert (first["version"], second["version"]) == (1, 2) and second["carried_forward"] == ["STE-SHIFT-01"] and second["shifts"] == 1
    path = scenarios.register_path("ste", tmp_path)
    edited = json.loads(path.read_text())
    path.write_text(json.dumps({**edited, "status": "EDITING"}))
    third = scenarios.approve(pool, "ste", GROUP_DATE, reviewed_by="Wilson Yeo", client=client, model="stub", config_dir=tmp_path, log=lambda _m: None)
    assert third["version"] == 3 and sorted(item.name for item in (path.parent / "history").iterdir()) == ["STE-Scenarios_v1.json", "STE-Scenarios_v2.json"]
    assert json.loads((path.parent / "history" / "STE-Scenarios_v1.json").read_text())["version"] == 1, "history is never overwritten"
