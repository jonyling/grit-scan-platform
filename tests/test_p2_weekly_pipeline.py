import json
from pathlib import Path

from grit_scan.ontology import OntologyPack
from grit_scan.weekly_pipeline import compose_delta, import_scan, rank, revise, run_weekly_pipeline


def _pack() -> OntologyPack:
    return OntologyPack(
        source_register={"sources": [{"sourceName": "Reuters", "sourceDomain": "reuters.com", "credibilityRating": "HIGH", "active": True}]},
        exposure={"projects": [], "customers": [], "suppliers": [], "sites": []},
        lob_profiles={
            "lobs": [
                {
                    "lobId": "LOB-004",
                    "lobCode": "URS-SM-MRL",
                    "lobName": "Mobility Rail",
                    "baName": "Urban Solutions",
                    "owner": "URS",
                    "active": True,
                    "keywords": ["rail"],
                    "priorityCountries": ["Taiwan"],
                }
            ]
        },
        themes={"themes": []},
        baseline={
            "baselineId": "URS-TW-BASE",
            "effectiveDate": "2026-09-01",
            "conditions": [
                {"id": "C01", "name": "Taiwan Strait pressure", "domain": "Security", "steThemes": ["Geopolitical volatility"]},
                {"id": "C02", "name": "Unused condition", "domain": "Other", "steThemes": []},
            ],
        },
        config_dir=Path("."),
    )


def test_import_rejects_placeholders_and_assigns_development_ids() -> None:
    raw = """
    Narrative before the array.
    [{"conditionHint": "Taiwan Strait pressure", "movement": "WORSENING", "evidenceConfidence": "HIGH",
      "summary": "Pressure increased.", "headline": "Strait pressure",
      "sources": [{"url": "https://www.reuters.com/world/asia-pacific/story"}]}]
    """
    imported = import_scan(raw, _pack().baseline, revision_id="URS-TW-WEEKLY-2026-09-21", revision_date="2026-09-21")
    assert imported["developments"][0]["developmentId"] == "DEV-C01-01"
    assert imported["developments"][0]["movement"] == "ESCALATING"
    assert imported["rejected"] == []

    rejected = import_scan(
        '[{"conditionHint": "Taiwan Strait pressure", "movement": "ESCALATING", "evidenceConfidence": "HIGH", "summary": "x", "sources": [{"url": "https://example.com/story"}]}]',
        _pack().baseline,
        revision_id="R",
        revision_date="2026-09-21",
    )
    assert rejected["developments"] == []
    assert rejected["rejected"]


def test_weekly_pipeline_writes_revision_without_a_score() -> None:
    pack = _pack()
    scan = json.dumps(
        [
            {
                "conditionHint": "C01",
                "movement": "ESCALATING",
                "evidenceConfidence": "HIGH",
                "headline": "Strait pressure",
                "summary": "Pressure increased on the rail corridor.",
                "sources": [{"url": "https://www.reuters.com/world/asia-pacific/story"}],
                "descentCandidate": {
                    "businessArea": "Urban Solutions",
                    "lineOfBusiness": "Mobility Rail",
                    "entityOrUnit": "URS Taiwan",
                    "transmissionPath": "agent prose that must be replaced",
                },
            }
        ]
    )
    revision_input = import_scan(scan, pack.baseline, revision_id="URS-TW-WEEKLY-2026-09-21", revision_date="2026-09-21")
    revision = revise(revision_input, pack)
    assert revision["revisions"][0]["descentStatus"] == "SUPPORTED"
    assert revision["revisions"][0]["descent"]["transmissionPath"] is None
    assert "score" not in revision["revisions"][0]
    text = compose_delta(revision)
    assert "URS TAIWAN WEEKLY STRATEGIC DELTA" in text
    assert "C02" in text

    output = Path("/tmp/grit-p2-week")
    paths = run_weekly_pipeline(scan, pack, output, revision_date="2026-09-21")
    written = json.loads(paths["revision"].read_text(encoding="utf-8"))
    assert written["revisionId"] == "URS-TW-WEEKLY-2026-09-21"
    assert written["developmentsReceived"] == 1


def test_rank_adds_the_powerapps_trend_risklevel_and_relevance_basis_fields() -> None:
    pack = _pack()
    scan = json.dumps(
        [
            {
                "conditionHint": "C01",
                "movement": "ESCALATING",
                "evidenceConfidence": "HIGH",
                "headline": "Strait pressure",
                "summary": "Pressure increased on the rail corridor.",
                "sources": [{"url": "https://www.reuters.com/world/asia-pacific/story"}],
            }
        ]
    )
    revision_input = import_scan(scan, pack.baseline, revision_id="URS-TW-WEEKLY-2026-09-21", revision_date="2026-09-21")
    revision = revise(revision_input, pack)
    ranking = rank(revision, pack)
    item = ranking["rankedItems"][0]
    assert item["trend"] == "ESCALATING"
    expected_risk_level = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "WATCHPOINT": "LOW"}[item["priorityLabel"]]
    assert item["riskLevel"] == expected_risk_level
    assert "Security condition" in item["businessRelevanceBasis"]
    assert "Geopolitical volatility" in item["businessRelevanceBasis"]
    assert "score" not in item

