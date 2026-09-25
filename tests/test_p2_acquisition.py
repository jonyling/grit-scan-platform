from pathlib import Path

from acquisition.adapters import agile, approved_web, isos, sphera, wild_scan


def test_adapters_emit_candidates_and_drop_scores() -> None:
    fetched_at = "2026-09-25T00:00:00Z"
    approved = approved_web(
        [{"title": "Rail tender", "text": "Vietnam opens a rail programme.", "url": "https://www.reuters.com/a", "risk_score": 25}],
        fetched_at=fetched_at,
    )
    wild = wild_scan([{"title": "Blog note", "text": "Unverified claim."}], fetched_at=fetched_at)
    internal = sphera([{"event_title": "Supplier delay", "description": "Named supplier slipped.", "score": 4}], fetched_at=fetched_at)
    insight = agile([{"headline": "Insight", "summary": "Reference item.", "severity": "Severe"}], fetched_at=fetched_at)
    assert [item.source_lane for item in approved + wild + internal + insight] == ["APPROVED", "WILD", "SPHERA", "AGILE"]
    assert isos(None, fetched_at=fetched_at) == []
    for candidate in approved + wild + internal + insight:
        record = candidate.as_record()
        assert "score" not in record
        assert "risk_score" not in record
        assert "severity" not in record
        assert len(candidate.content_hash) == 64


def test_acquisition_modules_do_not_call_the_scorer() -> None:
    root = Path(__file__).parents[1] / "acquisition"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "assess_impact" not in text
        assert "impact_scoring" not in text
