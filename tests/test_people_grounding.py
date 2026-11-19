"""Shared staff-log grounding across lanes (plan 17 Nov 2026). Fixtures only — never live secrets/."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from grit.core.people import (
    STAFF_AFFINITY,
    append_staff_basis,
    apply_staff_priority,
    countries_named,
    ground_page,
    is_adverse_signal,
    load_people,
    people_risk_in_lead,
    presence,
    staff_priority_key,
)


def _staff_file(tmp_path: Path) -> Path:
    path = tmp_path / "internal-context.json"
    path.write_text(json.dumps({
        "middle_east_local_staff": {
            "as_at": "2026-11-04",
            "entries": [
                {"entity": "URS", "country": "Qatar", "headcount": 52},
                {"entity": "iDirect", "country": "UAE (Dubai)", "headcount": 9},
            ],
        },
        "middle_east_travellers_and_assignees": {"as_at": "2026-10-30", "by_country": {"Qatar": 21, "UAE": 4}},
    }), encoding="utf-8")
    return path


def test_shared_module_loads_staff_by_country(tmp_path: Path) -> None:
    people = load_people(_staff_file(tmp_path), today=date(2026, 11, 17), addresses={})
    assert set(people["countries"]) == {"Qatar", "United Arab Emirates"}
    assert countries_named("Missiles over Doha", people) == ["Qatar"]
    assert "52 local staff" in presence("Qatar", people)


def test_people_risk_plus_staffed_country_grounds_a_page(tmp_path: Path) -> None:
    people = load_people(_staff_file(tmp_path), today=date(2026, 11, 17), addresses={})
    terms = ["missile*", "evacuat*"]
    assert people_risk_in_lead("Missiles intercepted over Doha", "", terms) == ["missile*"]
    grounding = ground_page("Missiles intercepted over Doha", "Air defences engaged.", people, terms=terms)
    assert grounding is not None and grounding.countries == ("Qatar",)
    assert "staff log" in grounding.reason.casefold()
    # Ukraine has no staff entry: not grounded.
    assert ground_page("Missiles intercepted over Kyiv", "", people, terms=terms) is None
    # People-risk without a staffed country does not invent exposure.
    assert ground_page("Missiles intercepted over Kyiv", "", people, terms=terms) is None
    # Staffed country without people-risk terms does not ground.
    assert ground_page("Qatar awards a metro contract", "Commercial award.", people, terms=terms) is None


def test_priority_and_basis_cite_staff_without_changing_rung(tmp_path: Path) -> None:
    people = load_people(_staff_file(tmp_path), today=date(2026, 11, 17), addresses={})
    grounding = ground_page("Evacuation ordered in Doha", "", people, terms=["evacuat*"])
    priority = apply_staff_priority({"tier": 3, "reasons": ["Theme hit."]}, grounding)
    assert priority["tier"] == 3 and priority["staff_priority"] is True and priority["staff_affinity"] == STAFF_AFFINITY
    assert any("Qatar" in reason for reason in priority["reasons"])
    basis = append_staff_basis("Counterparty link.", grounding)
    assert "Counterparty link." in basis and "staff log" in basis.casefold()
    assert staff_priority_key({"priority_json": priority}) == 0
    assert staff_priority_key({"priority_json": {"tier": 3}}) == 1


def test_adverse_classifier_uses_staff_match_or_included_set(tmp_path: Path) -> None:
    people = load_people(_staff_file(tmp_path), today=date(2026, 11, 17), addresses={})
    record = {"signal_id": "SIG-1", "url": "https://n.example/doha", "title": "Missiles over Doha", "text": ""}
    assert is_adverse_signal(record, people=people, terms=["missile*"])
    assert not is_adverse_signal(
        {"signal_id": "SIG-2", "url": "https://n.example/kyiv", "title": "Missiles over Kyiv", "text": ""},
        people=people, terms=["missile*"],
    )
    assert is_adverse_signal(
        {"signal_id": "SIG-9", "url": "https://n.example/other", "title": "Quiet day", "text": ""},
        people=people, adverse_signal_ids={"SIG-9"}, terms=["missile*"],
    )


def test_sharepoint_columns_include_adverse() -> None:
    from grit.review.sharepoint import COLUMNS, row_fields

    names = [name for name, _kind, _note in COLUMNS]
    assert "Adverse" in names
    fields = row_fields(
        {"signal_id": "SIG-1", "title": "Missiles over Doha", "text": "", "url": "https://n.example/1",
         "publisher": "N", "tier": 3, "red": 0, "relevance_basis": "x"},
        day=date(2026, 11, 17), position=1, memory_note="",
        adverse=True,
    )
    assert fields["Adverse"] is True


def test_adverse_docx_and_csv_use_shared_chrome(tmp_path: Path) -> None:
    from grit.adverse import checks, render
    from grit.report.table_style import ADVERSE_SECTION, NAVY_HEX, TABLE_STYLE

    report = {
        "run_date": "2026-11-13",
        "status": "READY",
        "window": {"start": "2026-11-06", "end": "2026-11-13", "previous": None},
        "items": {
            "A": [{
                "item_id": "A-1", "headline": "Missiles over Doha", "event": "Missiles over Doha",
                "severity": "High", "action": checks.FOLLOW_GF, "why_adverse": "Threat to people where STE staff are.",
                "exposure": checks.INDIRECT_EXPOSURE, "exposure_basis": checks.STAFF_LOG,
                "people": ["Qatar: 52 local staff (URS), as at 4 November 2026."],
                "event_date": "2026-11-12", "publication_date": "2026-11-12",
                "sources": [{"url": "https://n.example/1", "outlet": "Wire"}],
            }],
            "B": [], "C": [],
        },
        "attention": {"immediate": [], "monitoring": [], "lessons": []},
        "overall": ("Stable", "No new escalation beyond the staffed-country item."),
        "validation": ["Internal context: staff log as at 4 November 2026."],
    }
    folder = tmp_path / "adverse"
    docx = render.render_docx(report, folder / "adverse.docx")
    csv_path = render.write_signals_csv(folder, report)
    assert docx.is_file() and csv_path.is_file()
    text = csv_path.read_text(encoding="utf-8-sig")
    assert text.splitlines()[0] == ",".join(render.CSV_COLUMNS)
    assert "Adverse" in text and "Qatar" in text and "Yes" in text
    assert ADVERSE_SECTION[0].startswith("Event")
    assert TABLE_STYLE == "Table Grid" and NAVY_HEX == "1F3864"
    # Incomplete runs must not invent Word/CSV; only READY write() attaches them.
    incomplete = {**report, "status": "INCOMPLETE", "counts": {"pages": 0, "verified_pages": 0},
                  "blockers": ["x"], "repeats": [], "withheld": [], "notes": {}, "calls": [],
                  "own_unreported": [], "own_reviewed": [], "search": {}}
    # Bypass compose's Section A category loop by calling the READY gate directly.
    paths = {"email": folder / "email-DRAFT-INCOMPLETE.txt"}
    assert incomplete["status"] != "READY"
    assert "docx" not in paths and "csv" not in paths


def test_adverse_people_reexports_shared_module() -> None:
    from grit.adverse import people as adverse_people
    from grit.core import people as core_people

    assert adverse_people.load_people is core_people.load_people
    assert adverse_people.PEOPLE_RISK == "PEOPLE_RISK"


def test_weekly_rank_orders_staffed_items_first_without_changing_score_or_label(tmp_path: Path, monkeypatch) -> None:
    import grit.core.people as people_module
    from grit.core.conditions import rank
    from grit.ontology.pack import load_ontology_pack
    from grit.settings import CONFIG_DIR

    people = load_people(_staff_file(tmp_path), today=date(2026, 11, 17), addresses={})
    monkeypatch.setattr(people_module, "load_people", lambda *args, **kwargs: people)
    base = {"evidenceConfidence": "HIGH", "movement": "ESCALATING", "steThemes": ["A", "B"], "sources": []}
    revision = {"revisions": [
        {**base, "developmentId": "plain", "headline": "Factory output update in Vietnam"},
        {**base, "developmentId": "staffed", "headline": "Missile strike kills workers in Doha, Qatar"},
    ]}
    ranked = rank(revision, load_ontology_pack(CONFIG_DIR))["rankedItems"]
    assert [item["developmentId"] for item in ranked] == ["staffed", "plain"]
    assert {(item["priorityScore"], item["priorityLabel"]) for item in ranked} == {(4, "MEDIUM")}
    assert ranked[0]["staffCountries"] == ["Qatar"]


def test_staff_log_becomes_internal_evidence_without_an_approver(tmp_path: Path) -> None:
    from grit.core.people import STAFF_LOG_APPROVER, staff_snapshot
    from grit.pipeline.exposure_snapshot import check_snapshot
    from grit.pipeline.weekly import add_staff_log
    from grit.pool import Pool

    path = _staff_file(tmp_path)
    document = staff_snapshot(path)
    assert check_snapshot(document) == []
    assert {(row["entity"], row["country"], row["city"], row["headcount"]) for row in document["sites"]} == {
        ("URS", "Qatar", "Not stated", 52), ("iDirect", "United Arab Emirates", "Dubai", 9)}
    assert {(row["country"], row["travellers"]) for row in document["travel"]} == {("Qatar", 21), ("United Arab Emirates", 4)}

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-19", pack="marine", kind="READER_SCAN", config={"weekly_mode": "reasoned"})
    summary = add_staff_log(pool, "R", path=path)
    assert summary["approvedBy"] == STAFF_LOG_APPROVER and len(summary["sites"]) == 2
    add_staff_log(pool, "R", path=path)  # a second admission adds nothing
    approved = pool.rows("SELECT provenance_json FROM weekly_evidence WHERE run_id = 'R' AND kind = 'HUMAN_APPROVED'")
    assert len(approved) == 4
    assert {row["provenance"]["approved_by"] for row in approved} == {STAFF_LOG_APPROVER}
    titles = {row["title"] for row in pool.signals_for_run("R")}
    assert "URS: 52 staff in Qatar" in titles and "iDirect: 9 staff in Dubai" in titles


def test_weekly_sources_tag_only_the_business_area_s_own_staffed_countries(tmp_path: Path, monkeypatch) -> None:
    import grit.core.people as people_module
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore, add_staff_log
    from grit.pool import Pool

    path = _staff_file(tmp_path)
    people = load_people(path, today=date(2026, 11, 19), addresses={})
    monkeypatch.setattr(people_module, "load_people", lambda *args, **kwargs: people)
    monkeypatch.setattr(people_module, "staff_log_path", lambda: path)
    tagged = {}
    for pack in ("urs", "marine"):
        pool = Pool(tmp_path / f"{pack}.sqlite")
        pool.start_run("R", run_date="2026-11-19", pack=pack, kind="READER_SCAN", config={"weekly_mode": "reasoned", "window_days": 7})
        page = pool.add_signal(url="https://example.com/doha", title="Drone strike near Doha port", text="Air defences engaged over Qatar.",
                               publisher="example.com", published_at="2026-11-18", lane="THEME", content_hash="doha")
        pool.add_sighting(page, "R", query_id="q", query="q", lane="THEME", target_id=None, rank=1)
        from grit.pipeline.reader_scan import _load_pack

        add_staff_log(pool, "R", path=path, pack=_load_pack(pack))
        coordinator = WeeklyCoordinator(WeeklyStore(pool, "R", search_limit=0), client=None, model="offline")
        found = coordinator.sources
        tagged[pack] = (found[page].get("staff_countries"), sum(bool(row.get("internal_exposure")) for row in found.values()))
    # URS's register names URS, so its Qatar staff count. Marine has none in the log: no tag, no internal records.
    assert tagged == {"urs": (["Qatar"], 1), "marine": (None, 0)}


def test_every_run_reports_the_staff_log_in_its_exposure_snapshot(tmp_path: Path, monkeypatch) -> None:
    import argparse

    import grit.core.people as people_module
    from grit.cli import _admit_supplied_evidence
    from grit.pool import Pool

    path = _staff_file(tmp_path)
    monkeypatch.setattr(people_module, "staff_log_path", lambda: path)
    pool = Pool(tmp_path / "grit.sqlite")
    for run_id, mode in (("REASONED", "reasoned"), ("PLAIN", None)):
        pool.start_run(run_id, run_date="2026-11-19", pack="marine", kind="READER_SCAN", config={"weekly_mode": mode})
        run = argparse.Namespace(run_id=run_id)
        _admit_supplied_evidence(pool, run, argparse.Namespace())
        config = pool.run(run_id)["config"]
        assert config["exposure_snapshot"]["approvedBy"] == people_module.STAFF_LOG_APPROVER
        assert config["weekly_context"]["exposure_snapshot"]["sites"]
    # Only the reasoned run gets internal evidence records for the agents.
    counts = {row["run_id"]: row["n"] for row in pool.rows("SELECT run_id, COUNT(*) AS n FROM weekly_evidence GROUP BY run_id")}
    assert counts == {"REASONED": 4}


def test_a_business_area_without_staff_in_the_log_reads_not_supplied(tmp_path: Path) -> None:
    from grit.core.people import staff_snapshot
    from grit.pipeline.reader_scan import _load_pack
    from grit.report.markdown import _exposure

    path = _staff_file(tmp_path)
    urs = staff_snapshot(path, pack=_load_pack("urs"))
    assert [(row["entity"], row["country"]) for row in urs["sites"]] == [("URS", "Qatar")] and not urs["travel"] and not urs["notSupplied"]
    marine = staff_snapshot(path, pack=_load_pack("marine"))
    assert marine["notSupplied"] and not marine["sites"]
    assert _exposure(marine)[0].startswith("Marine exposure: not supplied.")
    group = staff_snapshot(path, pack=_load_pack("ste"))
    assert {row["entity"] for row in group["sites"]} == {"URS", "iDirect"} and group["travel"], "the Group pack sees the whole log"


def test_the_principal_ask_rests_only_on_a_strict_business_area_link():
    from grit.core.ba_link import links, register
    from grit.pipeline.reader_scan import _load_pack
    from grit.pipeline.weekly import ask_basis_problems

    area = register(_load_pack("marine"))
    # A Navantia steel supplier links only through a supplier class: no link.
    assert links("Liberty Steel's difficulties threaten 34,000 mt Royal Navy steel plate order",
                 "Dalzell plate mill supplies Navantia's Fleet Solid Support ships.", area) == []
    gulf = links("ADNOC L&S vessel attacked in the Strait of Hormuz", "One crew member missing and 11 injured off the United Arab Emirates.", area)
    assert any("ST Engineering Marine Ltd. - Abu Dhabi Branch" in item for item in gulf)
    kuwait = links("Iran attacks Kuwait air base", "Drones attacked Ali Al Salem air base in Kuwait.", area)
    assert any("Kuwait Naval Force" in item for item in kuwait)

    basis = [{"signal_id": "SIG-a", "title": "t", "links": gulf}]
    assert ask_basis_problems({"basis_id": "SIG-a"}, basis) == []
    assert ask_basis_problems({"basis_id": "SIG-dalzell"}, basis) and ask_basis_problems({}, basis)
    assert ask_basis_problems({"basis_id": None}, []) == [] and ask_basis_problems({"basis_id": "SIG-a"}, [])
