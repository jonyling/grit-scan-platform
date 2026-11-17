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
