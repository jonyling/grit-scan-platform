"""The reader scan end to end, offline: a fake Brave, a fake embedder, and a stub model. No network."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from grit.acquisition.brave_plan import BraveResponse
from grit.agents.reader_prompts import ASSESSOR_SYSTEM, CRITIC_SYSTEM, REPORTER_BOTTOM_LINE_SYSTEM, REPORTER_ITEMS_SYSTEM
from grit.pipeline import reader_scan
from grit.pool import Pool

RUN_DATE = date(2026, 10, 7)
ARTICLES = {
    "PLA aircraft Taiwan Strait": [
        {"title": "Taiwan reports 25 PLA aircraft near Taiwan Strait this week", "url": "https://focustaiwan.tw/cross-strait/202610050001",
         "description": "Taiwan's defence ministry said 25 PLA aircraft and 11 ships operated around Taiwan, below the weekly median.", "page_age": "2026-10-05T03:00:00"},
    ],
    "Taichung MRT Blue Line": [
        {"title": "Taichung MRT Blue Line BC21 tender stays open until 12 October", "url": "https://www.taipeitimes.com/News/taiwan/archives/2026/10/04/2003845",
         "description": "The Taichung MRT Blue Line civil package BC21, worth NT$13.9bn, closes bids on 12 October.", "page_age": "2026-10-04T08:00:00"},
    ],
    "Leonardo SpA": [
        {"title": "Leonardo DiCaprio film premieres", "url": "https://example-news.com/leonardo-film", "description": "Leonardo stars in a new film.", "page_age": "2026-10-03T08:00:00"},
    ],
    "Taiwan typhoon warning": [
        {"title": "Old typhoon story", "url": "https://focustaiwan.tw/old-typhoon", "description": "A typhoon hit Taiwan in July.", "page_age": "2026-07-01T08:00:00"},
    ],
}


def _getter(url: str, params: dict[str, str]) -> BraveResponse:
    results = [dict(item, meta_url={"hostname": item["url"].split("/")[2]}) for item in ARTICLES.get(params["q"], [])]
    return BraveResponse(200, {"results": results}, {"x-ratelimit-limit": "50"})


class _Embedder:
    model, dimensions, batch_size = "gemini-embedding-001", 768, 50

    def embed(self, texts: list[str], *, task: str) -> list[list[float]]:
        vectors = []
        for number, _text in enumerate(texts):
            vector = [0.0] * 768
            vector[(hash(_text) % 700) + number % 3] = 1.0
            vectors.append(vector)
        return vectors


def _model(system: str, payload: dict) -> dict:
    if system == ASSESSOR_SYSTEM:
        if payload["condition"]["id"] == "TW-GLOBAL-03" and payload["sources"]:
            return {"movement": "EASING", "summary": "Taiwan's defence ministry counted 25 PLA aircraft and 11 ships this week, below the weekly median. [S1]",
                    "source_ids": ["S1"], "quotes": [{"source_id": "S1", "quote": "25 PLA aircraft and 11 ships operated around Taiwan"}], "change_from_prior": "Activity eased against last week."}
        return {"movement": "NO_MATERIAL_CHANGE", "summary": "No new development was found this week in the checked sources.", "source_ids": [], "quotes": []}
    if system == CRITIC_SYSTEM:
        return {"issues": [{"item_id": "TW-GLOBAL-03", "kind": "UNSUPPORTED", "problem": "x", "correction": "There were 99 aircraft. [S1]", "source_id": "S1", "quote": "this quote is not in the source at all"}]}
    if system == REPORTER_ITEMS_SYSTEM:
        return {"items": [{"item_id": item["item_id"], "summary": "Bids for the BC21 civil package close on 12 October. [S1]", "source_ids": ["S1"],
                           "quotes": [{"source_id": "S1", "quote": "civil package BC21, worth NT$13.9bn"}]} for item in payload["items"]]}
    if system == REPORTER_BOTTOM_LINE_SYSTEM:
        return {"bottom_line": "No named URS Taiwan project is disrupted. PLA activity eased.", "escalation": "Nothing warrants escalation."}
    raise AssertionError(system[:40])


def _scan(tmp_path: Path) -> tuple[Pool, reader_scan.ReaderRun, list]:
    pool = Pool(tmp_path / "grit.sqlite")
    run = reader_scan.new_run(pool, RUN_DATE, "urs-tw")
    return pool, run, reader_scan.plan(pool, run)


def test_reader_scan_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool, run, specs = _scan(tmp_path)
    assert {spec.lane for spec in specs} == {"CONDITION", "PROJECT", "SUPPLIER", "CUSTOMER", "HAZARD", "WATCHPOINT"}
    options = {"getter": _getter, "usage_path": tmp_path / "usage.json", "sleep": lambda _s: None, "log": lambda _m: None}
    acquired = reader_scan.acquire(pool, run, specs, **options)
    assert acquired["queries_sent"] == len(specs)
    again = reader_scan.acquire(pool, run, specs, **options)
    assert again["queries_sent"] == 0, "a query already answered in this run is never sent again"

    reader_scan.embed(pool, run, embedder=_Embedder())
    result = reader_scan.assess(pool, run, score_harm_fn=lambda *a, **k: {"status": "NOT_RUN", "reason": "test"})
    rows = {row["title"]: row for row in pool.assessments(run.run_id)}
    assert rows["Taiwan reports 25 PLA aircraft near Taiwan Strait this week"]["decision"] == "KEEP"
    assert "TW-GLOBAL-03" in rows["Taiwan reports 25 PLA aircraft near Taiwan Strait this week"]["condition_ids"]
    blue = rows["Taichung MRT Blue Line BC21 tender stays open until 12 October"]
    assert blue["decision"] == "KEEP" and blue["rung"] == "DIRECT"
    assert rows["Leonardo DiCaprio film premieres"]["decision"] == "DROP", "a one-word name with no Taiwan or rail cue is not the supplier"
    assert rows["Old typhoon story"]["drop_reason"] == "STALE"
    assert result["kept"] == 2

    from grit.pipeline.reader_scan import stub_client

    outcome = reader_scan.run_agents(pool, run, client=stub_client(_model), model="stub", log=lambda _m: None)
    conditions = {row["condition_id"]: row for row in pool.conditions(run.run_id)}
    assert len(conditions) == 10
    strait = conditions["TW-GLOBAL-03"]
    assert strait["status"] == "VALIDATED" and strait["trend_label"] == "Easing"
    assert strait["confidence"] == "MEDIUM", "one HIGH-rated outlet is MEDIUM by rule; the model never grades evidence"
    assert strait["source_ids"] == [rows["Taiwan reports 25 PLA aircraft near Taiwan Strait this week"]["signal_id"]]
    assert strait["summary"].endswith(f"[{strait['source_ids'][0]}]"), "citations are stored as signal ids for the renderer"
    assert conditions["TW-GLOBAL-05"]["status"] == "NO_EVIDENCE"
    assert outcome["critic_accepted"] == 0, "an issue without a verbatim quote is rejected"
    checks = {row["item_id"]: row for row in pool.exposure_checks(run.run_id)}
    assert checks["PRJ-TC-BLUE"]["status"] == "FOUND"
    assert checks["PRJ-RK"]["status"] == "NOTHING_FOUND"
    watch = {row["watchpoint_id"]: row for row in pool.watchpoints("urs-tw")}
    assert watch["WP-TW-RKC02"]["cycles_without_update"] == 1
    assert (pool.run(run.run_id)["config"]["bottom_line"]["status"]) == "VALIDATED"
    agents = {row["actor"] for row in pool.execution_log(run.run_id)}
    assert {"Researcher", "Assessor", "Critic", "Reporter", "ReaderAssess"} <= agents


def test_assessor_rejects_an_ungrounded_number() -> None:
    from grit.agents.reader import check_condition

    sources = {"S1": {"title": "t", "text": "Taiwan counted 25 aircraft."}}
    draft = {"movement": "ESCALATING", "summary": "Taiwan counted 40 aircraft. [S1]", "source_ids": ["S1"], "quotes": [{"source_id": "S1", "quote": "Taiwan counted 25 aircraft."}]}
    assert any(item.startswith("UNGROUNDED_NUMBER") for item in check_condition(draft, sources))
    draft["summary"] = "Taiwan counted 25 aircraft. [S2]"
    assert any(item.startswith("UNLISTED_SOURCE") for item in check_condition(draft, sources))


def test_reader_pack_review_and_airis_handoff(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(reader_scan, "OUTPUT_DIR", tmp_path)
    pool, run, specs = _scan(tmp_path)
    options = {"getter": _getter, "usage_path": tmp_path / "usage.json", "sleep": lambda _s: None, "log": lambda _m: None}
    reader_scan.acquire(pool, run, specs, **options)
    reader_scan.embed(pool, run, embedder=_Embedder())
    reader_scan.assess(pool, run, score_harm_fn=lambda *a, **k: {"status": "NOT_RUN", "reason": "test"})
    reader_scan.run_agents(pool, run, client=reader_scan.stub_client(_model), model="stub", log=lambda _m: None)
    pool.finish_run(run.run_id)

    from docx import Document

    from grit.profiles import load_profiles
    from grit.report.airis import export_to_airis
    from grit.report.package import write_pack
    from grit.workflow.packages import review_package

    profiles = load_profiles()
    wilson, gareth, ksyong = profiles["wilson"], profiles["gareth"], profiles["ksyong"]
    pack = write_pack(pool, run.run_id, wilson, folder=tmp_path / "wilson")
    document = Document(pack["files"]["docx"])
    text = "\n".join([paragraph.text for paragraph in document.paragraphs] + [cell.text for table in document.tables for row in table.rows for cell in row.cells])
    assert "GRIT-Scan: URS Taiwan" in text and "Bottom Line" in text
    assert "Direct disruption to named Taiwan rail projects" in text
    assert "Kaohsiung RK Line" in text, "a project with nothing found is still listed"
    assert Path(pack["files"]["pptx"]).is_file() and Path(pack["files"]["xlsx"]).is_file()
    package = pack["package"]
    from grit.workflow.packages import execution_for

    execution = execution_for(run.run_id, "wilson")
    states = pool.current_states()
    assert all(states[(signal_id, execution)] == "PENDING_REVIEW" for signal_id in package["members"])

    import pytest

    with pytest.raises(PermissionError):
        review_package(pool, package_id=package["package_id"], version=package["version"], reviewer=ksyong, intent="APPROVE")
    hwaimay_pack = write_pack(pool, run.run_id, profiles["hwaimay"], folder=tmp_path / "hwaimay")
    assert hwaimay_pack["package"]["members"], "each reader gets their own package over the same signals"
    approved = review_package(pool, package_id=package["package_id"], version=package["version"], reviewer=gareth, intent="APPROVE")
    assert approved["outcome"] == "APPROVE"
    assert all(pool.current_states()[(signal_id, execution)] == "APPROVED" for signal_id in package["members"])

    assert export_to_airis(pool, gareth, folder=tmp_path / "airis")["exported"] == 0, "nothing goes to AIRIS without the BA head's own approval"
    chosen = package["members"][0]
    pool.add_feedback("gareth", chosen, "APPROVE", "Material to Taiwan rail.")
    exported = export_to_airis(pool, gareth, folder=tmp_path / "airis")
    assert exported["exported"] == 1
    assert pool.current_states()[(chosen, execution)] == "AIRIS_HANDOFF_READY"
    payload = json.loads(Path(exported["path"]).read_text())
    assert payload["signals"][0]["package_hash"] == package["content_hash"]
