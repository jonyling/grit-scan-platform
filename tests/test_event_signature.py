"""Phase P1 event signatures: contract, fallback, deterministic compare, cache, near-miss."""

from __future__ import annotations

from pathlib import Path

from grit.pool import Pool


def _sig(**kwargs):
    from grit.core.event_signature import empty_signature

    base = empty_signature()
    base.update(kwargs)
    return base


def test_location_only_never_same_event_candidate():
    from grit.core.event_signature import same_event_candidate

    left = _sig(actor=["OFAC"], action_class="sanction_designation", location=["United States"],
                date_window=["2026-11-20", "2026-11-20"], object=["shipping network"])
    right = _sig(actor=["Taiwan"], action_class="defence_funding", location=["United States"],
                 date_window=["2026-11-20", "2026-11-20"], object=["defence bill"])
    assert not same_event_candidate(left, right)


def test_actor_action_window_true():
    from grit.core.event_signature import same_event_candidate

    left = _sig(actor=["Houthi"], action_class="armed_attack", location=["Saudi Arabia"],
                date_window=["2026-11-24", "2026-11-25"], object=["airports"])
    right = _sig(actor=["Houthi rebels"], action_class="armed_attack", location=["Saudi Arabia"],
                 date_window=["2026-11-26", "2026-11-26"], object=["airport facilities"])
    assert same_event_candidate(left, right)


def test_object_action_true_without_actor():
    from grit.core.event_signature import same_event_candidate

    left = _sig(actor=[], action_class="export_control", location=["China"],
                date_window=["2026-11-01", "2026-11-01"], object=["Megaspeed", "entity list"])
    right = _sig(actor=["BIS"], action_class="export_control", location=["United States"],
                 date_window=["2026-01-01", "2026-01-01"], object=["Megaspeed"])
    assert same_event_candidate(left, right)


def test_actor_only_or_consequence_only_insufficient():
    from grit.core.event_signature import same_event_candidate

    actor_only = (
        _sig(actor=["Saudi Arabia"], action_class="threat_warning", consequence_class="operational_disruption",
             date_window=["2026-11-01", "2026-11-01"], object=["staff"]),
        _sig(actor=["Saudi Arabia"], action_class="investment_announcement", consequence_class="operational_disruption",
             date_window=["2026-11-01", "2026-11-01"], object=["refinery"]),
    )
    assert not same_event_candidate(*actor_only)
    consequence_only = (
        _sig(actor=["A"], action_class="armed_attack", consequence_class="physical_harm",
             date_window=["2026-11-01", "2026-11-01"], object=["port"]),
        _sig(actor=["B"], action_class="cyber_incident", consequence_class="physical_harm",
             date_window=["2026-11-01", "2026-11-01"], object=["grid"]),
    )
    assert not same_event_candidate(*consequence_only)


def test_fallback_marked_on_model_failure():
    from grit.agents.signature import extract_signature
    from grit.core.event_signature import SIGNATURE_SOURCE_FALLBACK

    def boom(*_a, **_k):
        raise RuntimeError("model down")

    out = extract_signature(
        [{"signal_id": "S1", "title": "OFAC sanctions shipping network", "published_at": "2026-11-25"}],
        invoke=boom,
    )
    assert out["signature_source"] == SIGNATURE_SOURCE_FALLBACK
    assert out["action_class"] == "sanction_designation"
    assert out.get("input_hash")


def test_cache_hit_on_unchanged_anchor(tmp_path: Path):
    from grit.core.event_signature import SIGNATURE_SOURCE_CACHE, SIGNATURE_SOURCE_LLM, signature_input_hash
    from grit.pipeline.resolve_signatures import attach_event_signatures

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={})
    anchors = [{"signal_id": "a0", "title": "Houthi drone strikes targeting Saudi airports",
                "published_at": "2026-11-01", "source_band": "UNRATED", "tier": 3, "publisher": "a0"}]
    event = {
        "event_id": "EVT-a", "elevated": True, "score": 10, "band": "▲", "new": True, "tier": 3,
        "red": False, "red_candidate": False, "lead": anchors[0], "members": anchors, "reports": anchors,
        "signal_ids": ["a0"], "publishers": 1, "first_date": "2026-11-01", "last_date": "2026-11-01",
        "risks": [], "risk_label": "", "requests": [],
    }
    digest = signature_input_hash(anchors)
    pool.add_event_signature(
        input_hash=digest,
        signature={"actor": ["Houthi"], "action_class": "armed_attack", "action_text": "drone strikes",
                   "location": ["Saudi Arabia"], "date_window": ["2026-11-01", "2026-11-01"],
                   "consequence_class": "operational_disruption", "object": ["airports"]},
        signature_source=SIGNATURE_SOURCE_LLM,
        event_id="EVT-a", run_id="R", model="test-model", prompt_version="event-signature-1",
    )
    calls = {"n": 0}

    def invoke(*_a, **_k):
        calls["n"] += 1
        raise AssertionError("should not call model on cache hit")

    result = attach_event_signatures([event], pool=pool, run_id="R", invoke=invoke)
    assert result["stats"]["cache_hits"] == 1
    assert calls["n"] == 0
    assert result["events"][0]["signature"]["signature_source"] == SIGNATURE_SOURCE_CACHE
    assert result["events"][0]["signature"]["action_class"] == "armed_attack"


def test_near_miss_exactly_one_field_short():
    from grit.core.event_signature import same_event_candidate, signature_near_miss

    left = _sig(actor=["Houthi"], action_class="armed_attack", location=["Saudi Arabia"],
                date_window=["2026-11-01", "2026-11-01"], object=["airports"])
    # Same actor + window, different action → one field short on actor+action+window path.
    right = _sig(actor=["Houthi"], action_class="threat_warning", location=["Saudi Arabia"],
                 date_window=["2026-11-01", "2026-11-01"], object=["travel"])
    assert not same_event_candidate(left, right)
    miss = signature_near_miss(left, right)
    assert miss is not None
    assert miss["missing_fields"] == ["action_class"]
    assert miss["near_miss"] is True


def test_ofac_taiwan_chip_stay_separate_on_fallback_merge():
    from grit.pipeline.events import UP, _merge_stories

    def event(key, title, score):
        row = {"signal_id": key, "title": title, "source_band": "UNRATED", "tier": 3, "publisher": key,
               "published_at": "2026-11-25"}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": "2026-11-25", "last_date": "2026-11-25",
                "risks": [], "risk_label": "", "requests": []}

    events = [
        event("ofac", "Notice of OFAC Sanctions Action against shipping network", 12),
        event("tw", "Taiwan defence funding bill advances in legislature", 11),
        event("chip", "US chip export controls tighten on Megaspeed entity list", 10),
        event("h1", "Houthi drone strikes targeting Saudi airports", 13),
        event("h2", "Houthi missile attacks on Saudi airport facilities", 12),
    ]
    merged = _merge_stories(events, {}, relations_on=False)
    by_member = {}
    for item in merged:
        for sid in item["signal_ids"]:
            by_member[sid] = item["event_id"]
    assert by_member["ofac"] != by_member["tw"]
    assert by_member["ofac"] != by_member["chip"]
    assert by_member["tw"] != by_member["chip"]
    assert by_member["h1"] == by_member["h2"]


def test_signature_nominates_when_relations_on():
    from grit.pipeline.events import UP, _merge_stories

    def event(key, title, score):
        row = {"signal_id": key, "title": title, "source_band": "UNRATED", "tier": 3, "publisher": key,
               "published_at": "2026-11-25"}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": "2026-11-25", "last_date": "2026-11-25",
                "risks": [], "risk_label": "", "requests": []}

    nominations: list[tuple[str, str]] = []
    events = [
        event("h1", "Houthi drone strikes targeting Saudi airports", 13),
        event("h2", "Houthi missile attacks on Saudi airport facilities", 12),
    ]
    merged = _merge_stories(events, {}, relations_on=True, nominations=nominations)
    assert sorted(sorted(item["signal_ids"]) for item in merged) == [["h1"], ["h2"]]
    assert ("h1", "h2") in nominations or ("h2", "h1") in nominations


def test_check_signature_rejects_invented_class():
    from grit.agents.signature import check_signature
    from grit.agents.weekly import ContractError
    import pytest

    with pytest.raises(ContractError):
        check_signature({
            "actor": ["X"], "action_class": "invented_class", "action_text": "did thing",
            "location": [], "date_window": [None, None], "consequence_class": "none_stated", "object": [],
        })


def test_relation_learning_includes_signature_proxies(tmp_path: Path):
    from grit.core.relation_learning import format_learning_report, learning_report

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"signature_stats": {"calls": 2, "cache_hits": 1, "fallback": 1, "llm": 1, "events": 3,
                                               "cache_hit_rate": 0.3333},
                           "signature_near_misses": [
                               {"missing_fields": ["action_class"], "actor_overlap": ["Houthi"],
                                "object_overlap": [], "location_overlap": ["Saudi Arabia"],
                                "left_event_id": "A", "right_event_id": "B"}]})
    report = learning_report(pool, run_id="R")
    assert "signatures" in report
    assert report["signatures"]["near_misses"]["count"] == 1
    text = format_learning_report(report)
    assert "Signatures:" in text and "near-misses" in text.lower() or "Near-misses" in text or "near-misses" in text
