import math
from datetime import date

from grit_scan.doc_type import LISTICLE, MARKET_RESEARCH, DocType
from grit_scan.link import COMPETITOR, COUNTERPARTY, DIRECT, PERIMETER, SECTOR, THEME, Involvement, Link, ThemeHit
from grit_scan.priority import PriorityConfig
from grit_scan.scale_cues import Figure, ScaleCues
from retrieval.signal_filter import FilterConfig, filter_candidates

_RUN = date(2026, 10, 6)


def _unit(degrees):
    return [math.cos(math.radians(degrees)), math.sin(math.radians(degrees))]


_RUNGS = {"ENTITY": DIRECT, "LOB": SECTOR}


def _bind(level=None, lob="Mobility Rail"):
    """A link at a rung. level None is unlinked, ENTITY is DIRECT, LOB is SECTOR."""
    rung = _RUNGS.get(level, level)
    return Link(rung, (), None, lob if rung else None, (), (), (), ())


def _c(name, published="2026-10-05T10:00:00Z", publisher=None):
    return {"url": f"https://{name}.com/a", "title": name, "publisher": publisher or name, "published_at": published}


def _run(rows, config=FilterConfig(), registered=(), doc_types=None, scales=None):
    """rows: (candidate, bind, best anchor score, in-query score, angle). One query q holds every row."""
    candidates = [row[0] for row in rows]
    return filter_candidates(
        candidates,
        run_date=_RUN,
        links={row[0]["url"]: row[1] for row in rows},
        doc_types=doc_types,
        scales=scales,
        best_anchors={row[0]["url"]: [{"anchor_id": "A", "score": row[2]}] for row in rows},
        query_scores={row[0]["url"]: [{"query_id": "q", "rank": 1, "score": row[3]}] for row in rows},
        vectors={row[0]["url"]: _unit(row[4]) for row in rows},
        source_registered={row[0]["url"]: row[0]["publisher"] in registered for row in rows},
        config=config,
    )


def _by_title(result):
    return {item["title"]: item for item in result["decisions"]}


def test_a_stale_page_is_dropped_and_a_missing_date_is_not_stale() -> None:
    rows = [(_c("old", published="2026-09-20T00:00:00Z"), _bind("LOB"), 0.7, 0.7, 0), (_c("undated", published=None), _bind("LOB"), 0.7, 0.7, 90)]
    decisions = _by_title(_run(rows))
    assert decisions["old"]["reason"] == "STALE" and decisions["undated"]["decision"] == "KEEP_BOUND"


def test_a_bound_page_stays_and_an_unbound_page_must_rank_near_the_top_of_a_query() -> None:
    rows = [(_c(f"u{number}"), _bind(), 0.7, 0.9 - number / 100, number * 30) for number in range(7)] + [(_c("bound-low"), _bind("LOB"), 0.7, 0.1, 200)]
    result = _run(rows, FilterConfig(unbound_cap=50))
    decisions = _by_title(result)
    assert [decisions[f"u{number}"]["decision"] for number in range(5)] == ["KEEP_UNBOUND"] * 5
    assert decisions["u5"]["reason"] == decisions["u6"]["reason"] == "NOT_TOP_IN_QUERY"
    assert decisions["bound-low"]["decision"] == "KEEP_BOUND" and decisions["bound-low"]["query_rank"] == 8


def test_near_duplicates_collapse_to_the_better_copy_and_record_the_outlets() -> None:
    rows = [(_c("wire", publisher="wire"), _bind("LOB"), 0.70, 0.8, 0), (_c("copy", publisher="paper"), _bind("LOB"), 0.65, 0.8, 8), (_c("other"), _bind("LOB"), 0.72, 0.8, 90)]
    result = _run(rows)
    decisions = _by_title(result)
    assert decisions["copy"]["reason"] == "NEAR_DUPLICATE" and decisions["copy"]["duplicate_of"] == "https://wire.com/a"
    assert decisions["wire"]["also_reported_by"] == [{"url": "https://copy.com/a", "publisher": "paper"}] and decisions["wire"]["corroboration"] == 2
    # Two outlets outrank one outlet, even when the single story is closer in topic.
    assert result["bound"] == ["https://wire.com/a", "https://other.com/a"]


def test_ranking_is_entity_then_lob_then_corroboration_similarity_registered_source_recency_and_url() -> None:
    rows = [
        (_c("lob-high"), _bind("LOB"), 0.90, 0.8, 0),
        (_c("entity-low"), _bind("ENTITY"), 0.50, 0.8, 30),
        (_c("lob-registered", publisher="reg"), _bind("LOB"), 0.80, 0.8, 60),
        (_c("lob-plain", publisher="plain"), _bind("LOB"), 0.80, 0.8, 90),
        (_c("lob-newer", published="2026-10-06T08:00:00Z"), _bind("LOB"), 0.80, 0.8, 120),
    ]
    result = _run(rows, registered={"reg"})
    assert [_by_title(result)[title]["url"] for title in ("entity-low", "lob-high", "lob-registered", "lob-newer", "lob-plain")] == result["bound"]


def test_caps_cut_by_rank_and_log_the_reason_and_one_event_takes_one_unbound_slot() -> None:
    rows = [
        (_c("b1"), _bind("LOB"), 0.9, 0.9, 0),
        (_c("b2"), _bind("LOB"), 0.8, 0.9, 60),
        (_c("u1"), _bind(), 0.9, 0.9, 120),
        (_c("u1-same-event"), _bind(), 0.8, 0.8, 142),
        (_c("u2"), _bind(), 0.7, 0.7, 200),
        (_c("u3"), _bind(), 0.6, 0.6, 260),
    ]
    result = _run(rows, FilterConfig(bound_cap=1, unbound_cap=2, per_query_top=10))
    decisions = _by_title(result)
    assert decisions["b2"]["reason"] == "BOUND_OVER_CAP" and decisions["u3"]["reason"] == "UNBOUND_OVER_CAP"
    # 22 degrees apart is cosine 0.93: not a duplicate, but too close for a second slot.
    assert decisions["u1-same-event"]["reason"] == "SIMILAR_TO_SELECTED" and decisions["u1-same-event"]["duplicate_of"] == "https://u1.com/a"
    assert result["unbound"] == ["https://u1.com/a", "https://u2.com/a"]


def test_every_candidate_has_exactly_one_decision_and_the_input_order_does_not_matter() -> None:
    rows = [(_c(f"n{number}"), _bind("LOB" if number % 2 else None), 0.5 + number / 100, 0.9 - number / 100, number * 25) for number in range(8)]
    forward, backward = _run(rows), _run(list(reversed(rows)))
    summary = forward["summary"]
    assert all(item["decision"] in {"KEEP_BOUND", "KEEP_UNBOUND", "DROP"} for item in forward["decisions"])
    assert summary["keep_bound"] + summary["keep_unbound"] + sum(summary["dropped"].values()) == summary["candidates"] == 8
    assert (forward["bound"], forward["unbound"]) == (backward["bound"], backward["unbound"])


def test_a_market_research_listing_or_a_ranked_list_reports_no_event_and_is_dropped_even_when_linked() -> None:
    rows = [(_c("report"), _bind("ENTITY"), 0.9, 0.9, 0), (_c("list"), _bind("LOB"), 0.9, 0.9, 60), (_c("news"), _bind("LOB"), 0.9, 0.9, 120)]
    docs = {rows[0][0]["url"]: DocType(MARKET_RESEARCH, ("CAGR",)), rows[1][0]["url"]: DocType(LISTICLE, ())}
    decisions = _by_title(_run(rows, doc_types=docs))
    assert [(decisions[t]["decision"], decisions[t]["reason"]) for t in ("report", "list")] == [("DROP", "NOT_AN_EVENT")] * 2
    assert decisions["report"]["doc_type"] == MARKET_RESEARCH and decisions["news"]["decision"] == "KEEP_BOUND"


def test_the_ladder_orders_direct_then_counterparty_then_perimeter_then_sector() -> None:
    rows = [
        (_c("sector"), _bind(SECTOR), 0.9, 0.8, 0),
        (_c("perimeter"), _bind(PERIMETER), 0.8, 0.8, 40),
        (_c("counterparty"), _bind(COUNTERPARTY), 0.7, 0.8, 80),
        (_c("direct"), _bind(DIRECT), 0.6, 0.8, 120),
    ]
    result = _run(rows)
    assert [_by_title(result)[title]["url"] for title in ("direct", "counterparty", "perimeter", "sector")] == result["bound"]


def test_a_decision_carries_the_link_and_the_legacy_fields_the_first_report_reads() -> None:
    named = Involvement("CUSTOMER-lta", "Customer", "LAND TRANSPORT AUTHORITY", "Land Transport Authority", "REGISTER", "TITLE", 1, True, False)
    passing = Involvement("SUPPLIER-byd", "Supplier", "BYD", "BYD", "GENERATED", "BODY", 1, False, False)
    link = Link(COUNTERPARTY, (named, passing), "LOB-x", "Mobility Rail", ("signalling",), (), (), ())
    decision = _by_title(_run([(_c("a"), link, 0.7, 0.8, 0)]))["a"]
    assert decision["rung"] == COUNTERPARTY and decision["link"]["involves"][0]["node_id"] == "CUSTOMER-lta"
    assert decision["link_level"] == "ENTITY" and decision["canonical_lob"] == "Mobility Rail"
    assert decision["entity_names"] == ["LAND TRANSPORT AUTHORITY", "Mobility Rail"] and decision["match_types"] == ["REGISTER_EXACT", "LOB_KEYWORD"]
    assert decision["evidence_spans"] == ["Land Transport Authority", "signalling"]


_KEY = (ThemeHit("URS-KEY-04", "KEY", ("cybersecurity",), "TITLE", True),)
_ACTION = ScaleCues((), False, ("tariffs",), ("GOV_ACTION",))
_TWO = ScaleCues((Figure("$5 million", 5e6, "USD", True, None),), True, ("tariffs",), ("GOV_ACTION",))
_ONE_CUE = FilterConfig(priority=PriorityConfig(min_cues=1))


def _themed(rung=None, themes=_KEY):
    return Link(rung, (), None, "Mobility Rail" if rung else None, (), (), themes, ())


def test_a_big_event_with_no_register_link_is_promoted_to_the_theme_rung_and_kept() -> None:
    fillers = [(_c(f"filler{n}"), _themed(themes=()), 0.5, 0.9 - n / 100, 100 + n * 20) for n in range(6)]
    rows = fillers + [(_c("quiet"), _themed(), 0.5, 0.1, 0), (_c("big"), _themed(), 0.5, 0.1, 60)]
    scales = {rows[-1][0]["url"]: _TWO}
    decisions = _by_title(_run(rows, scales=scales))
    # Both rank below the top 5 of the query. The big one is kept at the THEME rung. The quiet one is not linked and is dropped.
    assert decisions["big"]["query_rank"] > 5 and decisions["quiet"]["query_rank"] > 5
    assert decisions["big"]["decision"] == "KEEP_BOUND" and decisions["big"]["rung"] == THEME and decisions["big"]["promoted"] is True
    assert decisions["big"]["link_level"] == "THEME" and decisions["big"]["priority"]["tier"] == 2
    assert decisions["quiet"]["decision"] == "DROP" and decisions["quiet"]["reason"] == "NOT_TOP_IN_QUERY"


def test_tiers_decide_the_order_before_the_rung_and_cues_break_ties_inside_a_tier() -> None:
    rows = [
        (_c("sector"), _bind(SECTOR), 0.9, 0.8, 0),
        (_c("direct-quiet"), _bind(DIRECT), 0.5, 0.8, 30),
        (_c("big-theme"), _themed(), 0.5, 0.8, 60),
        (_c("direct-big"), _themed(DIRECT), 0.5, 0.8, 90),
        (_c("direct-cue"), _bind(DIRECT), 0.5, 0.8, 120),
    ]
    scales = {rows[2][0]["url"]: _ACTION, rows[3][0]["url"]: _ACTION, rows[4][0]["url"]: _ACTION}
    result = _run(rows, _ONE_CUE, scales=scales)
    order = [next(item["title"] for item in result["decisions"] if item["url"] == url) for url in result["bound"]]
    # Tier 1 first. In tier 2, the KEY theme plus a cue (two cues) outranks a direct link with one cue. A direct name mention with no cue is tier 3, ahead of a sector link.
    assert order == ["direct-big", "big-theme", "direct-cue", "direct-quiet", "sector"]
    assert result["summary"]["tiers"] == {"1": 1, "2": 2, "3": 2} and result["summary"]["big"] == 2  # a direct link with no cue is a name mention: tier 3


def test_outlets_that_carry_the_same_event_merge_into_one_finding_and_count_toward_the_big_event_cue() -> None:
    angles = [0, 5, 8]
    config = FilterConfig(near_duplicate=0.999, priority=PriorityConfig(same_event=0.99, outlets_needed=3, min_cues=1))
    rows = [(_c(f"story{n}", publisher=f"outlet{n}.com"), _themed(), 0.5, 0.9, angle) for n, angle in enumerate(angles, start=1)]
    result = _run(rows, config)
    decisions = _by_title(result)
    # Three outlets, a KEY theme, and nothing else: with one cue enough, the outlet cue alone makes the event big. One page represents the event.
    assert len(result["bound"]) == 1 and result["summary"]["dropped"] == {"SAME_EVENT": 2}
    primary = decisions[next(title for title, item in decisions.items() if item["decision"] == "KEEP_BOUND")]
    assert primary["priority"]["outlets"] == 3 and primary["priority"]["big"] is True and primary["corroboration"] == 3
    assert len(primary["also_reported_by"]) == 2
    assert {item["duplicate_of"] for item in decisions.values() if item["reason"] == "SAME_EVENT"} == {primary["url"]}


def test_a_wire_service_counts_once_and_three_outlets_without_a_key_theme_are_not_big() -> None:
    angles = [0, 5, 8]
    config = FilterConfig(near_duplicate=0.999, priority=PriorityConfig(same_event=0.99, outlets_needed=3, min_cues=1))
    wires = [(_c(f"story{n}", publisher=host), _themed(), 0.5, 0.9, angle) for n, (host, angle) in enumerate(zip(["globenewswire.com", "prnewswire.com", "openpr.com"], angles), start=1)]
    kept = _run(wires, config)
    primary = next(item for item in kept["decisions"] if item["decision"] == "KEEP_BOUND" or item["decision"] == "KEEP_UNBOUND")
    assert primary["priority"]["outlets"] == 1 and primary["priority"]["big"] is False
    plain = [(_c(f"story{n}", publisher=f"outlet{n}.com"), _themed(themes=()), 0.5, 0.9, angle) for n, angle in enumerate(angles, start=1)]
    assert all(item["priority"] is None or item["priority"]["big"] is False for item in _run(plain, config)["decisions"])


def test_one_cue_is_not_big_by_default_and_the_summary_shows_what_the_looser_bar_would_keep() -> None:
    rows = [(_c("campaign"), _themed(), 0.5, 0.9, 0), (_c("rule-and-figure"), _themed(), 0.5, 0.9, 60)]
    scales = {rows[0][0]["url"]: _ACTION, rows[1][0]["url"]: _TWO}
    result = _run(rows, scales=scales)
    decisions = _by_title(result)
    assert decisions["campaign"]["priority"]["big"] is False and decisions["rule-and-figure"]["priority"]["big"] is True
    assert result["summary"]["big"] == 1 and result["summary"]["big_with_one_cue"] == 2


def test_inside_a_tier_the_best_theme_tier_comes_first_then_the_cues() -> None:
    key = (ThemeHit("URS-KEY-02", "KEY", ("tariff",), "TITLE", True),)
    tier2 = (ThemeHit("URS-TIER_2-02", "TIER_2", ("typhoon",), "TITLE", True),)
    emerging = (ThemeHit("URS-EMERGING-02", "EMERGING", ("semiconductor",), "TITLE", True),)
    rows = [
        (_c("two-cues"), _themed(DIRECT, themes=()), 0.5, 0.8, 0),
        (_c("emerging"), _themed(DIRECT, themes=emerging), 0.5, 0.8, 40),
        (_c("tier2"), _themed(DIRECT, themes=tier2), 0.5, 0.8, 80),
        (_c("key"), _themed(DIRECT, themes=key), 0.5, 0.8, 120),
    ]
    scales = {rows[0][0]["url"]: _TWO}
    result = _run(rows, scales=scales)
    order = [next(item["title"] for item in result["decisions"] if item["url"] == url) for url in result["bound"]]
    assert order == ["key", "tier2", "emerging", "two-cues"]  # a theme tier outranks the number of cues


def test_the_page_with_the_strongest_link_stays_when_pages_of_one_event_merge() -> None:
    config = FilterConfig(near_duplicate=0.999, priority=PriorityConfig(same_event=0.99))
    rows = [(_c("themed-unlinked", publisher="a.com"), _themed(), 0.5, 0.9, 0), (_c("competitor", publisher="b.com"), _bind(COMPETITOR), 0.5, 0.9, 5)]
    decisions = _by_title(_run(rows, config))
    assert decisions["competitor"]["decision"] == "KEEP_BOUND" and decisions["competitor"]["rung"] == COMPETITOR
    assert decisions["themed-unlinked"]["reason"] == "SAME_EVENT" and decisions["themed-unlinked"]["duplicate_of"] == decisions["competitor"]["url"]
    assert [item["publisher"] for item in decisions["competitor"]["also_reported_by"]] == ["a.com"]
    # The linked page survives even when no page ranks near the top of a query.
    cut = _by_title(_run(rows, FilterConfig(near_duplicate=0.999, per_query_top=0, priority=PriorityConfig(same_event=0.99))))
    assert cut["competitor"]["decision"] == "KEEP_BOUND"
    counterparty = [(_c("competitor", publisher="b.com"), _bind(COMPETITOR), 0.5, 0.9, 0), (_c("customer", publisher="c.com"), _bind(COUNTERPARTY), 0.5, 0.9, 5)]
    merged = _by_title(_run(counterparty, config))
    assert merged["customer"]["decision"] == "KEEP_BOUND" and merged["competitor"]["reason"] == "SAME_EVENT"


def test_a_wire_service_counts_once_in_the_outlet_tie_break_and_in_the_logged_corroboration() -> None:
    config = FilterConfig(near_duplicate=0.999, priority=PriorityConfig(same_event=0.99))
    hosts = ["globenewswire.com", "prnewswire.com", "openpr.com"]
    wires = [(_c(f"wire{n}", publisher=host), _bind(SECTOR), 0.9, 0.9, angle) for n, (host, angle) in enumerate(zip(hosts, (0, 3, 6)))]
    papers = [(_c(f"paper{n}", publisher=f"paper{n}.com"), _bind(SECTOR), 0.5, 0.9, angle) for n, angle in enumerate((90, 93))]
    result = _run(wires + papers, config)
    first, second = (next(item for item in result["decisions"] if item["url"] == url) for url in result["bound"])
    # Three wire copies are one outlet. Two papers are two outlets, so they rank first despite the lower topic score.
    assert first["title"].startswith("paper") and second["title"].startswith("wire")
    assert first["corroboration"] == 2 and first["priority"]["outlets"] == 2 and second["corroboration"] == 1 and second["priority"]["outlets"] == 1
    assert {item["reason"] for item in result["decisions"] if item["decision"] == "DROP"} == {"SAME_EVENT"} and len(result["bound"]) == 2  # the other copies merged


def test_a_page_added_by_the_one_slot_per_event_rule_is_counted_in_the_final_corroboration() -> None:
    config = FilterConfig(near_duplicate=0.95, diverse_similarity=0.90)
    rows = [(_c("first", publisher="a.com"), _bind(), 0.7, 0.9, 0), (_c("second", publisher="b.com"), _bind(), 0.7, 0.8, 20)]
    result = _run(rows, config)
    decisions = _by_title(result)
    assert decisions["second"]["reason"] == "SIMILAR_TO_SELECTED" and decisions["first"]["decision"] == "KEEP_UNBOUND"
    assert [item["publisher"] for item in decisions["first"]["also_reported_by"]] == ["b.com"] and decisions["first"]["corroboration"] == 2
