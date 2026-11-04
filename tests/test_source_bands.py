from __future__ import annotations

from datetime import date

import pytest

from grit.core.source_bands import evidence_level, source_band, supports_fact

CONTESTED = frozenset({"naturalnews.com"})


@pytest.mark.parametrize("url, rating, source_class, band", [
    ("https://sa.usembassy.gov/security-alert", None, None, "T1"),
    ("https://www.mas.gov.sg/news", None, None, "T1"),
    ("https://example.org/notice", None, "GOVERNMENT", "T1"),
    ("https://www.reuters.com/world", "HIGH", None, "T2"),
    ("https://www.example.com/x", "LOW", None, "T3"),
    ("https://houseofsaud.com/x", None, None, "UNRATED"),
    ("https://finance.yahoo.com/x", "HIGH", None, "T4"),
    ("https://www.naturalnews.com/x", "HIGH", None, "CONTESTED"),
])
def test_a_source_band_follows_the_rule_and_the_r_and_a_lists(url, rating, source_class, band):
    assert source_band(url, rating, None, source_class=source_class, contested=CONTESTED) == band


def _outlet(name, band, **extra):
    return {"outlet": name, "url": f"https://{name}/x", "band": band, **extra}


@pytest.mark.parametrize("outlets, level", [
    ([_outlet("sa.usembassy.gov", "T1"), _outlet("houseofsaud.com", "UNRATED")], 3),
    ([_outlet("sa.usembassy.gov", "T1")], 2),
    ([_outlet("reuters.com", "T2")], 2),
    ([_outlet("houseofsaud.com", "UNRATED"), _outlet("jns.org", "UNRATED")], 2),
    ([_outlet("houseofsaud.com", "UNRATED")], 1),
    ([_outlet("naturalnews.com", "CONTESTED"), _outlet("yahoo.com", "T4")], 0),
    ([_outlet("internal:SIG-1", "T1", internal=True)], 3),
])
def test_evidence_follows_methodology_13d(outlets, level):
    assert evidence_level(outlets) == level


def test_a_fact_needs_a_rated_source_or_two_independent_unrated_outlets():
    assert supports_fact([_outlet("example.com", "T3")])
    assert supports_fact([_outlet("houseofsaud.com", "UNRATED"), _outlet("jns.org", "UNRATED")])
    assert not supports_fact([_outlet("houseofsaud.com", "UNRATED")])
    assert not supports_fact([_outlet("naturalnews.com", "CONTESTED"), _outlet("resistthemainstream.com", "CONTESTED")])


def test_an_include_on_unrated_sources_only_is_watched_and_evidence_is_set_by_rule():
    from grit.agents.weekly import check_selections

    chain = {"fact": "f", "exposure": "e", "transmission": "t", "impact": "i", "exposure_status": "POSSIBLE",
             "significance": {"exposure": 2, "consequence": 3, "immediacy": 3, "evidence": 3, "change": 2}}
    sources = {"W": {"outlets": [_outlet("houseofsaud.com", "UNRATED")]},
               "O": {"outlets": [_outlet("sa.usembassy.gov", "T1")]}}
    weak = check_selections({"selections": [{"signal_id": "W", "decision": "INCLUDE", "reason": "r", "source_ids": ["W"], **chain}]},
                            sources, {"W"})["selections"][0]
    assert weak["decision"] == "WATCH" and weak["decision_downgraded"] == "INCLUDE" and "verify" in weak["source_check"]
    assert weak["significance"]["evidence"] == 1
    strong = check_selections({"selections": [{"signal_id": "W", "decision": "INCLUDE", "reason": "r", "source_ids": ["W", "O"],
                                               **chain, "significance": {**chain["significance"], "evidence": 0}}]},
                              sources, {"W"})["selections"][0]
    assert strong["decision"] == "INCLUDE" and strong["significance"]["evidence"] == 3, "the rule raises evidence the model understated"


def test_other_reports_of_the_same_event_are_offered_best_band_first():
    from grit.pipeline.weekly import same_event

    sources = {
        "A": {"title": "US Orders Staff Out of Saudi Arabia as Embassy Operates on Emergency Staffing", "published_at": "2026-11-01", "source_band": "UNRATED"},
        "B": {"title": "Security Alert: U.S. Mission to Saudi Arabia - U.S. Embassy & Consulates", "published_at": "2026-11-02", "source_band": "T1"},
        "C": {"title": "State Dept denies wider Gulf evacuation, says order covers Saudi Arabia", "published_at": "2026-10-30", "source_band": "UNRATED"},
        "D": {"title": "Changi Airport to upgrade AI runway monitoring system", "published_at": "2026-10-30", "source_band": "T2"},
        "E": {"title": "Saudi Arabia hosts tourism summit", "published_at": "2026-09-01", "source_band": "T2"},
    }
    assert same_event("A", sources) == ["B", "C"]


def test_r_and_a_can_contest_an_outlet_and_a_rating_replaces_the_listing():
    from grit.ontology.source_ratings import confirm, contest, set_rating, uncontest

    document = {"ratings": [{"domain": "naturalnews.com", "rating": "LOW", "status": "PROPOSED"}], "contested": []}
    contest(document, "https://www.naturalnews.com/x", by="Jony Ling", reason="Unverified claims.", today=date(2026, 11, 4))
    assert document["ratings"] == [] and document["contested"][0]["domain"] == "naturalnews.com"
    with pytest.raises(ValueError, match="reason"):
        contest(document, "example.com", by="Jony Ling", reason=" ")
    document["contested"].append({"domain": "news-pravda.com", "status": "PROPOSED"})
    assert confirm(document, by="Jony Ling", today=date(2026, 11, 4)) == 1
    set_rating(document, "naturalnews.com", "low", by="Jony Ling", today=date(2026, 11, 4))
    assert [row["domain"] for row in document["contested"]] == ["news-pravda.com"]
    assert uncontest(document, "news-pravda.com") and document["contested"] == []


def test_unrated_outlets_are_listed_most_used_first(tmp_path):
    from grit.ontology.source_ratings import unrated_outlets
    from grit.pool import Pool

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN")
    for number, outlets in enumerate(([_outlet("bbc.com", "UNRATED"), _outlet("reuters.com", "T2")], [_outlet("bbc.com", "UNRATED")],
                                      [_outlet("gulfnews.com", "UNRATED")])):
        signal = pool.add_signal(url=f"https://news.example/{number}", title=str(number), text="t", publisher="p",
                                 published_at="2026-11-01", lane="TEST", content_hash=str(number))
        pool.save_assessment({"signal_id": signal, "run_id": "R", "pack": "urs", "decision": "KEEP", "outlets_json": outlets})
    assert unrated_outlets("R", pool) == [("bbc.com", 2), ("gulfnews.com", 1)]
