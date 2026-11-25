"""Phase 2 semantic reasoning: nightly resolve, input_hash reuse, STE fields, relation learning."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from grit.pool import Pool

DAY = date(2026, 11, 2)


def _keep(pool: Pool, run_id: str, pack: str, *, url: str, title: str, text: str, day: str, hash_: str, tier: int = 2) -> str:
    signal = pool.add_signal(
        url=url, title=title, text=text, publisher="test", published_at=f"{day}T00:00:00Z",
        lane="TEST", content_hash=hash_,
    )
    pool.save_assessment({"signal_id": signal, "run_id": run_id, "pack": pack, "decision": "KEEP", "tier": tier})
    return signal


def test_nightly_resolve_persists_auto_same_and_weekly_reuses_by_hash(tmp_path: Path):
    from grit.core.event_relations import input_hash_for
    from grit.daily.index import cluster_events, resolve_nightly_relations
    from grit.pipeline.resolve_relations import pair_input_hash
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-11-02-urs", run_date="2026-11-02", pack="urs", kind="DAILY", config={})
    first = _keep(
        pool, "2026-11-02-urs", "urs",
        url="https://a.example/1",
        title="Houthi drones strike Saudi airports overnight",
        text="drones hit airports in Saudi Arabia during overnight raids",
        day="2026-11-01", hash_="1",
    )
    second = _keep(
        pool, "2026-11-02-urs", "urs",
        url="https://b.example/2",
        title="Houthi drones strike Saudi airports overnight update",
        text="drones hit airports in Saudi Arabia during overnight raids again",
        day="2026-11-01", hash_="2",
    )
    # High-precision vectors so nightly auto-SAME can fire without LLM.
    pool.put_vector(first, model="gemini-embedding-001", task="RETRIEVAL_DOCUMENT", dimensions=768,
                    text_hash="1", vector=[1.0, 0.0] + [0.0] * 766)
    pool.put_vector(second, model="gemini-embedding-001", task="RETRIEVAL_DOCUMENT", dimensions=768,
                    text_hash="2", vector=[1.0, 0.0] + [0.0] * 766)
    cluster_events(pool)
    nightly = resolve_nightly_relations(pool, DAY)
    assert nightly["enabled"] == 1
    assert nightly["auto_same"] >= 1 or nightly["validated"] >= 1
    index_rows = pool.event_relations_for_run(f"{DAY.isoformat()}-index", validated_only=True)
    assert index_rows, "nightly should persist at least one validated relation"

    # Weekly run with flag on: same pair should reuse by input_hash (no LLM call).
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "event_relations": 1, "window_days": 7})
    for signal_id in (first, second):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})

    called: list[str] = []

    def call(client, *, model, system, payload, **kwargs):
        called.append(system[:40])
        return {"relations": []}

    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=call)
    summary = coordinator.resolve_event_relations()
    assert summary["enabled"] == 1
    assert summary["reused"] >= 1, "weekly should skip re-resolve via input_hash from nightly"
    assert not called, "reused edges must not invoke EventRelation"
    weekly_rows = pool.event_relations_for_run("R", validated_only=True)
    assert any((row.get("evidence") or {}).get("reused") for row in weekly_rows)


def test_cross_run_relation_peers_without_restamp(tmp_path: Path):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore, same_event

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("NIGHT", run_date="2026-11-01", pack="_index", kind="INDEX", config={"event_relations": 1})
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "event_relations": 1})
    first = pool.add_signal(url="https://a.example/1", title="Airline seeks creditor protection in Madrid",
                            text="t", publisher="a", published_at="2026-11-01", lane="TEST", content_hash="1")
    second = pool.add_signal(url="https://b.example/2", title="Spanish carrier begins insolvency talks with lenders",
                             text="t", publisher="b", published_at="2026-11-01", lane="TEST", content_hash="2")
    for signal_id in (first, second):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})
    pool.add_event_relation(
        relation_id="REL-night", run_id="NIGHT", left_signal_id=first, right_signal_id=second,
        label="SAME", confidence=0.92, reason="nightly same insolvency", input_hash="hn", status="VALIDATED",
    )
    sources = {
        first: {"title": "Airline seeks creditor protection in Madrid", "published_at": "2026-11-01"},
        second: {"title": "Spanish carrier begins insolvency talks with lenders", "published_at": "2026-11-01"},
    }
    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *a, **k: {})
    peers = coordinator.relation_peers(sources)
    assert second in peers[first] and first in peers[second]
    assert same_event(first, sources, relation_peers=peers) == [second]


def test_batch_hash_lookup_scales(tmp_path: Path):
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"event_relations": 1})
    left = pool.add_signal(url="https://a.example/1", title="A", text="t", publisher="a",
                           published_at="2026-11-01", lane="TEST", content_hash="1")
    right = pool.add_signal(url="https://b.example/2", title="B", text="t", publisher="b",
                            published_at="2026-11-01", lane="TEST", content_hash="2")
    pool.add_event_relation(
        relation_id="REL-1", run_id="R", left_signal_id=left, right_signal_id=right,
        label="RELATED", confidence=0.7, reason="theme link", input_hash="hash-a", status="VALIDATED",
    )
    found = pool.event_relations_by_hashes(["hash-a", "hash-missing", "hash-a"])
    assert set(found) == {"hash-a"}
    assert found["hash-a"]["label"] == "RELATED"


def test_ste_optional_fields_validated_and_disclosed(tmp_path: Path):
    from grit.agents.weekly import COUNTERARGUMENT_CHARS, ContractError, check_selections, ste_fields_of

    chain = {
        "fact": "Carrier filed for protection", "exposure": "URS aviation customer",
        "transmission": "Maintenance contract delay", "impact": "not established",
        "exposure_status": "POSSIBLE", "impact_status": "NOT_ESTABLISHED",
        "significance": {"exposure": 2, "consequence": 2, "immediacy": 1, "evidence": 2, "change": 1},
        "immediate_threat": False,
        "reach_channel": "entity", "urs_reach_reason": "Named airline is a URS maintenance customer",
    }
    sources = {"A": {"signal_id": "A", "condition_ids": ["URS-KEY-01"], "evidence_confidence": "MEDIUM",
                     "outlets": [{"outlet": "reuters.com", "band": "T2"}]}}
    row = {
        "signal_id": "A", "decision": "INCLUDE", "reason": "Material insolvency risk",
        "source_ids": ["A"], **chain,
        "ste_anchor": "URS-KEY-01",
        "counterargument": "Filing may be a tactical pause rather than a collapse.",
        "missing_check": "Confirm whether the Madrid court accepted the petition.",
    }
    checked = check_selections({"selections": [row]}, sources, {"A"}, register={"URS-KEY-01"})["selections"][0]
    assert checked["ste_anchor"] == "URS-KEY-01"
    assert "tactical pause" in checked["counterargument"]
    assert "Madrid court" in checked["missing_check"]
    assert ste_fields_of({"editorial": checked})["ste_anchor"] == "URS-KEY-01"
    with pytest.raises(ContractError, match="ste_anchor"):
        check_selections(
            {"selections": [{**row, "ste_anchor": "x" * (200)}]}, sources, {"A"}, register={"URS-KEY-01"},
        )
    with pytest.raises(ContractError, match="counterargument"):
        check_selections(
            {"selections": [{**row, "counterargument": "x" * (COUNTERARGUMENT_CHARS + 1)}]},
            sources, {"A"}, register={"URS-KEY-01"},
        )


def test_format_v2_and_scorecard_surface_ste_fields(tmp_path: Path):
    from grit.ontology.pack import load_ontology_pack
    from grit.pipeline.events import event_register
    from grit.report import format_v2
    from grit.report.scorecard import scorecard
    from grit.settings import CONFIG_DIR

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-10-29-urs", run_date="2026-10-29", pack="urs", kind="READER_SCAN", config={})
    signal = pool.add_signal(
        url="https://a.example/jeddah", title="Drones strike Jeddah port", text="port hit",
        publisher="a", published_at="2026-10-28T00:00:00Z", lane="TEST", content_hash="j",
    )
    pool.save_assessment({
        "signal_id": signal, "run_id": "2026-10-29-urs", "pack": "urs", "decision": "KEEP", "tier": 1,
    })
    pool.finish_run("2026-10-29-urs")
    events = event_register(pool, "2026-10-29-urs", names={})
    event_id = events[0]["event_id"]
    pool.update_run_config(
        "2026-10-29-urs",
        event_rows={
            event_id: {
                "item": "Drones strike Jeddah port",
                "ste_anchor": "URS-KEY-03",
                "counterargument": "Strike may have been a false claim by a local outlet.",
                "missing_check": "Corroborate with a T1 official bulletin.",
                "response": {"owner": "Security"},
            }
        },
    )
    routed = [{**row, "shown": True, "route": {"priority": None},
               "editorial": {
                   "decision": "INCLUDE", "ste_anchor": "URS-KEY-03",
                   "counterargument": "Strike may have been a false claim by a local outlet.",
                   "missing_check": "Corroborate with a T1 official bulletin.",
               }} for row in pool.weekly_assessments("2026-10-29-urs")]
    v2 = format_v2.build(
        pool, pool.run("2026-10-29-urs"), load_ontology_pack(CONFIG_DIR, "urs"),
        routed, [], {}, [],
    )
    assert v2["escalating"]
    row = v2["escalating"][0]
    assert row.get("ste_anchor") == "URS-KEY-03"
    assert row.get("counterargument")
    assert row.get("missing_check")
    card = scorecard({"v2": v2, "evidence_ids": [signal], "method": {"weekly": {"selections": []}}})
    assert card["metrics"]["ste_anchor_disclosed"] >= 1
    assert card["metrics"]["counterargument_disclosed"] >= 1
    assert card["metrics"]["missing_check_disclosed"] >= 1


def test_relation_learning_false_and_missed_merge_proxies(tmp_path: Path):
    from grit.core.relation_learning import format_learning_report, learning_report

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"event_relations": 1})
    a = pool.add_signal(url="https://a.example/1", title="A", text="t", publisher="a",
                        published_at="2026-11-01", lane="TEST", content_hash="1")
    b = pool.add_signal(url="https://b.example/2", title="B", text="t", publisher="b",
                        published_at="2026-11-01", lane="TEST", content_hash="2")
    c = pool.add_signal(url="https://c.example/3", title="C", text="t", publisher="c",
                        published_at="2026-11-01", lane="TEST", content_hash="3")
    d = pool.add_signal(url="https://d.example/4", title="D", text="t", publisher="d",
                        published_at="2026-11-01", lane="TEST", content_hash="4")
    pool.add_event_relation(
        relation_id="REL-ok", run_id="R", left_signal_id=a, right_signal_id=b,
        label="SAME", confidence=0.9, reason="same", input_hash="h1", status="VALIDATED",
        evidence={"auto_same": True},
    )
    pool.add_event_relation(
        relation_id="REL-false", run_id="R", left_signal_id=a, right_signal_id=c,
        label="INSUFFICIENT", confidence=0.4, reason="split", input_hash="h2", status="VALIDATED",
        evidence={"critic_action": "SPLIT"},
    )
    pool.add_event_relation(
        relation_id="REL-miss", run_id="R", left_signal_id=c, right_signal_id=d,
        label="SAME", confidence=0.88, reason="reclassified", input_hash="h3", status="VALIDATED",
        evidence={"critic_action": "RECLASSIFY", "critic_upheld": True},
    )
    report = learning_report(pool, run_id="R")
    assert report["apply"] is False
    assert report["outcomes"]["false_merge"]["count"] >= 1
    assert report["outcomes"]["missed_merge"]["count"] >= 1
    assert report["outcomes"]["applied_same"] >= 1
    text = format_learning_report(report)
    assert "False-merge" in text and "Missed-merge" in text


def test_governance_untouched_by_p2_fields():
    from grit.agents.weekly import CONTROLLED
    from grit.core.event_relations import enabled

    assert "band" in CONTROLLED and "override_rule_id" in CONTROLLED
    assert "ste_anchor" not in CONTROLLED
    assert enabled({"event_relations": 1})
    assert not enabled({})
