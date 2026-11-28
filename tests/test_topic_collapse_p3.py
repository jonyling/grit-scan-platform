"""Phase P3: topic render + continuity (format_v2, emerging themes, nightly reuse-only)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from grit.pool import Pool


def test_status_label_and_week_number():
    from grit.core.topics import format_status_vs_last_week, topic_week_number, worst_reader_band

    assert format_status_vs_last_week("ESCALATING", week=3) == "Week 3, escalating"
    assert format_status_vs_last_week("NEW", week=1) == "Week 1, new"
    assert topic_week_number(topic_key="me-security-2026-11-04", run_date="2026-11-25") == 4
    assert topic_week_number(topic_key="x", prior_run_dates=["2026-11-11", "2026-11-18"]) == 3
    assert worst_reader_band(["Watch", "Escalating", "Elevated"]) == "Escalating"


def test_combined_implication_prefers_drafted_children():
    from grit.core.topics import combined_implication

    topic = {"common_ste_path": "STE path", "basis": "shared mechanism"}
    children = [
        {"row": {"implications": {"plain": "Staff in Riyadh could face disruption."}}},
        {"row": {"implications": "Port ops may slow."}},
    ]
    text = combined_implication(topic, children)
    assert "Riyadh" in text and "Port" in text
    assert combined_implication(topic, []) == "STE path"


def test_format_v2_topic_parent_children_unassigned_and_themes(tmp_path: Path):
    from grit.ontology.pack import load_ontology_pack
    from grit.pipeline.events import event_register
    from grit.report import format_v2
    from grit.settings import CONFIG_DIR

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run(
        "2026-11-25-urs",
        run_date="2026-11-25",
        pack="urs",
        kind="READER_SCAN",
        config={"topic_collapse": 1},
    )
    a = pool.add_signal(
        url="https://reuters.com/houthi-riyadh", title="Houthi missiles hit Riyadh airport",
        text="Houthi forces fired missiles at Riyadh airport.", publisher="reuters.com",
        published_at="2026-11-24T00:00:00Z", lane="TEST", content_hash="a",
    )
    b = pool.add_signal(
        url="https://reuters.com/houthi-jeddah", title="Houthi drones strike Jeddah port",
        text="Houthi drones struck Jeddah port facilities.", publisher="reuters.com",
        published_at="2026-11-24T06:00:00Z", lane="TEST", content_hash="b",
    )
    c = pool.add_signal(
        url="https://reuters.com/ofac", title="OFAC adds Gulf shipping firms to SDN list",
        text="OFAC designated Gulf shipping firms under new sanctions.", publisher="reuters.com",
        published_at="2026-11-23T00:00:00Z", lane="TEST", content_hash="c",
    )
    near = pool.add_signal(
        url="https://reuters.com/cyber-ot", title="OT malware probes Gulf industrial sites",
        text="Researchers saw OT malware probes at Gulf industrial control sites.", publisher="reuters.com",
        published_at="2026-11-22T00:00:00Z", lane="TEST", content_hash="n",
    )
    for sid in (a, b, c):
        pool.save_assessment({
            "signal_id": sid, "run_id": "2026-11-25-urs", "pack": "urs", "decision": "KEEP", "tier": 1,
        })
    # Near-threshold / non-elevated — theme candidate only.
    pool.save_assessment({
        "signal_id": near, "run_id": "2026-11-25-urs", "pack": "urs", "decision": "KEEP", "tier": 3,
    })
    pool.finish_run("2026-11-25-urs")

    events = event_register(pool, "2026-11-25-urs", names={})
    by_title = {event["lead"]["title"]: event for event in events}
    eid_a = by_title["Houthi missiles hit Riyadh airport"]["event_id"]
    eid_b = by_title["Houthi drones strike Jeddah port"]["event_id"]
    eid_c = by_title["OFAC adds Gulf shipping firms to SDN list"]["event_id"]
    eid_n = by_title["OT malware probes Gulf industrial sites"]["event_id"]

    pool.add_topic_resolution(
        topic_key="middle-east-security-escalation-2026-11-11",
        run_id="2026-11-25-urs",
        label="Middle East security escalation",
        event_ids=[eid_a, eid_b],
        basis="Shared Houthi attack mechanism and STE Gulf exposure",
        common_ste_path="staff and sites in Gulf",
        differences="Airport vs port target",
        status_vs_last_week="ESCALATING",
        confidence=0.91,
        status="VALIDATED",
        input_hash="topic-h1",
    )
    pool.add_topic_resolution(
        topic_key=f"unassigned-{eid_c}",
        run_id="2026-11-25-urs",
        label="Unassigned elevated",
        event_ids=[eid_c],
        basis="Unassigned",
        common_ste_path="",
        differences="",
        status_vs_last_week="NEW",
        confidence=0.0,
        status="UNASSIGNED",
        input_hash="topic-h1",
    )
    pool.add_topic_resolution(
        topic_key="theme-h1-near",
        run_id="2026-11-25-urs",
        label="Emerging theme (not elevated)",
        event_ids=[eid_n],
        basis="Emerging OT malware pattern against industrial sites",
        common_ste_path="",
        differences="",
        status_vs_last_week="NEW",
        confidence=0.88,
        status="THEME_CANDIDATE",
        input_hash="topic-h1",
    )
    pool.update_run_config(
        "2026-11-25-urs",
        event_rows={
            eid_a: {
                "item": "Houthi missiles hit Riyadh airport",
                "implications": {"plain": "Riyadh staff could face disruption."},
                "response": {"owner": "Security", "action": "Check staff"},
                "source_ids": [a],
            },
            eid_b: {
                "item": "Houthi drones strike Jeddah port",
                "implications": {"plain": "Jeddah logistics may slow."},
                "response": {"owner": "Ops", "action": "Watch port"},
                "source_ids": [b],
            },
            eid_c: {
                "item": "OFAC adds Gulf shipping firms to SDN list",
                "implications": {"plain": "Shipping counterparties may need screening."},
                "response": {"owner": "Compliance", "action": "Screen"},
                "source_ids": [c],
            },
        },
        emerging_themes=[{
            "event_ids": [eid_n], "basis": "Emerging OT malware pattern against industrial sites",
            "confidence": 0.88, "band_unchanged": True,
        }],
    )

    routed = [{**row, "shown": True, "route": {"priority": None}} for row in pool.weekly_assessments("2026-11-25-urs")]
    v2 = format_v2.build(
        pool, pool.run("2026-11-25-urs"), load_ontology_pack(CONFIG_DIR, "urs"), routed, [], {}, [],
    )

    kinds = [row.get("row_kind") for row in v2["escalating"]]
    assert "topic_parent" in kinds
    assert "topic_child" in kinds
    assert "event" in kinds

    parent = next(row for row in v2["escalating"] if row.get("row_kind") == "topic_parent")
    assert parent["item"] == "Middle East security escalation"
    assert parent["status_vs_last_week"].startswith("Week ")
    assert "escalating" in parent["status_vs_last_week"]
    assert parent["implications_text"]
    assert "Riyadh" in parent["implications_text"] or "Jeddah" in parent["implications_text"] or "Gulf" in parent["implications_text"]

    children = [row for row in v2["escalating"] if row.get("row_kind") == "topic_child"]
    assert len(children) == 2
    assert all(row["item"].startswith("    ") for row in children)
    assert all(row.get("confidence") == 0.91 for row in children)
    assert all(row.get("event_date") for row in children)

    unassigned = [row for row in v2["escalating"] if row.get("row_kind") == "event"]
    assert len(unassigned) == 1
    assert "OFAC" in unassigned[0]["item"]

    assert v2["emerging_themes"]
    assert v2["emerging_themes"][0]["band_unchanged"] is True
    assert eid_n in v2["emerging_themes"][0]["event_ids"]
    assert v2["topics"] and v2["topics"][0]["topic_key"].startswith("middle-east")


def test_format_v2_without_flag_is_unchanged_shape(tmp_path: Path):
    from grit.ontology.pack import load_ontology_pack
    from grit.report import format_v2
    from grit.settings import CONFIG_DIR

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("2026-11-25-urs", run_date="2026-11-25", pack="urs", kind="READER_SCAN", config={})
    sid = pool.add_signal(
        url="https://a.example/1", title="Drones strike Jeddah port", text="port hit",
        publisher="a", published_at="2026-11-24T00:00:00Z", lane="TEST", content_hash="j",
    )
    pool.save_assessment({"signal_id": sid, "run_id": "2026-11-25-urs", "pack": "urs", "decision": "KEEP", "tier": 1})
    pool.finish_run("2026-11-25-urs")
    from grit.pipeline.events import event_register

    events = event_register(pool, "2026-11-25-urs", names={})
    eid = events[0]["event_id"]
    pool.update_run_config("2026-11-25-urs", event_rows={eid: {"item": "Drones strike Jeddah port"}})
    routed = [{**row, "shown": True, "route": {"priority": None}} for row in pool.weekly_assessments("2026-11-25-urs")]
    v2 = format_v2.build(
        pool, pool.run("2026-11-25-urs"), load_ontology_pack(CONFIG_DIR, "urs"), routed, [], {}, [],
    )
    assert v2["topics"] == []
    assert v2["emerging_themes"] == []
    assert all((row.get("row_kind") or "event") == "event" for row in v2["escalating"])


def test_nightly_topic_reuse_attaches_matching_signature_only(tmp_path: Path):
    from grit.core.event_signature import same_event_candidate
    from grit.daily.index import resolve_nightly_topics
    from grit.pipeline.resolve_topics import topic_index_run_id

    pool = Pool(tmp_path / "grit.sqlite")
    day = date(2026, 11, 26)
    # Prior weekly topic with one child event.
    pool.start_run("W", run_date="2026-11-25", pack="urs", kind="READER_SCAN", config={"topic_collapse": 1})
    child = pool.add_signal(
        url="https://reuters.com/houthi-airport-1",
        title="Houthi missiles strike Riyadh airport runway",
        text="Houthi forces struck the Riyadh airport runway with ballistic missiles.",
        publisher="reuters.com", published_at="2026-11-24T00:00:00Z", lane="TEST", content_hash="c1",
    )
    pool.save_assessment({"signal_id": child, "run_id": "W", "pack": "urs", "decision": "KEEP", "tier": 1})
    pool.finish_run("W")

    # Build event membership for the child (as nightly cluster would).
    child_event = f"EVT-{child[4:]}"
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO events (event_id, lead_signal_id, first_date, last_date, members, created_at, updated_at) "
            "VALUES (?, ?, '2026-11-24', '2026-11-24', 1, ?, ?)",
            (child_event, child, "2026-11-25T00:00:00Z", "2026-11-25T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO event_members (event_id, signal_id, joined_at) VALUES (?, ?, ?)",
            (child_event, child, "2026-11-25T00:00:00Z"),
        )

    pool.add_topic_resolution(
        topic_key="middle-east-security-escalation-2026-11-11",
        run_id="W",
        label="Middle East security escalation",
        event_ids=[child_event],
        basis="Shared Houthi attack mechanism and STE Gulf exposure",
        common_ste_path="Gulf staff",
        differences="",
        status_vs_last_week="CONTINUING",
        confidence=0.9,
        status="VALIDATED",
        input_hash="prior",
    )

    # New matching event (same actor+action+object style) and an unrelated OFAC event.
    match = pool.add_signal(
        url="https://reuters.com/houthi-airport-2",
        title="Houthi missiles strike Riyadh airport terminal",
        text="Houthi forces struck the Riyadh airport terminal with ballistic missiles.",
        publisher="reuters.com", published_at="2026-11-26T00:00:00Z", lane="TEST", content_hash="m1",
    )
    other = pool.add_signal(
        url="https://reuters.com/ofac-2",
        title="OFAC adds chip exporters to entity list",
        text="OFAC designated chip exporters under export controls.",
        publisher="reuters.com", published_at="2026-11-26T01:00:00Z", lane="TEST", content_hash="o1",
    )
    pool.start_run("D", run_date="2026-11-26", pack="urs", kind="DAILY", config={})
    for sid in (match, other):
        pool.save_assessment({"signal_id": sid, "run_id": "D", "pack": "urs", "decision": "KEEP", "tier": 1})
    pool.finish_run("D")

    match_event = f"EVT-{match[4:]}"
    other_event = f"EVT-{other[4:]}"
    with pool.transaction() as conn:
        for eid, sid in ((match_event, match), (other_event, other)):
            conn.execute(
                "INSERT INTO events (event_id, lead_signal_id, first_date, last_date, members, created_at, updated_at) "
                "VALUES (?, ?, '2026-11-26', '2026-11-26', 1, ?, ?)",
                (eid, sid, "2026-11-26T00:00:00Z", "2026-11-26T00:00:00Z"),
            )
            conn.execute(
                "INSERT INTO event_members (event_id, signal_id, joined_at) VALUES (?, ?, ?)",
                (eid, sid, "2026-11-26T00:00:00Z"),
            )

    # Sanity: fallback signatures should match airport pair and not OFAC.
    from grit.core.event_signature import fallback_extract

    sig_child = fallback_extract([{"title": "Houthi missiles strike Riyadh airport runway",
                                   "text": "Houthi forces struck the Riyadh airport runway with ballistic missiles.",
                                   "published_at": "2026-11-24"}])
    sig_match = fallback_extract([{"title": "Houthi missiles strike Riyadh airport terminal",
                                   "text": "Houthi forces struck the Riyadh airport terminal with ballistic missiles.",
                                   "published_at": "2026-11-26"}])
    sig_other = fallback_extract([{"title": "OFAC adds chip exporters to entity list",
                                   "text": "OFAC designated chip exporters under export controls.",
                                   "published_at": "2026-11-26"}])
    assert same_event_candidate(sig_child, sig_match)
    assert not same_event_candidate(sig_child, sig_other)

    summary = resolve_nightly_topics(pool, day)
    assert summary["enabled"] == 1
    assert summary["attached"] >= 1

    index_id = topic_index_run_id(day, "urs")
    rows = pool.topic_resolutions_for_run(index_id, validated_only=True)
    assert rows, "nightly should persist a CONTINUING attachment on the pack index run"
    assert all(row["status_vs_last_week"] == "CONTINUING" for row in rows)
    assert all(row.get("model") == "nightly-reuse" for row in rows)
    attached_ids = {eid for row in rows for eid in (row.get("event_ids") or [])}
    assert match_event in attached_ids
    assert other_event not in attached_ids
    # No new topic keys minted overnight.
    assert all(row["topic_key"] == "middle-east-security-escalation-2026-11-11" for row in rows)
