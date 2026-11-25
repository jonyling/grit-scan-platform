"""Phase 1 semantic reasoning: event_relations schema, candidates, apply, contracts, Critic, CONTRADICT seed."""

from __future__ import annotations

from pathlib import Path

import pytest

from grit.pool import Pool


def test_event_relations_table_is_append_only(tmp_path: Path):
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"event_relations": 1})
    left = pool.add_signal(url="https://a.example/1", title="A", text="t", publisher="a", published_at="2026-11-01",
                           lane="TEST", content_hash="1")
    right = pool.add_signal(url="https://b.example/2", title="B", text="t", publisher="b", published_at="2026-11-01",
                            lane="TEST", content_hash="2")
    row = pool.add_event_relation(
        relation_id="REL-R-x", run_id="R", left_signal_id=left, right_signal_id=right,
        label="SAME", confidence=0.9, reason="same story", input_hash="h1", status="VALIDATED",
    )
    assert row["left_signal_id"] < row["right_signal_id"]
    with pytest.raises(Exception, match="append-only"):
        with pool.transaction() as conn:
            conn.execute("UPDATE event_relations SET status = 'REJECTED' WHERE relation_row_id = ?", (row["relation_row_id"],))
    pool.add_event_relation(
        relation_id="REL-R-x", run_id="R", left_signal_id=left, right_signal_id=right,
        label="INSUFFICIENT", confidence=0.4, reason="split", input_hash="h1", status="SUPERSEDED",
    )
    latest = pool.event_relation_by_id("REL-R-x")
    assert latest["status"] == "SUPERSEDED" and latest["label"] == "INSUFFICIENT"


def test_candidate_edges_emit_near_miss_and_respect_caps():
    from grit.core.event_relations import candidate_edges, needs_llm, ordered_pair

    signals = [
        {"signal_id": "SIG-a", "title": "Houthi drones strike Saudi airports overnight", "published_at": "2026-11-01", "text": "a" * 80},
        {"signal_id": "SIG-b", "title": "Houthi missiles target Saudi airport facilities", "published_at": "2026-11-01", "text": "b" * 80},
        {"signal_id": "SIG-c", "title": "Changi Airport upgrades runway sensors quietly", "published_at": "2026-11-01", "text": "c" * 80},
    ]
    cosines = {ordered_pair("SIG-a", "SIG-b"): 0.85, ordered_pair("SIG-a", "SIG-c"): 0.50, ordered_pair("SIG-b", "SIG-c"): 0.40}
    edges = candidate_edges(signals, cosines=cosines, clusters={"SIG-a": "EVT-1", "SIG-b": "EVT-1"})
    pairs = {(e["left_signal_id"], e["right_signal_id"]) for e in edges}
    assert ("SIG-a", "SIG-b") in pairs
    near = next(e for e in edges if e["left_signal_id"] == "SIG-a" and e["right_signal_id"] == "SIG-b")
    assert "near_miss" in near["features"]["reasons"] or "cluster" in near["features"]["reasons"]
    assert needs_llm(near)
    auto = {"features": {"cosine": 0.92, "shared_terms": 2, "days_apart": 0}}
    assert not needs_llm(auto)
    capped = candidate_edges(signals, cosines=cosines, per_signal=1, budget=1)
    assert len(capped) <= 1


def test_apply_same_union_find_and_insufficient_noop():
    from grit.core.event_relations import applies_same, contradiction_pairs, same_groups, validated_same_pairs

    rows = [
        {"left_signal_id": "SIG-a", "right_signal_id": "SIG-b", "label": "SAME", "confidence": 0.86, "status": "VALIDATED", "evidence": {}},
        {"left_signal_id": "SIG-b", "right_signal_id": "SIG-c", "label": "SAME", "confidence": 0.9, "status": "VALIDATED", "evidence": {}},
        {"left_signal_id": "SIG-d", "right_signal_id": "SIG-e", "label": "SAME", "confidence": 0.5, "status": "VALIDATED", "evidence": {}},
        {"left_signal_id": "SIG-f", "right_signal_id": "SIG-g", "label": "INSUFFICIENT", "confidence": 0.4, "status": "VALIDATED", "evidence": {}},
        {"left_signal_id": "SIG-h", "right_signal_id": "SIG-i", "label": "CONTRADICTION", "confidence": 0.9, "status": "VALIDATED", "evidence": {}},
        {"left_signal_id": "SIG-j", "right_signal_id": "SIG-k", "label": "SAME", "confidence": 0.7, "status": "VALIDATED",
         "evidence": {"critic_upheld": True}},
    ]
    assert applies_same("SAME", 0.86)
    assert not applies_same("SAME", 0.5)
    assert applies_same("SAME", 0.7, critic_upheld=True)
    pairs = validated_same_pairs(rows)
    groups = same_groups(pairs)
    assert set(groups[min(groups)]) >= {"SIG-a", "SIG-b", "SIG-c"}
    assert ("SIG-j", "SIG-k") in pairs
    assert ("SIG-d", "SIG-e") not in pairs
    assert contradiction_pairs(rows) == [("SIG-h", "SIG-i")]


def test_contract_rejects_controlled_fields_and_unknown_edge():
    from grit.agents.relations import ContractError, check_relation_critique, check_relations

    with pytest.raises(ContractError, match="Unknown edge_id"):
        check_relations({"relations": [{"edge_id": "E9", "label": "SAME", "confidence": 0.9, "reason": "x" * 10}]}, {"E1"})
    with pytest.raises(ContractError, match="controlled"):
        check_relations({"relations": [{"edge_id": "E1", "label": "SAME", "confidence": 0.9, "reason": "same story here", "band": "Escalating"}]}, {"E1"})
    with pytest.raises(ContractError, match="Missing"):
        check_relations({"relations": []}, {"E1"})
    ok = check_relations({"relations": [{"edge_id": "E1", "label": "RELATED", "confidence": 0.6, "reason": "shared theme only"}]}, {"E1"})
    assert ok["relations"][0]["label"] == "RELATED"
    with pytest.raises(ContractError, match="UPHOLD"):
        check_relation_critique({"reviews": [{"edge_id": "E1", "action": "KEEP", "reason": "no"}]}, {"E1"})


def test_relation_contract_bounds_verbatim_quote_without_retrying_the_batch():
    from grit.agents.relations import check_relations
    from grit.core.event_relations import QUOTE_CHARS

    source_quote = "word " * 30
    raw = {"relations": [{
        "edge_id": "E1",
        "label": "SAME",
        "confidence": 0.9,
        "reason": "same source event",
        "quote": source_quote,
    }]}
    checked = check_relations(raw, {"E1"})
    quote = checked["relations"][0]["quote"]
    assert 0 < len(quote) <= QUOTE_CHARS
    assert quote == source_quote[:len(quote)]
    assert raw["relations"][0]["quote"] == quote


def test_critic_split_and_reclassify():
    from grit.agents.relations import apply_critic_reviews

    proposals = [
        {"edge_id": "E1", "label": "SAME", "confidence": 0.7, "reason": "maybe same", "evidence": {}},
        {"edge_id": "E2", "label": "RELATED", "confidence": 0.6, "reason": "linked", "evidence": {}},
    ]
    reviews = [
        {"edge_id": "E1", "action": "SPLIT", "reason": "different airports"},
        {"edge_id": "E2", "action": "RECLASSIFY", "label": "SAME", "confidence": 0.88, "reason": "same strike"},
    ]
    out = {row["edge_id"]: row for row in apply_critic_reviews(proposals, reviews)}
    assert out["E1"]["label"] == "INSUFFICIENT"
    assert out["E2"]["label"] == "SAME" and out["E2"]["confidence"] == 0.88


def test_same_dominates_label_merges_without_shared_words():
    from grit.pipeline.events import UP, label_merges

    def event(key, title, score, *, day="2026-11-01"):
        row = {"signal_id": key, "title": title, "source_band": "UNRATED", "tier": 3, "publisher": key}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": day, "last_date": day, "risks": ["R1"],
                "risk_label": "", "requests": []}

    events = [event("a", "Airline seeks protection", 12), event("b", "Carrier enters insolvency talks", 10)]
    rows = {"EVT-a": {"item": "Madrid carrier files for creditor protection"},
            "EVT-b": {"item": "Spanish airline begins lender negotiations"}}
    assert label_merges(events, rows) == {}, "no shared distinctive words or aliases"
    assert label_merges(events, rows, same_pairs=[("a", "b")]) == {"EVT-b": "EVT-a"}


def test_group_unions_validated_same_relations(tmp_path: Path):
    from grit.pipeline.events import _group

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"event_relations": 1})
    first = pool.add_signal(url="https://a.example/1", title="Airline seeks creditor protection in Madrid",
                            text="t", publisher="a", published_at="2026-11-01", lane="TEST", content_hash="1")
    second = pool.add_signal(url="https://b.example/2", title="Spanish carrier begins insolvency talks with lenders",
                             text="t", publisher="b", published_at="2026-11-01", lane="TEST", content_hash="2")
    pool.add_event_relation(
        relation_id="REL-1", run_id="R", left_signal_id=first, right_signal_id=second,
        label="SAME", confidence=0.9, reason="same insolvency", input_hash="h", status="VALIDATED",
    )
    rows = [
        {"signal_id": first, "title": "Airline seeks creditor protection in Madrid"},
        {"signal_id": second, "title": "Spanish carrier begins insolvency talks with lenders"},
    ]
    grouped = _group(pool, rows, run_id="R")
    assert len(grouped) == 1
    members = next(iter(grouped.values()))
    assert {row["signal_id"] for row in members} == {first, second}


def test_related_prefers_validated_relation_without_shared_words(tmp_path: Path):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore, same_event

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "event_relations": 1})
    first = pool.add_signal(url="https://a.example/1", title="Airline seeks creditor protection in Madrid",
                            text="t", publisher="a", published_at="2026-11-01", lane="TEST", content_hash="1")
    second = pool.add_signal(url="https://b.example/2", title="Spanish carrier begins insolvency talks with lenders",
                             text="t", publisher="b", published_at="2026-11-01", lane="TEST", content_hash="2")
    third = pool.add_signal(url="https://c.example/3", title="Changi Airport upgrades runway sensors",
                            text="t", publisher="c", published_at="2026-11-01", lane="TEST", content_hash="3")
    for signal_id in (first, second, third):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})
    pool.add_event_relation(
        relation_id="REL-1", run_id="R", left_signal_id=first, right_signal_id=second,
        label="SAME", confidence=0.91, reason="paraphrase of one filing", input_hash="h", status="VALIDATED",
    )
    sources = {
        first: {"title": "Airline seeks creditor protection in Madrid", "published_at": "2026-11-01", "source_band": "UNRATED"},
        second: {"title": "Spanish carrier begins insolvency talks with lenders", "published_at": "2026-11-01", "source_band": "T1"},
        third: {"title": "Changi Airport upgrades runway sensors", "published_at": "2026-11-01", "source_band": "T2"},
    }
    assert same_event(first, sources) == []
    peers = {first: [second], second: [first], third: []}
    assert same_event(first, sources, relation_peers=peers) == [second]

    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *a, **k: {})
    related = coordinator.related([first], sources)
    assert second in related and third not in related


def test_resolve_event_relations_auto_same_and_critic(tmp_path: Path):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "event_relations": 1, "window_days": 7})
    first = pool.add_signal(url="https://a.example/1", title="Houthi drones strike Saudi airports overnight",
                            text="drones hit airports in Saudi Arabia during overnight raids", publisher="a",
                            published_at="2026-11-01", lane="TEST", content_hash="1")
    second = pool.add_signal(url="https://b.example/2", title="Houthi drones strike Saudi airports overnight update",
                             text="drones hit airports in Saudi Arabia during overnight raids again", publisher="b",
                             published_at="2026-11-01", lane="TEST", content_hash="2")
    for signal_id in (first, second):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})

    def call(client, *, model, system, payload, **kwargs):
        # Without vectors, word_rule still emits a candidate that needs the LLM.
        candidates = payload.get("candidates") or []
        if payload.get("contract") == "event-relation-1" and "candidates" in payload and "proposal" not in (candidates[0] if candidates else {}):
            return {"relations": [
                {"edge_id": item["edge_id"], "label": "SAME", "confidence": 0.9, "reason": "same overnight strike wording"}
                for item in candidates
            ]}
        if "reviews" in str(system) or "ambiguous" in system.casefold() or "RelationCritic" in system or "UPHOLD" in system:
            return {"reviews": [
                {"edge_id": item["edge_id"], "action": "UPHOLD", "reason": "evidence supports one story"}
                for item in candidates
            ]}
        return {"relations": []}

    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=call)
    summary = coordinator.resolve_event_relations()
    assert summary["enabled"] == 1
    assert summary["candidates"] >= 1
    rows = pool.event_relations_for_run("R")
    assert rows
    assert any(row["label"] in {"SAME", "RELATED", "UPDATE", "INSUFFICIENT", "CONTRADICTION"} for row in rows)


def test_contradict_seeded_from_contradiction_relation(tmp_path: Path):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "event_relations": 1, "window_days": 7})
    first = pool.add_signal(url="https://a.example/1", title="Port strike suspends container movements",
                            text="dock workers strike at the main container terminal", publisher="a",
                            published_at="2026-11-01", lane="TEST", content_hash="1")
    second = pool.add_signal(url="https://b.example/2", title="Port operators say strike never started",
                             text="operators deny any strike at the terminal", publisher="b",
                             published_at="2026-11-01", lane="TEST", content_hash="2")
    for signal_id, decision in ((first, "KEEP"), (second, "KEEP")):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": decision, "tier": 1})
    pool.add_event_relation(
        relation_id="REL-c", run_id="R", left_signal_id=first, right_signal_id=second,
        label="CONTRADICTION", confidence=0.9, reason="strike vs denial", input_hash="hc", status="VALIDATED",
    )

    searched: list[dict] = []

    def call(client, *, model, system, payload, **kwargs):
        if "Find missing coverage" in system or "CONTRADICT" in system:
            return {"queries": [], "missing_checks": [], "verify_source_ids": [], "history_queries": []}
        return {"queries": [], "missing_checks": [], "verify_source_ids": [], "history_queries": []}

    def search(query: str) -> dict:
        searched.append({"query": query})
        return {"status": "SUCCEEDED", "candidates": []}

    store = WeeklyStore(pool, "R", search_limit=5, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=call)
    coordinator.research("initial-counterevidence", search, reassess=lambda: None)
    assert searched, "CONTRADICTION relation should seed a CONTRADICT search"
    assert any("denied" in item["query"].casefold() or "false" in item["query"].casefold() for item in searched)


def test_feature_flag_disables_resolver(tmp_path: Path):
    from grit.core.event_relations import enabled
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore

    assert enabled({"event_relations": 1})
    assert enabled({"event_relations": "true"})
    assert not enabled({})
    assert not enabled({"event_relations": 0})

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"weekly_mode": "reasoned"})
    pool.add_signal(url="https://a.example/1", title="A story about airports under attack today",
                    text="t", publisher="a", published_at="2026-11-01", lane="TEST", content_hash="1")
    pool.add_signal(url="https://b.example/2", title="A story about airports under attack tonight",
                    text="t", publisher="b", published_at="2026-11-01", lane="TEST", content_hash="2")
    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *a, **k: {})
    assert coordinator.resolve_event_relations()["enabled"] == 0
    assert pool.event_relations_for_run("R") == []


def test_override_band_untouched_by_same_merge(tmp_path: Path):
    """Governance: SAME merge must not rewrite override/band fields on assessments."""
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"event_relations": 1})
    signal = pool.add_signal(url="https://a.example/1", title="Staff threat at named site",
                             text="t", publisher="a", published_at="2026-11-01", lane="TEST", content_hash="1")
    other = pool.add_signal(url="https://b.example/2", title="Named site reports staff threat update",
                            text="t", publisher="b", published_at="2026-11-01", lane="TEST", content_hash="2")
    pool.save_assessment({
        "signal_id": signal, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 1,
        "priority_json": {"band": "Escalating", "override_rule_id": "OVERRIDE_STAFF_THREAT", "override_reason": "staff threat"},
    })
    pool.save_assessment({"signal_id": other, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})
    pool.add_event_relation(
        relation_id="REL-o", run_id="R", left_signal_id=signal, right_signal_id=other,
        label="SAME", confidence=0.95, reason="same staff threat", input_hash="ho", status="VALIDATED",
    )
    by_id = {row["signal_id"]: row for row in pool.assessments("R")}
    assert (by_id[signal].get("priority") or {}).get("override_rule_id") == "OVERRIDE_STAFF_THREAT"
    relation = pool.event_relations_for_run("R", validated_only=True)[0]
    assert relation["label"] == "SAME"
    assert "band" not in (relation.get("evidence") or {})
    assert "override_rule_id" not in (relation.get("evidence") or {})
    # Relation ledger did not clear the assessment override.
    assert (by_id[signal].get("priority") or {}).get("band") == "Escalating"
