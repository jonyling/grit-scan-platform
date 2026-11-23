from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from grit.pipeline.exposure_snapshot import (
    check_snapshot, exposure_freshness, exposure_records, is_snapshot, principal_ask, principal_ask_problems,
    snapshot_records, snapshot_summary,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "templates" / "exposure-snapshot.example.json"


def test_the_example_snapshot_is_valid_and_becomes_approved_records():
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert is_snapshot(document) and check_snapshot(document) == []
    records = snapshot_records(document)
    assert len(records) == 5
    site = records[0]
    assert site["exposure"] == {"type": "SITE", "status": "CONFIRMED", "entity": "ST Engineering Saudi Limited"}
    assert "12 staff" in site["text"] and "last confirmed 2026-10-28" in site["text"] and site["published_at"] == "2026-11-02"
    alert = records[-1]
    assert alert["exposure"]["status"] == "CONFIRMED" and "3 km away" in alert["text"], "a site within 10 km of an alert is exposed"
    assert "reference" not in snapshot_summary(document)["sites"][0]
    typed = exposure_records(document)
    assert len(typed) == 5 and all(record.pack == "urs" for record in typed)
    assert typed[0].country == "Saudi Arabia" and typed[0].entity == "ST Engineering Saudi Limited"
    assert typed[0].staff_count == 12 and typed[0].staff_scope == "urs local staff"
    assert typed[0].snapshot_date == date(2026, 11, 2)
    assert typed[0].last_safety_confirmation == date(2026, 10, 28)
    assert typed[-1].status == "CONFIRMED"
    assert exposure_freshness(document, run_date="2026-11-09") == ("2026-11-02", False)
    assert exposure_freshness(document, run_date="2026-11-10") == ("2026-11-02", True)


def test_snapshot_and_principal_ask_contracts_are_pack_scoped():
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert "pack must be 'marine'" in " ".join(check_snapshot(document, pack="marine"))
    with pytest.raises(ValueError, match="cannot be admitted"):
        snapshot_records(document, pack="marine")

    owner = SimpleNamespace(profile_id="reviewer", review_role="REVIEWER", packs=("urs",))
    profiles = {"reviewer": owner}
    raw = {
        "text": "Confirm project exposure with the BA head by 2026-11-06.",
        "owner_profile_id": "reviewer",
        "due_date": "2026-11-06",
        "red_triggers": ["ASSET_DAMAGED"],
    }
    with_ask = {**document, "principalAsk": raw}
    assert snapshot_summary(with_ask)["principalAsk"] == raw
    ask = principal_ask(raw, pack="urs", profiles=profiles)
    assert ask.pack == "urs" and ask.owner_profile_id == "reviewer"
    assert ask.due_date == date(2026, 11, 6) and ask.red_triggers == ("ASSET_DAMAGED",)
    assert any("explicit due_date" in item for item in principal_ask_problems(
        {key: value for key, value in raw.items() if key != "due_date"}, pack="urs", profiles=profiles,
    ))
    for profile in (
        SimpleNamespace(profile_id="reader", review_role="READER", packs=("urs",)),
        SimpleNamespace(profile_id="wrong-pack", review_role="BA_HEAD", packs=("marine",)),
    ):
        broken = {**raw, "owner_profile_id": profile.profile_id}
        problems = principal_ask_problems(broken, pack="urs", profiles={profile.profile_id: profile})
        assert problems and any("REVIEWER or BA_HEAD" in item or "not scoped" in item for item in problems)


def test_a_far_alert_is_possible_exposure_and_a_bad_snapshot_is_refused():
    alert = {"date": "2026-10-27", "location": "Jeddah", "country": "Saudi Arabia", "summary": "Drone intercepted.",
             "nearest": [{"entity": "ST Engineering Saudi Limited", "distanceKm": 850}], "reference": "GRIT alert"}
    (record,) = snapshot_records({"asOf": "2026-11-02", "alerts": [alert]})
    assert record["exposure"]["status"] == "POSSIBLE"
    problems = check_snapshot({"asOf": "2 Nov", "sites": [{"entity": "X", "headcount": "many"}]})
    assert any("asOf" in item for item in problems) and any("needs city" in item for item in problems)
    assert any("whole number" in item for item in problems)
    with pytest.raises(ValueError, match="cannot be admitted"):
        snapshot_records({"asOf": "2026-11-02"})


def test_snapshot_records_are_admitted_as_approved_internal_evidence(tmp_path):
    from grit.pipeline.weekly import add_internal_evidence
    from grit.pool import Pool

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN")
    records = snapshot_records(json.loads(EXAMPLE.read_text(encoding="utf-8")))
    ids = add_internal_evidence(pool, "R", records, SimpleNamespace(review_role="REVIEWER", profile_id="hwaimay"))
    assert len(ids) == 5
    approved = pool.rows("SELECT provenance_json FROM weekly_evidence WHERE run_id = 'R' AND kind = 'HUMAN_APPROVED'")
    assert {row["provenance"]["exposure"]["type"] for row in approved} == {"SITE", "TRAVEL", "CONTRACT", "OTHER"}


def test_cli_preserves_and_validates_principal_ask_from_approved_snapshot(tmp_path, monkeypatch):
    from grit import cli
    from grit.pool import Pool

    approver = SimpleNamespace(
        profile_id="wilson",
        review_role="REVIEWER",
        packs=("urs",),
        can_own_principal_ask=lambda pack: pack == "urs",
    )
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["principalAsk"] = {
        "text": "Confirm the Riyadh staff exposure by the next cycle.",
        "owner_profile_id": "wilson",
        "due_date": "2026-11-06",
        "red_triggers": ["STAFF_HARMED"],
    }
    path = tmp_path / "exposure.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(cli, "_profiles", lambda names: [approver])
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN")
    run = SimpleNamespace(run_id="R", pack="urs")
    args = SimpleNamespace(internal_evidence=str(path), evidence_approved_by="wilson")

    summary = cli._admit_approved_evidence(pool, run, args)

    assert summary["principalAsk"] == document["principalAsk"]
    assert summary["pack"] == "urs"
