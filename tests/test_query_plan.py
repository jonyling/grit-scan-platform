import re
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from grit.ontology.pack import load_ontology_pack
from grit.ontology.query_plan import (
    DEFAULT_MAX_QUERIES,
    EXACT_LANES,
    LANES,
    balanced_sample,
    build_query_plan,
    is_due,
    select_queries,
)

_ROOT = Path(__file__).resolve().parent.parent


def _library(**parts):
    base = {"sheet": "SM - Mobility Rail", "canonicalLob": "Mobility Rail", "tagClusters": [], "emergingThemes": [], "scanKeywords": None, "tier1": [], "clusters": []}
    return base | parts


def _pack(libraries=(), management=None, suppliers=(), customers=(), entities=(), competitors=(), themes=(), lobs=()):
    return SimpleNamespace(
        keyword_library={"libraries": list(libraries)},
        lob_profiles={"lobs": list(lobs)},
        themes={"themes": list(themes)},
        management_focus=management or {"focusItems": [], "requests": []},
        exposure={
            "suppliers": [{"supplierName": name} for name in suppliers],
            "customers": [{"customerName": name} for name in customers],
            "entities": [{"entityName": name, "lob": "Mobility Services"} for name in entities],
        },
        competitors={"competitors": list(competitors)},
        lob_hierarchy={"lobs": [{"lobName": "Mobility Road & Services", "lobGrouping": "Smart Mobility", "aliases": ["Mobility Services"]}]},
    )


def test_a_repeated_query_keeps_its_first_lane_merges_sources_and_takes_the_shorter_cadence() -> None:
    cluster = {"number": 4, "name": "Rail Cybersecurity", "priority": "Critical", "groups": [{"name": "Core Keywords", "terms": ["railway cybersecurity ", "SCADA"]}]}
    library = _library(tagClusters=[{"name": "Railway Cybersecurity", "priority": "Critical"}], clusters=[cluster])
    plan = build_query_plan(_pack([library]))
    by_query = {spec.query.casefold(): spec for spec in plan}
    merged = by_query["railway cybersecurity"]
    assert (merged.lane, merged.cadence_weeks, merged.priority) == ("TAG_CLUSTER", 1, "Critical")
    assert len(merged.sources) == 2
    assert by_query["scada"].lane == "CLUSTER_TERM" and by_query["scada"].cadence_weeks == 12
    assert len(plan) == 2


def test_names_are_exact_and_topics_are_not_and_entities_resolve_a_canonical_lob() -> None:
    plan = build_query_plan(
        _pack(
            [_library(tagClusters=[{"name": "Platform Screen Doors", "priority": "High"}])],
            suppliers=["LEONARDO S.P.A."],
            customers=["SBS TRANSIT LTD"],
            entities=["STA Inspection Pte Ltd"],
            competitors=[{"competitor": "Faiveley Transport", "lobLabel": "Mobility (Rail)", "canonicalLob": "Mobility Rail", "row": 145}],
        )
    )
    by_lane = {spec.lane: spec for spec in plan}
    assert by_lane["TAG_CLUSTER"].exact is False
    assert all(by_lane[lane].exact for lane in ("SUPPLIER", "CUSTOMER", "COMPETITOR", "ENTITY"))
    assert by_lane["ENTITY"].canonical_lob == "Mobility Road & Services"
    assert by_lane["COMPETITOR"].canonical_lob == "Mobility Rail"
    assert EXACT_LANES == {"SUPPLIER", "CUSTOMER", "COMPETITOR", "ENTITY"}


def test_the_market_request_crosses_countries_with_the_sectors_its_scope_names() -> None:
    request = {"number": 4, "scope": "Rail, Road & Airport markets for the following countries", "text": "Analyse the sectors.", "countries": ["Thailand", "Malaysia"]}
    plan = build_query_plan(_pack(management={"focusItems": [], "requests": [request]}))
    assert sorted(spec.query for spec in plan) == sorted(f"{country} {sector} sector" for country in ("Thailand", "Malaysia") for sector in ("Rail", "Road", "Airport"))
    # A scope that does not name its markets adds no sector. No query is invented.
    vague = build_query_plan(_pack(management={"focusItems": [], "requests": [{**request, "scope": "The following countries"}]}))
    assert vague == []


def test_an_overlong_query_fails_loudly() -> None:
    try:
        build_query_plan(_pack([_library(tagClusters=[{"name": "x" * 401, "priority": None}])]))
    except ValueError as error:
        assert "400" in str(error)
    else:
        raise AssertionError("An overlong query must not be planned")


def _small_plan():
    terms = [f"term {number}" for number in range(300)]
    library = _library(
        tagClusters=[{"name": "Critical topic", "priority": "Critical"}],
        scanKeywords={"declared": 2, "terms": ["scan one", "scan two"]},
        clusters=[{"number": 1, "name": "Cluster", "priority": None, "groups": [{"name": "Keywords", "terms": terms}]}],
    )
    return build_query_plan(_pack([library], suppliers=["Supplier A"]))


def test_selection_is_deterministic_and_every_query_is_due_within_twelve_weeks() -> None:
    plan = _small_plan()
    assert plan == _small_plan()
    monday = date(2026, 10, 12)
    assert select_queries(plan, monday) == select_queries(plan, monday)
    covered = {spec.query_id for week in range(12) for spec in plan if is_due(spec, monday + timedelta(weeks=week))}
    assert covered == {spec.query_id for spec in plan}
    weekly = [spec for spec in plan if spec.cadence_weeks == 1]
    assert weekly and all(is_due(spec, monday + timedelta(weeks=week)) for spec in weekly for week in range(12))


def test_the_cap_keeps_lane_order_and_records_what_it_defers() -> None:
    plan = _small_plan()
    selection = select_queries(plan, date(2026, 10, 12), max_queries=3)
    lanes = [item["lane"] for item in selection["selected"]]
    assert len(lanes) == 3 and lanes == sorted(lanes, key=LANES.index)
    due = {spec.query_id for spec in plan if is_due(spec, date(2026, 10, 12))}
    assert {item["query_id"] for item in selection["selected"]} | set(selection["deferredQueryIds"]) == due
    assert selection["due"] == len(due) and selection["deferredQueryIds"]


def test_the_urs_plan_traces_every_query_and_stays_inside_the_monthly_budget() -> None:
    plan = build_query_plan(load_ontology_pack(_ROOT / "config", "urs"))
    assert {spec.lane for spec in plan} == set(LANES)
    assert all(spec.sources for spec in plan)
    assert len({spec.query.casefold().strip(" .;,") for spec in plan}) == len(plan)
    assert max(len(spec.query) for spec in plan) <= 400

    monday = date(2026, 10, 12)
    sizes = [len(select_queries(plan, monday + timedelta(weeks=week))["selected"]) for week in range(5)]
    assert max(sizes) <= DEFAULT_MAX_QUERIES and sum(sizes) <= 2000, "five weekly runs stay inside the 2,000 a month free plan"


def test_critical_tag_clusters_and_exposure_names_run_every_week() -> None:
    plan = build_query_plan(load_ontology_pack(_ROOT / "config", "urs"))
    always = {spec.query_id for spec in plan if spec.cadence_weeks == 1}
    assert any(spec.priority == "Critical" and spec.lane == "TAG_CLUSTER" for spec in plan if spec.query_id in always)
    assert any(spec.lane in {"SUPPLIER", "CUSTOMER", "MANAGEMENT_FOCUS"} for spec in plan if spec.query_id in always)
    for week in range(6):
        picked = {item["query_id"] for item in select_queries(plan, date(2026, 10, 12) + timedelta(weeks=week))["selected"]}
        assert always <= picked


def test_a_small_trial_is_dealt_one_query_per_lane_in_turn() -> None:
    selected = select_queries(_small_plan(), date(2026, 10, 12), max_queries=50)["selected"]
    lanes_present = {item["lane"] for item in selected}
    trial = balanced_sample(selected, len(lanes_present))
    assert {item["lane"] for item in trial} == lanes_present and len(trial) == len(lanes_present)
    longer = balanced_sample(selected, len(selected) + 5)
    assert [item["query_id"] for item in longer if item["lane"] == "TAG_CLUSTER"] == [
        item["query_id"] for item in selected if item["lane"] == "TAG_CLUSTER"
    ]
    assert len(longer) == len(selected)


def test_name_lanes_search_by_the_short_name_news_uses_and_record_the_basis() -> None:
    pack = _pack(
        suppliers=("MOTOROLA SOLUTIONS SINGAPORE PTE. LTD.", "LEONARDO S.P.A.", "ACME"),
        customers=("LAND TRANSPORT AUTHORITY", "CH. KARNCHANG PUBLIC COMPANY LIMITED"),
        entities=("ST Engineering (Shanghai) Co., Ltd.", "ST Engineering (Taiwan) Ltd.", "SPTel Pte. Ltd."),
    )
    for rows, id_field, name_field in ((pack.exposure["suppliers"], "supplierId", "supplierName"), (pack.exposure["customers"], "customerId", "customerName"), (pack.exposure["entities"], "entityId", "entityName")):
        for number, row in enumerate(rows):
            row[id_field] = f"{id_field}-{number}"
    pack.exposure["entities"][0]["countryOfIncorporation"] = "China"
    pack.exposure["entities"][2]["countryOfIncorporation"] = "Singapore"
    reviewed = {"aliases": [{"entity_id": "supplierId-1", "alias": "Leonardo"}]}
    plan = {spec.query: spec for spec in build_query_plan(pack, reviewed)}
    assert {"MOTOROLA SOLUTIONS", "Leonardo", "ACME", "LAND TRANSPORT AUTHORITY", "CH. KARNCHANG", "ST Engineering", "SPTel"} <= set(plan)
    assert not any(re.search(r"\b(PTE|LTD)\b", query.upper()) for query in plan)
    assert all(plan[query].exact for query in ("MOTOROLA SOLUTIONS", "Leonardo", "SPTel"))
    sources = {source["name"]: source["searchNameBasis"] for spec in plan.values() for source in spec.sources if "searchNameBasis" in source}
    assert sources["LEONARDO S.P.A."] == "REVIEWED" and sources["MOTOROLA SOLUTIONS SINGAPORE PTE. LTD."] == "GENERATED" and sources["ACME"] == "REGISTER"
    # Two entities share one short name. They become one query that keeps both rows as sources.
    assert len([source for source in plan["ST Engineering"].sources if source["register"] == "URS-EXPOSURE"]) == 2
