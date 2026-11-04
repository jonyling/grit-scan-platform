from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from grit.pipeline.exposure_snapshot import check_snapshot, is_snapshot, snapshot_records, snapshot_summary

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
