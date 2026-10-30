"""The shared source ratings: a fallback for every pack that a workbook rebuild cannot wipe."""

from datetime import date
from types import SimpleNamespace

import pytest

from grit.ontology.matchers.credibility_lookup import credibility_lookup
from grit.ontology.source_ratings import confirm, seed, set_rating

SHARED = {"ratings": [{"domain": "reuters.com", "sourceName": "Reuters", "rating": "HIGH", "status": "PROPOSED"}]}
REGISTER = {"sources": [
    {"sourceDomain": "reuters.com", "credibilityRating": "MEDIUM", "active": True},
    {"sourceDomain": "straitstimes.com", "credibilityRating": "UNRATED", "active": True},
]}


def test_a_pack_rating_wins_and_the_shared_list_fills_an_unrated_or_missing_outlet() -> None:
    assert credibility_lookup("https://www.reuters.com/x", REGISTER, SHARED)["credibilityRating"] == "MEDIUM", "the pack's own rating wins"
    unrated = credibility_lookup("https://www.reuters.com/x", {"sources": [{"sourceDomain": "reuters.com", "credibilityRating": "UNRATED", "active": True}]}, SHARED)
    assert unrated["credibilityRating"] == "HIGH" and unrated["matchMethod"] == "SHARED_RATING" and unrated["ratingStatus"] == "PROPOSED"
    missing = credibility_lookup("https://jp.reuters.com/y", {"sources": []}, SHARED)
    assert missing["credibilityRating"] == "HIGH" and missing["active"] and missing["sourceName"] == "Reuters", "a subdomain matches"
    assert credibility_lookup("https://example.com/z", REGISTER, SHARED)["credibilityRating"] == "UNRATED"


def test_seeding_takes_the_lower_rating_when_registers_disagree(monkeypatch: pytest.MonkeyPatch) -> None:
    import grit.ontology.pack as pack_module

    registers = {
        "a": {"registerId": "A", "sources": [{"sourceDomain": "x.com", "sourceName": "X", "credibilityRating": "HIGH"}, {"sourceDomain": "y.com", "credibilityRating": "UNRATED"}]},
        "b": {"registerId": "B", "sources": [{"sourceDomain": "X.com", "sourceName": "X", "credibilityRating": "MEDIUM"}]},
    }
    monkeypatch.setattr(pack_module, "load_ontology_pack", lambda _dir, name: SimpleNamespace(source_register=registers[name]))
    (entry,) = seed(packs=("a", "b"))["ratings"]
    assert entry["domain"] == "x.com" and entry["rating"] == "MEDIUM" and entry["status"] == "PROPOSED"
    assert "A: HIGH; B: MEDIUM" in entry["basis"] and "lower rating" in entry["basis"]


def test_setting_and_confirming_names_the_person_and_the_date() -> None:
    document = {"ratings": [{"domain": "x.com", "rating": "HIGH", "status": "PROPOSED"}, {"domain": "y.com", "rating": "LOW", "status": "PROPOSED"}]}
    entry = set_rating(document, "https://www.reuters.com/world", "high", by="Wilson Yeo", name="Reuters", today=date(2026, 10, 30))
    assert entry == {"domain": "reuters.com", "sourceName": "Reuters", "basis": "Set by hand.", "rating": "HIGH", "status": "CONFIRMED", "confirmedBy": "Wilson Yeo", "confirmedOn": "2026-10-30"}
    assert confirm(document, by="Wilson Yeo", domains=["x.com"]) == 1 and document["ratings"][1]["status"] == "PROPOSED"
    assert confirm(document, by="Wilson Yeo") == 1 and all(row["status"] == "CONFIRMED" for row in document["ratings"])
    with pytest.raises(ValueError):
        set_rating(document, "x.com", "TOP", by="Wilson Yeo")
