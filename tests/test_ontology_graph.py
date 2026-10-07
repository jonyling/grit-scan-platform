import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from grit.ontology.pack import load_ontology_pack
from grit.ontology.graph import DERIVED, PROPOSED, SOURCE_STATED, VALIDATED, build_graph, short_names, usable_name

_ROOT = Path(__file__).resolve().parent.parent


def _pack(**parts):
    entities = [
        {"entityId": "ENTITY-a", "entityName": "ST Engineering Urban Solutions Ltd", "aliases": [], "countryOfIncorporation": "Singapore", "lob": "Mobility Rail", "lobGrouping": "Smart Mobility"},
        {"entityId": "ENTITY-b", "entityName": "Amtech Systems (Hong Kong) Limited", "aliases": [], "countryOfIncorporation": "Hong Kong", "lob": "Mobility (Rail & Road)", "lobGrouping": "Smart Mobility"},
        {"entityId": "ENTITY-c", "entityName": "SPTel Pte. Ltd.", "aliases": [], "countryOfIncorporation": "Singapore", "lob": "Smart Security & Automation", "lobGrouping": "Smart Utilities & Infrastructure"},
    ]
    base = dict(
        exposure={
            "entities": entities,
            "customers": [{"customerId": "CUSTOMER-lta", "customerName": "LAND TRANSPORT AUTHORITY", "aliases": []}],
            "suppliers": [{"supplierId": "SUPPLIER-byd", "supplierName": "BYD (SINGAPORE) PTE LTD", "businessArea": "URS Singapore", "aliases": []}],
        },
        lob_hierarchy={
            "businessArea": "Urban Solutions",
            "lobs": [
                {"lobName": "Mobility Rail", "lobGrouping": "Smart Mobility", "aliases": []},
                {"lobName": "Mobility Road & Services", "lobGrouping": "Smart Mobility", "aliases": []},
                {"lobName": "Smart Security & Automation", "lobGrouping": "Smart Utilities & Infrastructure", "aliases": []},
            ],
        },
        lob_profiles={"lobs": []},
        competitors={"competitors": [{"lobLabel": "Mobility (Road)", "canonicalLob": "Mobility Road & Services", "competitor": "Kapsch", "workscope": "Tolling", "row": 7}]},
        management_focus={"requests": [{"number": 1, "scope": "Mobility Rail", "entity": None, "text": "Rail markets", "countries": ["Thailand"], "topics": []}], "focusItems": []},
        themes={"themes": [{"themeId": "URS-KEY-04", "themeName": "Cybersecurity", "tier": "KEY", "keywords": ["ransomware"]}]},
    )
    return SimpleNamespace(**(base | parts))


def _walk(graph, reach):
    return [(step.edge.rel, step.forward) for step in reach.steps]


def test_the_graph_links_register_facts_and_lists_what_the_workbook_lacks() -> None:
    graph = build_graph(_pack())
    assert graph.has_edge("ENTITY-a", "located_in", "PLACE-singapore") and graph.has_edge("ENTITY-a", "operates_in", "LOB-mobility-rail")
    # An unresolved LOB label links to the grouping only, and the gap names the label.
    assert graph.has_edge("ENTITY-b", "operates_in", "GROUPING-smart-mobility") and not graph.has_edge("ENTITY-b", "operates_in", "LOB-mobility-rail")
    kinds = {gap["kind"]: gap for gap in graph.gaps}
    assert kinds["ENTITY_LOB_UNRESOLVED"]["label"] == "Mobility (Rail & Road)" and kinds["ENTITY_LOB_UNRESOLVED"]["ids"] == ["ENTITY-b"]
    assert {"CUSTOMER_ENTITY_UNKNOWN", "SUPPLIER_ENTITY_UNKNOWN"} <= set(kinds)
    assert {edge.status for edge in graph.edges} == {SOURCE_STATED, DERIVED}


def test_a_customer_and_a_supplier_reach_only_the_business_area_and_the_unit_the_workbook_names() -> None:
    graph = build_graph(_pack())
    (customer,) = graph.reach("CUSTOMER-lta")
    assert customer.targets == ("STE-BA-urban-solutions",) and _walk(graph, customer) == [("buys_from", True)]
    (supplier,) = graph.reach("SUPPLIER-byd")
    assert supplier.targets == ("STE-UNIT-urs-singapore",)


def test_a_place_reaches_its_entities_and_a_competitor_reaches_its_lob_entities_without_crossing_to_a_sibling_lob() -> None:
    graph = build_graph(_pack())
    (place,) = graph.reach("PLACE-singapore")
    assert place.targets == ("ENTITY-a", "ENTITY-c")
    org = next(node.id for node in graph.nodes.values() if node.kind == "COMPETITOR")
    reached = {target for reach in graph.reach(org) for target in reach.targets}
    # Kapsch competes in Mobility Road. Entity a runs Mobility Rail, a sibling LOB, so it is not reached.
    assert reached == {"ENTITY-b", "STE-BA-urban-solutions"}
    rail = graph.reach("LOB-mobility-rail")
    assert {target for reach in rail for target in reach.targets} == {"ENTITY-a", "ENTITY-b", "STE-BA-urban-solutions"}
    assert graph.entities_in_lob("LOB-mobility-rail") == {"ENTITY-a", "ENTITY-b"}
    assert graph.entities_in_place("PLACE-singapore") == {"ENTITY-a", "ENTITY-c"}


def test_a_request_theme_or_competitor_is_never_a_stop_on_the_way() -> None:
    graph = build_graph(_pack())
    for reach in graph.reach("LOB-mobility-rail"):
        assert all(graph.nodes[step.edge.src].cls != "ManagementRequest" and graph.nodes[step.edge.dst].cls != "ManagementRequest" for step in reach.steps)
    assert graph.reach("MGMT-REQUEST-1") and graph.reach("URS-KEY-04") == []


def test_short_names_drop_brackets_suffixes_and_trailing_countries_and_refuse_generic_words() -> None:
    assert short_names("MOTOROLA SOLUTIONS SINGAPORE PTE. LTD.", ["Singapore"]) == ["MOTOROLA SOLUTIONS"]
    assert short_names("BYD (SINGAPORE) PTE LTD") == ["BYD"]
    assert short_names("LEONARDO S.P.A.") == ["LEONARDO"]
    assert short_names("Amtech Systems, LLC") == ["Amtech Systems"]
    assert short_names("SIEMENS") == [] and short_names("Smart Systems Pte Ltd") == []
    assert short_names("MAN TRUCK & BUS SE") == ["MAN TRUCK & BUS"] and short_names("ST Engineering (Latin America) Ltda.") == ["ST Engineering"]
    assert short_names("ST Engineering Urban Solutions Ltd - Dubai Branch", ["Dubai"]) == ["ST Engineering Urban Solutions"]
    assert short_names("ST Engineering Urban Solutions Ltd. - India Office", ["India"]) == ["ST Engineering Urban Solutions"]
    assert usable_name("NEC") and not usable_name("Group") and not usable_name("Traffic") and not usable_name("nec")


def test_the_group_alias_belongs_to_the_group_not_to_an_entity() -> None:
    graph = build_graph(_pack(), reviewed_aliases={"aliases": [{"entity_id": "SUPPLIER-byd", "alias": "BYD Auto"}]})
    assert "ST Engineering" in graph.nodes["STE-GROUP"].aliases
    assert "ST Engineering" not in graph.nodes["ENTITY-a"].aliases
    assert graph.nodes["SUPPLIER-byd"].alias_basis["BYD Auto"] == "REVIEWED" and graph.nodes["SUPPLIER-byd"].alias_basis["BYD"] == "GENERATED"
    assert "Urban Solutions" not in graph.nodes["STE-BA-urban-solutions"].aliases


def test_an_overlay_edge_carries_its_status_and_a_bad_row_fails_loudly() -> None:
    good = {"src": "CUSTOMER-lta", "rel": "buys_from", "dst": "ENTITY-a", "status": PROPOSED, "basis": "Seen in a BA note"}
    graph = build_graph(_pack(), overlay={"edges": [good], "aliases": [{"node": "CUSTOMER-lta", "alias": "LTA"}]})
    (reach,) = [r for r in graph.reach("CUSTOMER-lta") if r.targets == ("ENTITY-a",)]
    assert reach.weakest == PROPOSED and "LTA" in graph.nodes["CUSTOMER-lta"].aliases
    for bad, message in (
        (good | {"status": "GUESSED"}, "needs status"),
        (good | {"status": VALIDATED}, "without approvedBy"),
        (good | {"dst": "ENTITY-zzz"}, "does not exist"),
        (good | {"basis": ""}, "needs a basis"),
    ):
        with pytest.raises(ValueError, match=message):
            build_graph(_pack(), overlay={"edges": [bad]})
    with pytest.raises(ValueError, match="unknown relation"):
        build_graph(_pack(), overlay={"edges": [good | {"rel": "owns"}]})
    approved = good | {"status": VALIDATED, "approvedBy": "Jony", "approvedOn": "2026-10-07"}
    assert build_graph(_pack(), overlay={"edges": [approved]}).reach("CUSTOMER-lta")


def test_the_urs_graph_builds_from_the_workbook_pack_and_reports_its_real_gaps() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    graph = build_graph(pack, reviewed_aliases=json.loads((_ROOT / "config" / "registry_aliases.json").read_text(encoding="utf-8")))
    assert len(graph.of_class("STE")) >= 41 and len(graph.of_class("Customer")) == 20 and len(graph.of_class("Supplier")) == 15
    assert all(graph.reach(customer.id)[0].targets == ("STE-BA-urban-solutions",) for customer in graph.of_class("Customer"))
    unresolved = next(gap for gap in graph.gaps if gap["kind"] == "ENTITY_LOB_UNRESOLVED" and gap["label"] == "Mobility (Rail & Road)")
    assert unresolved["count"] == 28
