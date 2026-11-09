"""Weekly report v4: dated sequence display, conflict settlement, EBIT bands, forum ask, and rank noise."""

from __future__ import annotations

import json
from pathlib import Path

from grit.agents.reader import check_sequence, format_timeline
from grit.core.financial_band import band_for_amount, financial_size
from grit.pipeline.weekly import rank_noise
from grit.report.format_v2 import settle_count_conflict


URS_BANDS = json.loads((Path(__file__).resolve().parents[1] / "config" / "urs" / "pack.json").read_text(encoding="utf-8"))["severityBands"]


def test_sequence_requires_dates_already_on_the_cited_page() -> None:
    sources = {
        "S1": {"title": "Alert", "text": "Saudi forces intercepted missiles on 27 Oct over Riyadh.", "date": "27 Oct 2026"},
    }
    steps, problems = check_sequence(
        [{"date": "27 Oct", "text": "Saudi forces intercepted missiles over Riyadh.", "source_id": "S1"},
         {"date": "28 Oct", "text": "Saudi forces intercepted missiles over Riyadh.", "source_id": "S1"}],
        ["S1"], sources)
    assert steps == [] and any(item.startswith("SEQUENCE_DATE") for item in problems)
    ok, problems = check_sequence(
        [{"date": "27 Oct", "text": "Saudi forces intercepted missiles over Riyadh.", "source_id": "S1"},
         {"date": "27 Oct", "text": "The alert named Riyadh.", "source_id": "S1"}],
        ["S1"], {"S1": {**sources["S1"], "text": sources["S1"]["text"] + " The alert named Riyadh."}})
    assert problems == [] and "27 Oct:" in format_timeline(ok, "Staff in Riyadh could face disruption [S1].")


def test_conflict_settlement_prefers_the_stronger_source_band() -> None:
    members = [
        {"publisher": "mod.gov.sa", "source_band": "T1"},
        {"publisher": "blog.example", "source_band": "T4"},
    ]
    chosen, outlets = settle_count_conflict({"163": {"mod.gov.sa"}, "600": {"blog.example"}}, members)
    assert chosen == "163" and outlets == {"mod.gov.sa"}
    tied = settle_count_conflict({"100": {"a.com", "b.com"}, "200": {"c.com"}}, [
        {"publisher": "a.com", "source_band": "T3"},
        {"publisher": "b.com", "source_band": "T3"},
        {"publisher": "c.com", "source_band": "T3"},
    ])
    assert tied[0] == "100", "equal bands keep the figure more outlets report"


def test_financial_size_labels_only_a_stated_pack_currency_amount() -> None:
    assert financial_size("No money figure here.", URS_BANDS)["label"] == "not established"
    labelled = financial_size("The project loss is S$30 million this year.", URS_BANDS)
    assert labelled["label"] == "Major" and labelled["amount_millions"] == 30.0
    assert band_for_amount(1.0, URS_BANDS["bands"]) == "Insignificant"
    assert band_for_amount(70.0, URS_BANDS["bands"]) == "Severe"


def test_rank_noise_drops_hobby_notes_without_a_register_anchor() -> None:
    names = ["Urban Solutions", "Mobility Rail", "ST Engineering Saudi Limited"]
    golf = rank_noise(
        {"decision": "INCLUDE", "fact": "A golf tournament opened in Florida.", "exposure": "not established",
         "transmission": "same region as a project", "impact": "not established", "risk_ids": []},
        {"title": "Amateur golf opens", "rung": "THEME", "condition_ids": []},
        names,
    )
    assert golf is True
    direct = rank_noise(
        {"decision": "INCLUDE", "fact": "A fire hit a plant.", "exposure": "possible", "transmission": "site staff",
         "impact": "not established", "risk_ids": []},
        {"title": "Plant fire", "rung": "DIRECT", "condition_ids": []},
        names,
    )
    assert direct is False
    anchored = rank_noise(
        {"decision": "INCLUDE", "fact": "A strike hit Mobility Rail suppliers.", "exposure": "possible",
         "transmission": "parts delay", "impact": "not established", "risk_ids": ["URS-KEY-05"]},
        {"title": "Rail strike", "rung": "PERIMETER", "condition_ids": ["URS-KEY-05"]},
        names,
    )
    assert anchored is False


def test_urs_pack_carries_action_posture_and_severity_bands() -> None:
    pack = json.loads((Path(__file__).resolve().parents[1] / "config" / "urs" / "pack.json").read_text(encoding="utf-8"))
    assert "nextForum" in pack["actionPosture"] and "nextForumDate" in pack["actionPosture"]
    assert pack["severityBands"]["currency"] == "SGD"
    assert pack["severityBands"]["bands"][0]["name"] == "Insignificant"
    assert pack["severityBands"]["bands"][-1]["name"] == "Severe"
