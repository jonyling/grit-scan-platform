"""Phase 0 semantic reasoning: NFKD/alias matching, cluster mates in related(), near-miss instrumentation."""

from __future__ import annotations

from pathlib import Path

from grit.pool import Pool


def test_accented_and_ascii_headlines_share_distinctive_terms():
    from grit.core.headlines import distinctive_terms, fold_accents

    assert fold_accents("Líneas Aéreas") == "Lineas Aereas"
    accented = distinctive_terms("Plus Ultra Líneas Aéreas files for creditor protection")
    ascii_form = distinctive_terms("Plus Ultra Lineas Aereas files for creditor protection")
    # Plural fold turns lineas/aereas into linea/aerea; accents must not keep the two sets apart.
    assert "linea" in accented and "aerea" in accented
    assert accented == ascii_form
    assert len(accented & ascii_form) >= 2


def test_entity_aliases_fold_known_names_to_one_id():
    from grit.core.headlines import alias_ids

    short = alias_ids("Plus Ultra files for creditor protection")
    long = alias_ids("Plus Ultra Líneas Aéreas enters pre-insolvency")
    assert short == {"plusultra"} and long == {"plusultra"}
    assert short & long


def test_alias_joins_labels_that_share_only_the_entity_name():
    from grit.pipeline.events import label_merges

    def event(key, title, score, *, day="2026-11-01"):
        from grit.pipeline.events import UP

        row = {"signal_id": key, "title": title, "source_band": "UNRATED", "tier": 3, "publisher": key}
        return {"event_id": f"EVT-{key}", "band": UP, "new": True, "score": score, "elevated": True, "tier": 3,
                "red": False, "red_candidate": False, "lead": row, "members": [row], "reports": [row],
                "signal_ids": [key], "publishers": 1, "first_date": day, "last_date": day, "risks": ["R1"],
                "risk_label": "", "requests": []}

    events = [event("a", "Airline seeks protection", 12), event("b", "Carrier enters insolvency talks", 10)]
    rows = {"EVT-a": {"item": "Plus Ultra files for creditor protection"},
            "EVT-b": {"item": "Plus Ultra Lineas Aereas enters pre-insolvency"}}
    assert label_merges(events, rows) == {"EVT-b": "EVT-a"}


def test_same_event_includes_cluster_mates_without_shared_words(tmp_path: Path):
    from grit.pipeline.weekly import WeeklyCoordinator, WeeklyStore, same_event

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="READER_SCAN", config={"weekly_mode": "reasoned"})
    first = pool.add_signal(url="https://a.example/1", title="Airline seeks creditor protection in Madrid",
                            text="t", publisher="a", published_at="2026-11-01", lane="TEST", content_hash="1")
    second = pool.add_signal(url="https://b.example/2", title="Spanish carrier begins insolvency talks with lenders",
                             text="t", publisher="b", published_at="2026-11-01", lane="TEST", content_hash="2")
    third = pool.add_signal(url="https://c.example/3", title="Changi Airport upgrades runway sensors",
                            text="t", publisher="c", published_at="2026-11-01", lane="TEST", content_hash="3")
    now = "2026-11-02T00:00:00Z"
    with pool.transaction() as conn:
        conn.execute("INSERT INTO events (event_id, lead_signal_id, first_date, last_date, members, created_at, updated_at) VALUES (?, ?, ?, ?, 2, ?, ?)",
                     ("EVT-story", first, "2026-11-01", "2026-11-01", now, now))
        conn.execute("INSERT INTO event_members (signal_id, event_id, joined_at) VALUES (?, ?, ?)", (first, "EVT-story", now))
        conn.execute("INSERT INTO event_members (signal_id, event_id, joined_at) VALUES (?, ?, ?)", (second, "EVT-story", now))
    sources = {
        first: {"title": "Airline seeks creditor protection in Madrid", "published_at": "2026-11-01", "source_band": "UNRATED"},
        second: {"title": "Spanish carrier begins insolvency talks with lenders", "published_at": "2026-11-01", "source_band": "T1"},
        third: {"title": "Changi Airport upgrades runway sensors", "published_at": "2026-11-01", "source_band": "T2"},
    }
    assert same_event(first, sources) == [], "word overlap alone is below the threshold"
    clusters = {first: "EVT-story", second: "EVT-story"}
    assert same_event(first, sources, clusters=clusters) == [second]

    store = WeeklyStore(pool, "R", search_limit=0, refresh=False)
    coordinator = WeeklyCoordinator(store, client=None, model="offline", call=lambda *a, **k: {})
    related = coordinator.related([first], sources)
    assert second in related and third not in related, "Assessment batch sees its event_members cluster mate"


def test_is_near_miss_requires_soft_cosine_and_a_shared_feature():
    from grit.core.headlines import is_near_miss

    assert is_near_miss(0.85, shared_terms=1)
    assert is_near_miss(0.82, shared_identifiers=1)
    assert not is_near_miss(0.91, shared_terms=2), "at or above SAME_STORY is a join, not a near-miss"
    assert not is_near_miss(0.85, shared_terms=0, shared_identifiers=0)
    assert not is_near_miss(0.70, shared_terms=3)


def test_cluster_events_logs_near_misses_without_joining(tmp_path: Path):
    import numpy as np

    from grit.daily.index import cluster_events
    from grit.retrieval.embedder import DEFAULT_DIMENSIONS, DEFAULT_MODEL

    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R", run_date="2026-11-02", pack="urs", kind="DAILY")
    day = "2026-11-01T12:00:00Z"
    first = pool.add_signal(url="https://a.example/1", title="Houthi drones strike Saudi airports overnight",
                            text="t", publisher="a", published_at=day, lane="TEST", content_hash="n1")
    second = pool.add_signal(url="https://b.example/2", title="Houthi missiles target Saudi airport facilities",
                             text="t", publisher="b", published_at=day, lane="TEST", content_hash="n2")
    # Unit vectors with cosine ~0.85: shared axis plus orthogonal remainders.
    left = np.zeros(DEFAULT_DIMENSIONS, dtype=np.float32)
    right = np.zeros(DEFAULT_DIMENSIONS, dtype=np.float32)
    left[0], left[1] = 0.922, 0.387
    right[0], right[2] = 0.922, 0.387
    left /= np.linalg.norm(left)
    right /= np.linalg.norm(right)
    assert 0.82 <= float(left @ right) < 0.90
    pool.put_vector(first, model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS,
                    text_hash="n1", vector=left.tolist())
    pool.put_vector(second, model=DEFAULT_MODEL, task="RETRIEVAL_DOCUMENT", dimensions=DEFAULT_DIMENSIONS,
                    text_hash="n2", vector=right.tolist())
    for signal_id in (first, second):
        pool.save_assessment({"signal_id": signal_id, "run_id": "R", "pack": "urs", "decision": "KEEP", "tier": 2})
    result = cluster_events(pool)
    assert result["joined"] == 0 and result["new"] == 2 and result["near_misses"] >= 1
    members = pool.rows("SELECT signal_id, event_id FROM event_members ORDER BY signal_id")
    assert len({row["event_id"] for row in members}) == 2, "near-miss does not join"
    log = pool.rows("SELECT * FROM execution_log WHERE actor = 'EventNearMiss'")
    assert log and log[0]["detail"]["count"] >= 1
