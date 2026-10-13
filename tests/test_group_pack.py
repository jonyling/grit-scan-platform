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
        area = payload["business_area"]
        anchor = area["countries"][0] if area.get("countries") else area["business_areas"][0]
        scenario = {"horizon": "1-5 years", "signposts": ["Memory makers announce new capacity", "HBM contract prices fall"], "counter_indicators": ["Prices keep rising"],
                    "risk_for_ste": f"Memory costs could squeeze project margins for STE in {anchor}.", "opportunity_for_ste": None, "source_ids": ["S1"]}
        return {"reasoning": REASONING, "scenarios": [
            {**scenario, "name": "Persistent premium", "narrative": "HBM could stay scarce as AI demand grows."},
            {**scenario, "name": "Capacity catch-up", "narrative": "New capacity may ease memory prices."},
        ]}
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

    scenarios = propose_scenarios(pool, run, client=reader_scan.stub_client(_group_model), model="stub", log=lambda _m: None)
    proposed = json.loads(Path(scenarios["files"]["json"]).read_text(encoding="utf-8"))
    memory = next(item for item in proposed["shifts"] if item["name"] == "Semiconductor/GPU access")
    assert memory["status"] == "PROPOSED" and [item["name"] for item in memory["scenarios"]] == ["Persistent premium", "Capacity catch-up"]
    assert REASONING not in json.dumps(proposed), "the Strategist's reasoning stays in the execution log"
    assert any(REASONING in str((row.get("detail") or {}).get("reasoning")) for row in pool.execution_log(run.run_id) if row["actor"] == "Strategist")

    ceo = load_profiles(config / "profiles")["vschong"]
    files = write_pack(pool, run.run_id, ceo, folder=tmp_path / "ceo", submit=False)["files"]
    document = Document(files["docx"])
    radar = next([[cell.text for cell in row.cells] for row in table.rows] for table in document.tables if table.rows[0].cells[0].text == "Theme and what changed")
    assert radar[0] == ["Theme and what changed", "Sources, date and time", "Risk and opportunity for STE", "Business areas", "Counter-indicator", "Proposed response", "Priority and trend"]
    assert len(radar) == 13, "one row for each of the 12 conditions"
    row = next(item for item in radar[1:] if item[0].startswith("2. "))
    assert "Urban Solutions" in row[3] and row[4].startswith("A durable ceasefire") and "Consider reviewing site security plans" in row[5]
    deck = Presentation(files["pptx"])
    assert len(deck.slides) == 3, "a cover, the summary, and one appendix slide for the one condition with evidence"


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

    anchors = ["Digital Systems & Cyber"]
    scenario = {"name": "Persistent premium", "horizon": "1-5 years", "narrative": "HBM could stay scarce.", "signposts": ["New capacity", "Prices fall"],
                "counter_indicators": ["Prices keep rising"], "risk_for_ste": "Costs could rise for Digital Systems & Cyber.", "opportunity_for_ste": None, "source_ids": ["S1"]}
    draft = {"reasoning": "Prices rose.", "scenarios": [scenario, {**scenario, "name": "Catch-up"}]}
    assert check_scenarios(draft, sources, anchors) == []

    def problems(**changes) -> str:
        return " | ".join(check_scenarios({**draft, "scenarios": [{**scenario, **changes}, scenario]}, sources, anchors))

    assert "NOT_CONDITIONAL" in problems(narrative="HBM stays scarce.")
    assert "CERTAINTY_CLAIM" in problems(narrative="HBM could and will stay scarce.")
    assert "BAD_COUNT" in problems(signposts=["Only one"])
    assert "NO_ANCHOR" in problems(risk_for_ste="Costs could rise for the company.")
    assert "UNCITED" in problems(source_ids=[])
    assert "MARK_IN_TEXT" in problems(narrative="HBM could stay scarce. [S1]")
    assert "UNGROUNDED_NUMBER" in problems(narrative="HBM could stay 30 percent dearer.")
    assert "BAD_VALUE" in problems(horizon="next year")
    assert "BAD_COUNT" in " ".join(check_scenarios({**draft, "scenarios": [scenario]}, sources, anchors))
    assert "NO_REASONING" in " ".join(check_scenarios({**draft, "reasoning": ""}, sources, anchors))


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
