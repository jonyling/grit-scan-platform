import csv
import json
from tempfile import TemporaryDirectory
from pathlib import Path

from grit.ontology.link import LinkIndex, link_candidate
from grit.ontology.graph import PROPOSED, VALIDATED, build_graph
from grit.ontology.relationship_gaps import (
    ALIAS_COLUMNS,
    RELATIONSHIP_COLUMNS,
    alias_rows,
    import_aliases,
    import_relationships,
    merge_overlay,
    relationship_rows,
    write_csv,
)
from tests.test_link import _FILLER, _pack


def _graph(**kwargs):
    pack = _pack()
    pack.exposure["entities"].append({"entityId": "ENTITY-m", "entityName": "Mixed Mobility Pte Ltd", "aliases": [], "countryOfIncorporation": "Singapore", "lob": "Mobility (Rail & Road)", "lobGrouping": "Smart Mobility"})
    pack.competitors["competitors"].append({"lobLabel": "Unmapped", "canonicalLob": None, "competitor": "Orphan Co", "workscope": "x", "row": 9})
    return pack, build_graph(pack, **kwargs)


def _by_kind(rows):
    return {(row["kind"], row["node_id"]): row for row in rows}


def test_the_export_lists_each_gap_with_its_candidates_and_what_is_needed() -> None:
    _, graph = _graph()
    rows = _by_kind(relationship_rows(graph))
    entity = rows[("ENTITY_LOB_UNRESOLVED", "ENTITY-m")]
    assert entity["relation"] == "operates_in" and entity["candidates"] == "LOB-mobility-rail" and "Mobility (Rail & Road)" in entity["current_link"]
    customer = rows[("CUSTOMER_ENTITY_UNKNOWN", "CUSTOMER-lta")]
    assert customer["relation"] == "buys_from" and "LOB-mobility-rail" in customer["candidates"] and "Urban Solutions" in customer["current_link"]
    assert rows[("SUPPLIER_ENTITY_UNKNOWN", "SUPPLIER-byd")]["current_link"] == "supplies URS Singapore"
    assert ("COMPETITOR_LOB_UNRESOLVED", next(key[1] for key in rows if key[0] == "COMPETITOR_LOB_UNRESOLVED" and "Orphan" in rows[key]["node_name"])) in rows


def test_the_alias_list_holds_every_non_register_name_with_a_risk_hint() -> None:
    _, graph = _graph(reviewed_aliases={"aliases": [{"entity_id": "SUPPLIER-leonardo", "alias": "Leonardo"}]})
    rows = {(row["node_id"], row["alias"]): row for row in alias_rows(graph)}
    assert rows[("SUPPLIER-byd", "BYD")]["basis"] == "GENERATED" and "ACRONYM" in rows[("SUPPLIER-byd", "BYD")]["risk_hint"]
    assert rows[("SUPPLIER-leonardo", "Leonardo")]["basis"] == "REVIEWED" and "ONE_WORD_NAME" in rows[("SUPPLIER-leonardo", "Leonardo")]["risk_hint"]
    assert not any(row["alias"] == "LAND TRANSPORT AUTHORITY" for row in rows.values())  # a register name is not in question


def test_a_filled_row_becomes_a_proposed_edge_or_a_validated_one_when_approved_and_a_blank_row_is_skipped() -> None:
    _, graph = _graph()
    base = {"kind": "CUSTOMER_ENTITY_UNKNOWN", "node_id": "CUSTOMER-lta", "answer_node_ids": "LOB-mobility-rail", "basis": "BA note, 6 Oct", "approved_by": "", "approved_on": ""}
    edges, errors = import_relationships([base, {**base, "node_id": "CUSTOMER-lta", "answer_node_ids": ""}], graph)
    assert errors == [] and edges == [{"src": "CUSTOMER-lta", "rel": "buys_from", "dst": "LOB-mobility-rail", "status": PROPOSED, "basis": "BA note, 6 Oct"}]
    approved, _ = import_relationships([{**base, "approved_by": "Jony", "approved_on": "2026-10-07"}], graph)
    assert approved[0]["status"] == VALIDATED and approved[0]["approvedBy"] == "Jony"
    several, _ = import_relationships([{**base, "answer_node_ids": "LOB-mobility-rail|ENTITY-a"}], graph)
    assert [edge["dst"] for edge in several] == ["LOB-mobility-rail", "ENTITY-a"]


def test_a_bad_answer_is_an_error_with_the_row_number_and_nothing_is_returned_as_valid_for_it() -> None:
    _, graph = _graph()
    row = {"kind": "ENTITY_LOB_UNRESOLVED", "node_id": "ENTITY-m", "answer_node_ids": "LOB-mobility-rail", "basis": "x", "approved_by": "", "approved_on": ""}
    cases = [
        ({**row, "basis": ""}, "needs a basis"),
        ({**row, "answer_node_ids": "LOB-nope"}, "not a node in the graph"),
        ({**row, "answer_node_ids": "ENTITY-a"}, "is a STE, and this row accepts LOB"),
        ({**row, "approved_by": "Jony"}, "go together"),
        ({**row, "kind": "OTHER"}, "unknown kind or node"),
    ]
    for case, message in cases:
        edges, errors = import_relationships([case], graph)
        assert edges == [] and len(errors) == 1 and errors[0].startswith("row 2 (ENTITY-m)") and message in errors[0], (case, errors)


def test_the_imported_edge_changes_the_graph_and_a_second_export_no_longer_asks_for_it() -> None:
    pack, graph = _graph()
    edges, _ = import_relationships([{"kind": "ENTITY_LOB_UNRESOLVED", "node_id": "ENTITY-m", "answer_node_ids": "LOB-mobility-rail", "basis": "BA note", "approved_by": "", "approved_on": ""}], graph)
    overlay = merge_overlay({"registerId": "URS-RELATIONSHIPS", "status": "NO_ROWS_YET", "edges": [], "aliases": []}, edges, [], [])
    assert overlay["status"] == "HAS_ROWS"
    updated = build_graph(pack, overlay=overlay)
    assert updated.has_edge("ENTITY-m", "operates_in", "LOB-mobility-rail")
    assert ("ENTITY_LOB_UNRESOLVED", "ENTITY-m") not in _by_kind(relationship_rows(updated))
    assert ("ENTITY_LOB_UNRESOLVED", "ENTITY-m") in _by_kind(relationship_rows(graph))


def test_a_rejected_alias_is_switched_off_and_an_accepted_one_is_added_and_a_repeat_replaces_the_old_row() -> None:
    pack, graph = _graph()
    accepted, rejected, errors = import_aliases(
        [
            {"node_id": "SUPPLIER-leonardo", "alias": "LEONARDO", "decision": "REJECT", "note": "Matches the actor's first name"},
            {"node_id": "CUSTOMER-lta", "alias": "LAND TRANSPORT AUTHORITY", "decision": "ACCEPT", "approved_by": "Jony", "approved_on": "2026-10-07"},
            {"node_id": "SUPPLIER-byd", "alias": "BYD", "decision": ""},
        ],
        graph,
    )
    assert errors == [] and rejected == [{"node": "SUPPLIER-leonardo", "alias": "LEONARDO", "reason": "Matches the actor's first name"}]
    assert accepted == [{"node": "CUSTOMER-lta", "alias": "LAND TRANSPORT AUTHORITY", "approvedBy": "Jony", "approvedOn": "2026-10-07"}]
    overlay = merge_overlay({}, [], [], rejected)
    updated = build_graph(pack, overlay=overlay)
    assert "LEONARDO" not in updated.nodes["SUPPLIER-leonardo"].aliases
    index = LinkIndex(updated, pack.lob_profiles)
    off = link_candidate({"title": "Leonardo supplies platform screen doors", "text": _FILLER}, index, pack.lob_profiles)
    assert off.rung == "SECTOR" and off.involves == ()  # the alias is off: only the LOB keyword links the story
    again = merge_overlay(overlay, [], [], [{"node": "SUPPLIER-leonardo", "alias": "LEONARDO", "reason": "second note"}])
    assert len(again["rejectedAliases"]) == 1 and again["rejectedAliases"][0]["reason"] == "second note"


def test_a_decision_must_be_accept_or_reject_and_name_an_alias_the_node_has() -> None:
    _, graph = _graph()
    _, _, errors = import_aliases(
        [{"node_id": "SUPPLIER-byd", "alias": "BYD", "decision": "MAYBE"}, {"node_id": "SUPPLIER-byd", "alias": "NOPE", "decision": "REJECT"}], graph
    )
    assert "decision must be ACCEPT or REJECT" in errors[0] and "no such alias on that node" in errors[1]


def test_the_csv_round_trips_with_a_header_and_utf8() -> None:
    _, graph = _graph()
    with TemporaryDirectory() as directory:
        path = Path(directory) / "r.csv"
        count = write_csv(path, RELATIONSHIP_COLUMNS, relationship_rows(graph))
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        assert count == len(rows) and list(rows[0].keys()) == list(RELATIONSHIP_COLUMNS) and rows[0]["answer_node_ids"] == ""
        assert list(csv.DictReader((Path(directory) / "r.csv").open(encoding="utf-8-sig")))[0]["kind"]
        write_csv(Path(directory) / "a.csv", ALIAS_COLUMNS, alias_rows(graph))
        assert (Path(directory) / "a.csv").read_text(encoding="utf-8-sig").splitlines()[0] == ",".join(ALIAS_COLUMNS)
    json.dumps({"ok": True})


def test_an_accepted_generated_alias_becomes_a_firm_overlay_alias_and_leaves_the_review_list() -> None:
    pack, graph = _graph()
    assert graph.nodes["SUPPLIER-byd"].alias_basis["BYD"] == "GENERATED"
    accepted, _, errors = import_aliases([{"node_id": "SUPPLIER-byd", "alias": "BYD", "decision": "ACCEPT", "approved_by": "Jony", "approved_on": "2026-10-07"}], graph)
    assert errors == []
    updated = build_graph(pack, overlay=merge_overlay({}, [], accepted, []))
    assert updated.nodes["SUPPLIER-byd"].alias_basis["BYD"] == "OVERLAY"
    assert ("SUPPLIER-byd", "BYD") not in {(row["node_id"], row["alias"]) for row in alias_rows(updated)}
    index = LinkIndex(updated, pack.lob_profiles)
    link = link_candidate({"title": "BYD plans a plant", "text": _FILLER}, index, pack.lob_profiles)
    assert [item.alias_basis for item in link.involves if item.node_id == "SUPPLIER-byd"] == ["OVERLAY"]


def test_the_later_alias_decision_wins_and_a_reject_removes_every_spelling_of_the_name() -> None:
    pack, graph = _graph(reviewed_aliases={"aliases": [{"entity_id": "SUPPLIER-leonardo", "alias": "Leonardo"}]})
    assert {"LEONARDO", "Leonardo"} <= set(graph.nodes["SUPPLIER-leonardo"].aliases)
    accepted = [{"node": "SUPPLIER-leonardo", "alias": "LEONARDO"}]
    rejected = [{"node": "SUPPLIER-leonardo", "alias": "LEONARDO", "reason": "A first name"}]
    reject_after_accept = merge_overlay(merge_overlay({}, [], accepted, []), [], [], rejected)
    assert reject_after_accept["aliases"] == [] and len(reject_after_accept["rejectedAliases"]) == 1
    rebuilt = build_graph(pack, reviewed_aliases={"aliases": [{"entity_id": "SUPPLIER-leonardo", "alias": "Leonardo"}]}, overlay=reject_after_accept)
    assert not {"LEONARDO", "Leonardo"} & set(rebuilt.nodes["SUPPLIER-leonardo"].aliases)  # rejecting one case rejects the other
    accept_after_reject = merge_overlay(reject_after_accept, [], accepted, [])
    assert accept_after_reject["rejectedAliases"] == [] and len(accept_after_reject["aliases"]) == 1
    both = build_graph(pack, overlay={"aliases": accepted, "rejectedAliases": rejected})
    assert "LEONARDO" not in both.nodes["SUPPLIER-leonardo"].aliases  # a name in both lists is rejected


def test_a_proposed_row_never_replaces_a_validated_one_and_the_import_says_so() -> None:
    from grit.ontology.relationship_gaps import downgrades

    validated = {"src": "CUSTOMER-lta", "rel": "buys_from", "dst": "LOB-mobility-rail", "status": VALIDATED, "basis": "BA", "approvedBy": "Jony", "approvedOn": "2026-10-07"}
    proposed = {"src": "CUSTOMER-lta", "rel": "buys_from", "dst": "LOB-mobility-rail", "status": PROPOSED, "basis": "a note"}
    existing = merge_overlay({}, [validated], [], [])
    assert downgrades(existing, [proposed]) == 1
    kept = merge_overlay(existing, [proposed], [], [])
    assert kept["edges"] == [validated] and kept["edges"][0]["approvedBy"] == "Jony"
    newer = {**validated, "basis": "re-approved", "approvedOn": "2026-11-01"}
    assert merge_overlay(kept, [newer], [], [])["edges"] == [newer]  # a later VALIDATED row replaces it
    assert downgrades(existing, [newer]) == 0


def test_an_alias_rejected_earlier_can_be_accepted_again_because_import_reads_the_graph_without_the_overlay() -> None:
    pack, _ = _graph()
    pristine = build_graph(pack, overlay={})
    assert "LEONARDO" in pristine.nodes["SUPPLIER-leonardo"].aliases
    accepted, _, errors = import_aliases([{"node_id": "SUPPLIER-leonardo", "alias": "LEONARDO", "decision": "ACCEPT"}], pristine)
    assert errors == [] and accepted == [{"node": "SUPPLIER-leonardo", "alias": "LEONARDO"}]
    with_overlay = build_graph(pack, overlay={"rejectedAliases": [{"node": "SUPPLIER-leonardo", "alias": "LEONARDO", "reason": "x"}]})
    _, _, errors = import_aliases([{"node_id": "SUPPLIER-leonardo", "alias": "LEONARDO", "decision": "ACCEPT"}], with_overlay)
    assert errors and "no such alias" in errors[0]  # why import must not read this graph
