"""Hand the BA the gaps in the ontology and take their answers back. Pure functions plus a small CLI. No network, no model.

export writes two CSV files a BA can fill in with a spreadsheet:
  relationships-to-fill.csv  the links the workbook does not state: entity to one LOB, customer to entity or LOB, supplier to entity or LOB,
                             and competitor to LOB. Each row lists the candidate node ids.
  aliases-to-review.csv      every name the graph searches for that is not in the register as written, with a hint about how it could mislead.
import reads the filled files and writes config/urs/URS-Relationships.json. A relationship row is PROPOSED until the row carries both
approved_by and approved_on, then it is VALIDATED. An accepted alias is added. A rejected alias is switched off. One bad row stops the import and nothing is written.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from grit.ontology.graph import PROPOSED, VALIDATED, OntologyGraph, build_graph
from grit.settings import ROOT

RELATIONSHIP_COLUMNS = ("kind", "node_id", "node_name", "current_link", "needed", "relation", "candidates", "answer_node_ids", "basis", "approved_by", "approved_on")
ALIAS_COLUMNS = ("node_id", "node_name", "class", "alias", "basis", "risk_hint", "decision", "note", "approved_by", "approved_on")
RELATION_FOR = {"ENTITY_LOB_UNRESOLVED": "operates_in", "CUSTOMER_ENTITY_UNKNOWN": "buys_from", "SUPPLIER_ENTITY_UNKNOWN": "supplies", "COMPETITOR_LOB_UNRESOLVED": "competes_in"}
ANSWER_CLASSES = {
    "ENTITY_LOB_UNRESOLVED": {"LOB"},
    "CUSTOMER_ENTITY_UNKNOWN": {"STE", "LOB"},
    "SUPPLIER_ENTITY_UNKNOWN": {"STE", "LOB"},
    "COMPETITOR_LOB_UNRESOLVED": {"LOB"},
}
_PUBLIC_BODY = re.compile(r"^(ministry|department|bureau|board|authority|agency|commission)\b", re.I)
_SPLIT = re.compile(r"[|;]")


def _lob_ids(graph: OntologyGraph, grouping_id: str | None = None) -> list[str]:
    nodes = [node for node in graph.of_class("LOB") if node.kind == "LOB"]
    if grouping_id:
        nodes = [node for node in nodes if graph.has_edge(node.id, "part_of", grouping_id)]
    return sorted(node.id for node in nodes)


def _answered(graph: OntologyGraph, node_id: str, rel: str, classes: set[str], kinds: set[str] | None = None) -> bool:
    return any(forward and other.cls in classes and (kinds is None or other.kind in kinds) for edge, other, forward in graph.neighbours(node_id, rel))


def relationship_rows(graph: OntologyGraph) -> list[dict[str, str]]:
    """One row per node that lacks a link the workbook does not state. A node the overlay already answers is left out."""
    rows: list[dict[str, str]] = []
    lobs = _lob_ids(graph)
    for gap in graph.gaps:
        kind = gap["kind"]
        if kind == "ENTITY_LOB_UNRESOLVED":
            for entity_id in gap["ids"]:
                if _answered(graph, entity_id, "operates_in", {"LOB"}, {"LOB"}):
                    continue
                node = graph.nodes[entity_id]
                grouping = next((other.id for edge, other, forward in graph.neighbours(entity_id, "operates_in") if forward), None)
                rows.append({
                    "kind": kind, "node_id": entity_id, "node_name": node.name,
                    "current_link": f"operates_in {graph.nodes[grouping].name if grouping else '?'} (the register label is '{gap['label']}')",
                    "needed": "The one org chart LOB this entity operates in", "relation": "operates_in", "candidates": "|".join(_lob_ids(graph, grouping) or lobs),
                })
        elif kind == "CUSTOMER_ENTITY_UNKNOWN":
            for node in graph.of_class("Customer"):
                if _answered(graph, node.id, "buys_from", {"STE", "LOB"}, {"ENTITY", "LOB"}):
                    continue
                rows.append({
                    "kind": kind, "node_id": node.id, "node_name": node.name, "current_link": "buys_from Urban Solutions (the business area)",
                    "needed": "The LOB or entity this customer buys from. List every one, separated by |", "relation": "buys_from", "candidates": "|".join(lobs),
                })
        elif kind == "SUPPLIER_ENTITY_UNKNOWN":
            for node in graph.of_class("Supplier"):
                if _answered(graph, node.id, "supplies", {"STE", "LOB"}, {"ENTITY", "LOB"}):
                    continue
                units = ", ".join(other.name for edge, other, forward in graph.neighbours(node.id, "supplies") if forward)
                rows.append({
                    "kind": kind, "node_id": node.id, "node_name": node.name, "current_link": f"supplies {units}",
                    "needed": "The entity or LOB this supplier supplies. List every one, separated by |", "relation": "supplies", "candidates": "|".join(lobs),
                })
        elif kind == "COMPETITOR_LOB_UNRESOLVED":
            for node in graph.of_class("Org"):
                if not graph.neighbours(node.id, "competes_in"):
                    rows.append({
                        "kind": kind, "node_id": node.id, "node_name": node.name, "current_link": "none",
                        "needed": "The org chart LOB this competitor competes in", "relation": "competes_in", "candidates": "|".join(lobs),
                    })
    return rows


def _risk_hint(name: str, alias: str, shared: int) -> str:
    hints = []
    words = alias.split()
    if len(words) == 1 and not (alias.isupper() and alias.isalpha() and len(alias) <= 4):
        hints.append("ONE_WORD_NAME: needs a LOB keyword in the text to count")
    if alias.isupper() and alias.isalpha() and len(alias) <= 4:
        hints.append("ACRONYM: matched in capitals only")
    if _PUBLIC_BODY.match(alias):
        hints.append("PUBLIC_BODY_NAME: other countries have the same name")
    if shared > 1:
        hints.append(f"SHARED_BY_{shared}_NODES")
    return "; ".join(hints)


def alias_rows(graph: OntologyGraph) -> list[dict[str, str]]:
    """Every alias that is not the register name. A reviewed alias is listed too, so a BA can reject one."""
    uses: dict[str, int] = {}
    for node in graph.nodes.values():
        for alias in node.aliases:
            uses[alias.casefold()] = uses.get(alias.casefold(), 0) + 1
    rows = []
    for node in graph.nodes.values():
        if node.cls not in {"STE", "Customer", "Supplier", "Org"}:
            continue
        for alias in node.aliases:
            basis = node.alias_basis.get(alias, "REGISTER")
            if basis in {"REGISTER", "OVERLAY"}:
                continue  # a register name is not in question, and an overlay alias was decided already
            rows.append({
                "node_id": node.id, "node_name": node.name, "class": node.cls, "alias": alias, "basis": basis,
                "risk_hint": _risk_hint(node.name, alias, uses[alias.casefold()]), "decision": "", "note": "",
            })
    return sorted(rows, key=lambda row: (row["class"], row["node_name"], row["alias"]))


def write_csv(path: Path, columns: Iterable[str], rows: Iterable[Mapping[str, str]]) -> int:
    columns = list(columns)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
            count += 1
    return count


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [{key: (value or "").strip() for key, value in row.items()} for row in csv.DictReader(handle)]


def import_relationships(rows: Iterable[Mapping[str, str]], graph: OntologyGraph) -> tuple[list[dict[str, Any]], list[str]]:
    """Edges from the filled rows, and the errors. An unanswered row is skipped. A row with an answer but no basis is an error."""
    edges: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=2):
        answers = [item.strip() for item in _SPLIT.split(row.get("answer_node_ids", "")) if item.strip()]
        if not answers:
            continue
        kind, node_id = row.get("kind", ""), row.get("node_id", "")
        where = f"row {number} ({node_id})"
        if kind not in RELATION_FOR or node_id not in graph.nodes:
            errors.append(f"{where}: unknown kind or node")
            continue
        if not row.get("basis"):
            errors.append(f"{where}: an answer needs a basis, such as the document or the person who said it")
            continue
        approved = bool(row.get("approved_by") and row.get("approved_on"))
        if bool(row.get("approved_by")) != bool(row.get("approved_on")):
            errors.append(f"{where}: approved_by and approved_on go together")
            continue
        for answer in answers:
            node = graph.nodes.get(answer)
            if node is None:
                errors.append(f"{where}: {answer} is not a node in the graph")
            elif node.cls not in ANSWER_CLASSES[kind]:
                errors.append(f"{where}: {answer} is a {node.cls}, and this row accepts {', '.join(sorted(ANSWER_CLASSES[kind]))}")
            else:
                edge: dict[str, Any] = {"src": node_id, "rel": RELATION_FOR[kind], "dst": answer, "status": VALIDATED if approved else PROPOSED, "basis": row["basis"]}
                if approved:
                    edge |= {"approvedBy": row["approved_by"], "approvedOn": row["approved_on"]}
                edges.append(edge)
    return edges, errors


def import_aliases(rows: Iterable[Mapping[str, str]], graph: OntologyGraph) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    errors: list[str] = []
    for number, row in enumerate(rows, start=2):
        decision = row.get("decision", "").upper()
        if not decision:
            continue
        node_id, alias = row.get("node_id", ""), row.get("alias", "")
        where = f"row {number} ({node_id}, '{alias}')"
        if node_id not in graph.nodes or alias not in graph.nodes[node_id].aliases:
            errors.append(f"{where}: no such alias on that node")
        elif decision == "ACCEPT":
            if bool(row.get("approved_by")) != bool(row.get("approved_on")):
                errors.append(f"{where}: approved_by and approved_on go together")
            else:
                accepted.append({"node": node_id, "alias": alias} | ({"approvedBy": row["approved_by"], "approvedOn": row["approved_on"]} if row.get("approved_by") else {}))
        elif decision == "REJECT":
            rejected.append({"node": node_id, "alias": alias, "reason": row.get("note") or "Rejected in BA review"})
        else:
            errors.append(f"{where}: decision must be ACCEPT or REJECT")
    return accepted, rejected, errors


def merge_overlay(existing: Mapping[str, Any], edges: list[dict[str, Any]], accepted: list[dict[str, Any]], rejected: list[dict[str, Any]]) -> dict[str, Any]:
    """Add the new rows to the overlay. A repeated row replaces the old one, except that a PROPOSED row never replaces a VALIDATED one.
    A later alias decision wins: an ACCEPT removes an earlier REJECT of the same name, and a REJECT removes an earlier ACCEPT."""
    def merged(old: list[dict[str, Any]], new: list[dict[str, Any]], key: tuple[str, ...], fold: str | None = None) -> list[dict[str, Any]]:
        def keyed(item: Mapping[str, Any]) -> tuple[str, ...]:
            return tuple(str(item[name]).casefold() if name == fold else item[name] for name in key)

        table = {keyed(item): item for item in old}
        for item in new:
            current = table.get(keyed(item))
            if current is not None and current.get("status") == VALIDATED and item.get("status") != VALIDATED:
                continue
            table[keyed(item)] = item
        return list(table.values())

    def without(rows: list[dict[str, Any]], decided: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = {(item["node"], str(item["alias"]).casefold()) for item in decided}
        return [row for row in rows if (row["node"], str(row["alias"]).casefold()) not in names]

    overlay = dict(existing)
    overlay["edges"] = merged(list(existing.get("edges") or []), edges, ("src", "rel", "dst"))
    overlay["aliases"] = merged(without(list(existing.get("aliases") or []), rejected), accepted, ("node", "alias"), fold="alias")
    overlay["rejectedAliases"] = merged(without(list(existing.get("rejectedAliases") or []), accepted), rejected, ("node", "alias"), fold="alias")
    overlay["status"] = "HAS_ROWS" if overlay["edges"] or overlay["aliases"] or overlay["rejectedAliases"] else existing.get("status", "NO_ROWS_YET")
    return overlay


def downgrades(existing: Mapping[str, Any], edges: list[dict[str, Any]]) -> int:
    """How many new PROPOSED rows meet a VALIDATED row for the same relationship. merge_overlay keeps the VALIDATED one."""
    validated = {(item["src"], item["rel"], item["dst"]) for item in existing.get("edges") or [] if item.get("status") == VALIDATED}
    return sum(1 for edge in edges if (edge["src"], edge["rel"], edge["dst"]) in validated and edge.get("status") != VALIDATED)


def _load_graph(root: Path, *, with_overlay: bool = True) -> tuple[OntologyGraph, Path]:
    """with_overlay False builds the graph the workbook alone gives. Import validates against it, so a BA can reverse an earlier decision."""
    from grit.ontology.pack import load_ontology_pack

    pack = load_ontology_pack(root / "config", "urs")
    aliases = json.loads((root / "config" / "registry_aliases.json").read_text(encoding="utf-8-sig"))
    return build_graph(pack, reviewed_aliases=aliases, overlay=None if with_overlay else {}), root / "config" / "urs" / "URS-Relationships.json"


def main(argv: list[str] | None = None) -> int:
    root = ROOT
    parser = argparse.ArgumentParser(description="Export the ontology gaps for the BA to fill in, or import the filled files.")
    sub = parser.add_subparsers(dest="command", required=True)
    export = sub.add_parser("export", help="Write relationships-to-fill.csv and aliases-to-review.csv.")
    export.add_argument("--out", type=Path, default=root / "output" / "ba-review")
    load = sub.add_parser("import", help="Read the filled files and write config/urs/URS-Relationships.json.")
    load.add_argument("--relationships", type=Path, help="The filled relationships-to-fill.csv.")
    load.add_argument("--aliases", type=Path, help="The filled aliases-to-review.csv.")
    load.add_argument("--dry-run", action="store_true", help="Check the rows and print the counts. Write nothing.")
    args = parser.parse_args(argv)
    graph, overlay_path = _load_graph(root, with_overlay=args.command == "export")

    if args.command == "export":
        relationships = write_csv(args.out / "relationships-to-fill.csv", RELATIONSHIP_COLUMNS, relationship_rows(graph))
        aliases = write_csv(args.out / "aliases-to-review.csv", ALIAS_COLUMNS, alias_rows(graph))
        print(f"Wrote {relationships} relationship rows and {aliases} alias rows to {args.out}. The files hold names from the confidential workbook: do not commit them.")
        return 0

    if not args.relationships and not args.aliases:
        print("Pass --relationships, --aliases, or both.")
        return 1
    edges: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    errors: list[str] = []
    if args.relationships:
        found, problems = import_relationships(_read_csv(args.relationships), graph)
        edges, errors = found, errors + problems
    if args.aliases:
        accepted, rejected, problems = import_aliases(_read_csv(args.aliases), graph)
        errors += problems
    if errors:
        print(f"{len(errors)} problem(s). Nothing was written.")
        for item in errors[:20]:
            print(f"  {item}")
        return 1
    existing = json.loads(overlay_path.read_text(encoding="utf-8")) if overlay_path.is_file() else {}
    if downgrades(existing, edges):
        print(f"{downgrades(existing, edges)} PROPOSED row(s) were ignored because a VALIDATED row exists for the same relationship.")
    overlay = merge_overlay(existing, edges, accepted, rejected)
    # The merged overlay must build. A row the graph refuses stops here, before the file changes.
    from grit.ontology.pack import load_ontology_pack

    build_graph(load_ontology_pack(root / "config", "urs"), reviewed_aliases=json.loads((root / "config" / "registry_aliases.json").read_text(encoding="utf-8-sig")), overlay=overlay)
    print(f"{len(edges)} relationship(s), {len(accepted)} accepted alias(es), {len(rejected)} rejected alias(es).")
    if args.dry_run:
        print("Dry run. Nothing was written.")
        return 0
    overlay_path.write_text(json.dumps(overlay, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {overlay_path}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
