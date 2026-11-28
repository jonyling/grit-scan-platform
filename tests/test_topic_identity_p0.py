"""Phase P0 identity guards: eligibility, nominate-not-merge, anchors, cluster guard, targeted internal."""

from __future__ import annotations

from pathlib import Path

from grit.pool import Pool


def test_internal_evidence_is_not_identity_eligible():
    from grit.core.identity import is_identity_eligible

    assert not is_identity_eligible({"signal_id": "S1", "lane": "HUMAN_APPROVED", "title": "Staff in Riyadh"})
    assert not is_identity_eligible({"signal_id": "S2", "url": "urn:grit:approved-evidence:abc", "title": "Site"})
    assert not is_identity_eligible({
        "signal_id": "S3", "title": "Travel",
        "evidence_records": [{"scope": "APPROVED_INTERNAL", "status": "SUCCEEDED"}],
    })
    assert not is_identity_eligible({"signal_id": "S4", "title": "x", "internal_exposure": [{"type": "SITE"}]})
    assert is_identity_eligible({"signal_id": "S5", "lane": "THEME", "title": "Houthi strike", "url": "https://n.example/1"})


def test_internal_signal_cannot_join_or_bridge_in_group(tmp_path: Path):
    from grit.pipeline.events import _group

    pool = Pool(tmp_path / "grit.sqlite")
    rows = [
        {"signal_id": "SIG-a", "title": "Houthi drone strikes targeting Saudi airports", "lane": "THEME",
         "editorial": {"source_ids": ["SIG-a", "SIG-internal", "SIG-b"]}},
        {"signal_id": "SIG-internal", "title": "STE staff in Saudi Arabia", "lane": "HUMAN_APPROVED",
         "url": "urn:grit:approved-evidence:staff", "editorial": {"source_ids": ["SIG-internal"]}},
        {"signal_id": "SIG-b", "title": "Houthi missile attacks on Saudi airport facilities", "lane": "THEME",
         "editorial": {"source_ids": ["SIG-b"]}},
        {"signal_id": "SIG-c", "title": "Taiwan defence funding bill advances", "lane": "THEME",
         "editorial": {"source_ids": ["SIG-c"]}},
    ]
    groups = {tuple(sorted(r["signal_id"] for r in members)): eid for eid, members in _group(pool, rows).items()}
    members = sorted(groups)
    assert ("SIG-internal",) not in members and not any("SIG-internal" in g for g in members)
    assert ("SIG-a", "SIG-b") in members
    assert ("SIG-c",) in members


def test_country_only_overlap_never_unions_flag_off(tmp_path: Path):
    from grit.pipeline.events import _group

    pool = Pool(tmp_path / "grit.sqlite")
    rows = [
        {"signal_id": "SIG-a", "title": "US orders staff out of Saudi Arabia",
         "editorial": {"source_ids": ["SIG-a", "SIG-b"]}},
        {"signal_id": "SIG-b", "title": "Security alert for Saudi Arabia", "editorial": {"source_ids": ["SIG-b"]}},
    ]
    groups = sorted(sorted(r["signal_id"] for r in m) for m in _group(pool, rows, relations_on=False).values())
    assert groups == [["SIG-a"], ["SIG-b"]]


def test_word_overlap_nominates_when_relations_on(tmp_path: Path):
    from grit.pipeline.events import _group

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN",
                   config={"weekly_mode": "reasoned", "event_relations": 1})
    nominations: list[tuple[str, str]] = []
    rows = [
        {"signal_id": "SIG-a", "title": "Houthi drone strikes targeting Saudi airports",
         "editorial": {"source_ids": ["SIG-a", "SIG-b"]}},
        {"signal_id": "SIG-b", "title": "Houthi missile attacks on Saudi airport facilities",
         "editorial": {"source_ids": ["SIG-b"]}},
    ]
    groups = sorted(sorted(r["signal_id"] for r in m) for m in _group(
        pool, rows, run_id="R", relations_on=True, nominations=nominations).values())
    assert groups == [["SIG-a"], ["SIG-b"]], "word overlap does not union when relations are on"
    assert ("SIG-a", "SIG-b") in nominations or ("SIG-b", "SIG-a") in nominations


def test_plus_ultra_alias_still_joins_labels():
    from grit.pipeline.events import label_merges

    def event(key, title, score):
        from grit.pipeline.events import UP

        row = {"signal_id": key, "title": title, "source_band": "UNRATED", "tier": 3, "publisher": key}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": "2026-11-01", "last_date": "2026-11-01",
                "risks": [], "risk_label": "", "requests": []}

    events = [event("a", "Airline seeks protection", 12), event("b", "Carrier enters insolvency", 10)]
    rows = {"EVT-a": {"item": "Plus Ultra files for creditor protection"},
            "EVT-b": {"item": "Plus Ultra Lineas Aereas enters pre-insolvency"}}
    assert label_merges(events, rows, relations_on=False) == {"EVT-b": "EVT-a"}
    assert label_merges(events, rows, relations_on=True) == {"EVT-b": "EVT-a"}


def test_anchor_rule_blocks_chaining_through_peripheral_member():
    from grit.pipeline.events import UP, _merge_stories

    def event(key, titles, score):
        members = [{"signal_id": f"{key}{i}", "title": title, "source_band": "UNRATED", "tier": 3, "publisher": f"{key}{i}",
                    "published_at": "2026-11-01"} for i, title in enumerate(titles)]
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": members[0], "members": members, "reports": members,
                "signal_ids": [m["signal_id"] for m in members], "publishers": len(members),
                "first_date": "2026-11-01", "last_date": "2026-11-01", "risks": [], "risk_label": "", "requests": []}

    # Seed A: Houthi airport story. Peripheral member mentions Taiwan — must not let Taiwan event chain in.
    seed = event("a", [
        "Houthi drone strikes targeting Saudi airports",
        "Embassy note on regional travel",
        "Taiwan chip export controls tighten overnight",
    ], 12)
    taiwan = event("t", ["Taiwan defence funding bill advances in legislature"], 11)
    ofac = event("o", ["Notice of OFAC Sanctions Action against shipping network"], 10)
    merged = _merge_stories([seed, taiwan, ofac], {})
    stories = sorted(sorted(item["signal_ids"]) for item in merged)
    assert ["t0"] in stories and ["o0"] in stories
    assert not any("t0" in group and "a0" in group for group in stories)


def test_cluster_guard_splits_oversized_multi_country_merge():
    from grit.core.identity import IDENTITY_SUSPECT, is_identity_suspect, split_suspect_to_premerge
    from grit.pipeline.events import UP, _apply_cluster_guard

    def singleton(key, title, countries_in_title=True):
        # Titles name distinct countries so a merged bag exceeds MAX_EVENT_COUNTRIES.
        row = {"signal_id": key, "title": title, "text": title, "source_band": "UNRATED", "tier": 3, "publisher": key,
               "published_at": "2026-11-01"}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": 10, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": "2026-11-01", "last_date": "2026-11-01",
                "risks": [], "risk_label": "", "requests": [], "merged_events": [f"EVT-{key}"]}

    parts = [
        singleton("a", "Houthi strikes on Saudi airports"),
        singleton("b", "Taiwan defence funding advances"),
        singleton("c", "Iran nuclear talks stall in Vienna"),
        singleton("d", "Ukraine grain corridor negotiations resume"),
    ]
    merged = {
        **parts[0],
        "event_id": "EVT-a",
        "members": [p["lead"] for p in parts],
        "reports": [p["lead"] for p in parts],
        "signal_ids": [p["lead"]["signal_id"] for p in parts],
        "merged_events": [p["event_id"] for p in parts],
    }
    assert is_identity_suspect(merged)
    pre = {p["event_id"]: p for p in parts}
    guarded = _apply_cluster_guard([merged], pre)
    assert len(guarded) == 4
    assert all(row.get("identity_status") == IDENTITY_SUSPECT for row in guarded)
    # Critic cannot emit partitions — deterministic pre-merge restore is the safe split.
    restored = split_suspect_to_premerge(merged, parts)
    assert len(restored) == 4


def test_merge_stories_uses_anchor_titles_only():
    from grit.core.identity import anchor_terms
    from grit.pipeline.events import UP, _merge_stories

    def event(key, titles, score):
        members = [{"signal_id": f"{key}{i}", "title": t, "source_band": "UNRATED", "tier": 3, "publisher": f"{key}{i}",
                    "published_at": "2026-11-01"} for i, t in enumerate(titles)]
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": members[0], "members": members, "reports": members,
                "signal_ids": [m["signal_id"] for m in members], "publishers": len(members),
                "first_date": "2026-11-01", "last_date": "2026-11-01", "risks": [], "risk_label": "", "requests": []}

    # Peripheral member shares "sanctions" with OFAC; with 4 members it is outside lead+2 medoids.
    big = event("a", [
        "Houthi drone strikes targeting Saudi airports",
        "Houthi missile alert for regional airports",
        "Embassy security note on airport strikes",
        "Shipping network faces new sanctions pressure",
    ], 12)
    ofac = event("o", ["Notice of OFAC Sanctions Action against shipping network"], 11)
    assert "sanction" not in anchor_terms(big) and "sanctions" not in anchor_terms(big)
    merged = _merge_stories([big, ofac], {})
    stories = sorted(sorted(item["signal_ids"]) for item in merged)
    assert not any(set(s) >= {"a0", "o0"} for s in stories)


def test_targeted_internal_evidence_marks_joinable_false(tmp_path: Path):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"weekly_mode": "reasoned"})
    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *a, **k: {})
    sources = {
        "SIG-news": {
            "signal_id": "SIG-news", "title": "Alert near Riyadh operations", "text": "Saudi Arabia site disruption",
            "source_band": "T1", "published_at": "2026-11-01",
        },
        "SIG-internal-match": {
            "signal_id": "SIG-internal-match", "title": "ST Engineering Saudi Limited: 40 staff in Riyadh",
            "text": "Approved internal record in Saudi Arabia", "source_band": "UNRATED", "published_at": "2026-11-01",
            "internal_exposure": [{"type": "SITE", "status": "CONFIRMED", "entity": "ST Engineering Saudi Limited"}],
        },
        "SIG-internal-other": {
            "signal_id": "SIG-internal-other", "title": "Travellers in Tokyo", "text": "Japan travel",
            "source_band": "UNRATED", "published_at": "2026-11-01",
            "internal_exposure": [{"type": "TRAVEL", "status": "CONFIRMED", "entity": "Travellers in Tokyo"}],
        },
    }
    related = coordinator.related(["SIG-news"], sources)
    assert "SIG-internal-match" in related
    assert related["SIG-internal-match"].get("joinable") is False
    assert "SIG-internal-other" not in related


def test_cluster_events_skips_human_approved(tmp_path: Path):
    from grit.daily.index import cluster_events

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="DAILY")
    day = "2026-11-01T12:00:00Z"
    news = pool.add_signal(url="https://n.example/1", title="Houthi drones strike Saudi airports overnight",
                           text="t", publisher="a", published_at=day, lane="THEME", content_hash="n1")
    internal = pool.add_signal(url="urn:grit:approved-evidence:abc", title="Staff in Riyadh",
                               text="t", publisher="internal", published_at=day, lane="HUMAN_APPROVED",
                               content_hash="i1")
    for signal_id in (news, internal):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})
    result = cluster_events(pool)
    members = {row["signal_id"] for row in pool.rows("SELECT signal_id FROM event_members")}
    assert news in members and internal not in members
    assert result["new"] == 1


def test_flag_off_path_still_merges_houthi_duplicates():
    from grit.pipeline.events import UP, _merge_stories

    def event(key, title, score, *, band="UNRATED"):
        row = {"signal_id": key, "title": title, "source_band": band, "tier": 3, "publisher": key,
               "published_at": "2026-11-01"}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": "2026-11-01", "last_date": "2026-11-01",
                "risks": [], "risk_label": "", "requests": []}

    events = [
        event("a", "Houthi drone strikes targeting Saudi airports", 12),
        event("b", "Houthi missile attacks on Saudi airport facilities", 10, band="T1"),
    ]
    merged = _merge_stories(events, {}, relations_on=False)
    assert sorted(sorted(item["signal_ids"]) for item in merged) == [["a", "b"]]
