import json
from pathlib import Path
from types import SimpleNamespace

from grit_scan.link import COMPETITOR, COUNTERPARTY, DIRECT, PERIMETER, SECTOR, THEME, LinkIndex, link_candidate, rung_rank
from grit_scan.ontology import load_ontology_pack
from grit_scan.ontology_graph import build_graph

_ROOT = Path(__file__).resolve().parent.parent
_FILLER = "The programme covers design, delivery and operations across several phases and agencies. " * 8


def _pack():
    entities = [
        {"entityId": "ENTITY-a", "entityName": "ST Engineering Urban Solutions Ltd", "aliases": [], "countryOfIncorporation": "Singapore", "lob": "Mobility Rail", "lobGrouping": "Smart Mobility"},
        {"entityId": "ENTITY-c", "entityName": "SPTel Pte. Ltd.", "aliases": [], "countryOfIncorporation": "Hong Kong", "lob": "Smart Security & Automation", "lobGrouping": "Smart Utilities & Infrastructure"},
    ]
    lobs = [
        {"lobName": "Mobility Rail", "lobGrouping": "Smart Mobility", "aliases": []},
        {"lobName": "Smart Security & Automation", "lobGrouping": "Smart Utilities & Infrastructure", "aliases": []},
    ]
    return SimpleNamespace(
        exposure={
            "entities": entities,
            "customers": [{"customerId": "CUSTOMER-lta", "customerName": "LAND TRANSPORT AUTHORITY", "aliases": []}],
            "suppliers": [
                {"supplierId": "SUPPLIER-byd", "supplierName": "BYD (SINGAPORE) PTE LTD", "businessArea": "URS Singapore", "aliases": []},
                {"supplierId": "SUPPLIER-leonardo", "supplierName": "LEONARDO S.P.A.", "businessArea": "URS Singapore", "aliases": []},
            ],
        },
        lob_hierarchy={"businessArea": "Urban Solutions", "lobs": lobs},
        lob_profiles={
            "lobs": [
                {"lobId": "LOB-mobility-rail", "lobName": "Mobility Rail", "canonicalLob": "Mobility Rail", "active": True, "keywords": ["platform screen doors"]},
                {"lobId": "LOB-ssa", "lobName": "Smart Security & Automation", "canonicalLob": "Smart Security & Automation", "active": True, "keywords": ["video surveillance"]},
            ]
        },
        competitors={"competitors": [{"lobLabel": "Mobility Rail", "canonicalLob": "Mobility Rail", "competitor": "NEC", "workscope": "x", "row": 3}]},
        management_focus={"requests": [], "focusItems": []},
        themes={"themes": [{"themeId": "URS-KEY-04", "themeName": "Cybersecurity", "tier": "KEY", "keywords": ["ransomware", "forex"]}]},
    )


def _link(title, text=""):
    pack = _pack()
    index = LinkIndex(build_graph(pack), pack.lob_profiles)
    return link_candidate({"title": title, "text": text}, index, pack.lob_profiles)


def test_an_entity_in_the_title_is_direct_and_the_longer_name_wins_over_the_group_name() -> None:
    link = _link("SPTel Pte. Ltd. wins a Hong Kong tender", _FILLER)
    assert link.rung == DIRECT and link.involves[0].node_id == "ENTITY-c" and link.involves[0].where == "TITLE"
    link = _link("ST Engineering Urban Solutions Ltd signs a deal", _FILLER)
    assert [item.node_id for item in link.involves if item.cls == "STE"] == ["ENTITY-a"]
    group = _link("ST Engineering lands a defence order", _FILLER)
    assert group.rung == DIRECT and group.involves[0].node_id == "STE-GROUP"


def test_a_customer_or_supplier_in_the_title_is_counterparty_and_a_competitor_is_competitor() -> None:
    assert _link("LAND TRANSPORT AUTHORITY awards a signalling contract", _FILLER).rung == COUNTERPARTY
    assert _link("BYD plans a plant", _FILLER).rung == COUNTERPARTY  # generated short name of the supplier
    competitor = _link("NEC wins a tender", _FILLER)
    assert competitor.rung == COMPETITOR and competitor.involves[0].alias_basis == "REGISTER"


def test_one_passing_mention_in_the_body_is_recorded_and_does_not_raise_the_rung() -> None:
    body = _FILLER + "Platform screen doors are widely fitted. " + _FILLER + "Land Transport Authority was one of the buyers."
    link = _link("Station upgrades continue", body)
    assert link.rung == SECTOR and link.involves[0].strong is False and link.involves[0].where == "BODY"
    assert any(reason.startswith("Passing mention") for reason in link.reasons)
    twice = _link("Station upgrades continue", body + _FILLER + "Land Transport Authority also confirmed the date.")
    assert twice.rung == COUNTERPARTY and twice.involves[0].count == 2


def test_a_country_places_a_lob_story_on_the_perimeter_only_where_an_entity_of_that_grouping_sits() -> None:
    assert _link("Singapore tests platform screen doors", _FILLER).rung == PERIMETER
    assert _link("Hong Kong tests platform screen doors", _FILLER).rung == SECTOR  # the Hong Kong entity is in the other grouping
    assert _link("Singapore plans a new budget", _FILLER).rung is None


def test_acronyms_match_in_their_own_case_only_and_a_short_theme_keyword_is_ignored() -> None:
    assert _link("nec wins a tender", _FILLER).rung is None
    link = _link("Ransomware halts a rail operator, forex falls", _FILLER)
    assert [(hit.theme_id, hit.tier, hit.matched) for hit in link.themes] == [("URS-KEY-04", "KEY", ("ransomware",))]
    assert link.rung is None  # a theme alone is never a link


def test_a_one_word_name_needs_a_second_ontology_cue_and_an_acronym_does_not() -> None:
    namesake = _link("Leonardo DiCaprio raises alarm over forest fires", _FILLER)
    assert namesake.rung is None and namesake.involves[0].context_required and not namesake.involves[0].strong
    brazil = _link("Leonardo DiCaprio pledges support to Singapore's forests", _FILLER)
    assert brazil.rung is None and [item.node_id for item in brazil.involves if item.cls == "Supplier"] == ["SUPPLIER-leonardo"]  # one node, one involvement
    assert any(reason.startswith("Not a link: 'Leonardo' is a one-word name") for reason in namesake.reasons)
    assert _link("Leonardo supplies platform screen doors", _FILLER).rung == COUNTERPARTY  # a LOB keyword
    assert _link("Leonardo hit by ransomware", _FILLER).rung is None  # a theme keyword is not a domain cue
    assert _link("Leonardo opens an office in Singapore", _FILLER).rung is None  # a country is not a domain cue
    assert _link("BYD plans a plant", _FILLER).rung == COUNTERPARTY and _link("NEC wins a tender", _FILLER).rung == COMPETITOR
    assert _link("LAND TRANSPORT AUTHORITY awards a signalling contract", _FILLER).rung == COUNTERPARTY  # more than one word


def test_a_theme_keyword_counts_only_in_the_title_or_lead_or_when_named_twice() -> None:
    lead = _link("Rail operator hit by a ransomware attack", _FILLER)
    assert [(hit.where, hit.strong) for hit in lead.themes] == [("TITLE", True)]
    once = _link("Station upgrades continue", _FILLER + "Staff reported ransomware in an unrelated case. " + _FILLER)
    assert [(hit.where, hit.strong) for hit in once.themes] == [("BODY", False)]
    twice = _link("Station upgrades continue", _FILLER + "Ransomware struck. " + _FILLER + "The ransomware spread.")
    assert [(hit.where, hit.strong) for hit in twice.themes] == [("BODY", True)]


def test_rungs_rank_from_theme_up() -> None:
    assert [rung_rank(r) for r in (None, THEME, SECTOR, PERIMETER, COMPETITOR, COUNTERPARTY, DIRECT)] == [0, 1, 2, 3, 4, 5, 6]


def test_the_urs_pack_links_a_named_customer_and_records_the_link_as_json() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    aliases = json.loads((_ROOT / "config" / "registry_aliases.json").read_text(encoding="utf-8"))
    index = LinkIndex(build_graph(pack, reviewed_aliases=aliases), pack.lob_profiles)
    link = link_candidate({"title": "Land Transport Authority awards new rail signalling contract", "text": _FILLER}, index, pack.lob_profiles)
    assert link.rung == COUNTERPARTY and link.involves[0].node_id == "CUSTOMER-land-transport-authority"
    json.dumps(link.as_record())


def test_a_reviewed_and_a_generated_spelling_of_one_name_are_one_involvement_and_are_not_ambiguous() -> None:
    pack = _pack()
    graph = build_graph(pack, reviewed_aliases={"aliases": [{"entity_id": "SUPPLIER-leonardo", "alias": "Leonardo"}]})
    index = LinkIndex(graph, pack.lob_profiles)
    link = link_candidate({"title": "Leonardo supplies platform screen doors", "text": _FILLER}, index, pack.lob_profiles)
    suppliers = [item for item in link.involves if item.node_id == "SUPPLIER-leonardo"]
    assert len(suppliers) == 1 and suppliers[0].alias_basis == "REVIEWED" and suppliers[0].ambiguous is False
