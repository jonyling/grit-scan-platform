"""Deterministic STE link for one candidate. Pure: no I/O, no model, no score.

This is a subset of the bind described in Copilot_CLI_Ontology_Bind.md. It links a candidate to a register
entity, customer, supplier, or site, or to exactly one active LOB. Patterns, triples, and GDELT actors are
not part of it. A candidate with no register hit is unbound. The link is never inferred from a country or a theme.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from grit_scan.matchers.exposure_matcher import exposure_entity_match
from grit_scan.matchers.lob_matcher import lob_match
from grit_scan.matchers.text_norm import keyword_in_text, normalise, normalise_exposure
from grit_scan.ontology import OntologyPack

REGISTER_EXACT = "REGISTER_EXACT"
REGISTER_ALIAS = "REGISTER_ALIAS"
LOB_EXACT = "LOB_EXACT"
LOB_KEYWORD = "LOB_KEYWORD"
MAX_EVIDENCE = 5
_COLLECTIONS = ("entities", "customers", "suppliers", "projects", "sites")
_ID_FIELDS = ("entityId", "customerId", "supplierId", "projectId", "siteId")


@dataclass(frozen=True)
class BindResult:
    pack: str
    bound: bool
    link_level: str | None  # ENTITY for a register row, LOB for a line of business alone
    entity_ids: tuple[str, ...]  # register ids. An LOB-only bind carries the LOB id.
    match_types: tuple[str, ...]
    canonical_lob: str | None
    unbound_reason: str | None
    evidence_spans: tuple[str, ...]
    entity_names: tuple[str, ...] = ()  # the register's own names for entity_ids

    def as_record(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("entity_ids", "match_types", "evidence_spans", "entity_names"):
            record[key] = list(record[key])
        return record


def augment_exposure(pack: OntologyPack, aliases: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy the exposure register and add the reviewed aliases from registry_aliases.json. The pack is not changed."""
    register = copy.deepcopy(pack.exposure)
    by_id = {
        str(row.get(id_field)): row
        for collection, id_field in zip(_COLLECTIONS, _ID_FIELDS)
        for row in register.get(collection) or []
    }
    for item in (aliases or {}).get("aliases") or []:
        row = by_id.get(str(item.get("entity_id")))
        if row is not None and item.get("alias") not in (row.get("aliases") or []):
            row.setdefault("aliases", []).append(item["alias"])
    return register


def bind_candidate(candidate: Mapping[str, Any], pack: OntologyPack, *, exposure: dict[str, Any] | None = None) -> BindResult:
    """Link a candidate's title and text to the named pack's registers."""
    text = f"{candidate.get('title') or ''}\n{candidate.get('text') or ''}".strip()
    if not text:
        return _unbound(pack, "EMPTY_TEXT")
    entity_ids: list[str] = []
    entity_names: list[str] = []
    match_types: list[str] = []
    evidence: list[str] = []
    for match in exposure_entity_match(text, exposure if exposure is not None else pack.exposure)["matchedEntities"]:
        entity_ids.append(match["entityId"])
        entity_names.append(match["entityName"])
        exact = normalise_exposure(match["matchedAlias"]) == normalise_exposure(match["entityName"])
        match_types.append(REGISTER_EXACT if exact else REGISTER_ALIAS)
        evidence.append(match["matchedAlias"])

    lob = lob_match(text, pack.lob_profiles)
    canonical_lob: str | None = None
    ambiguous = lob["matchMethod"].startswith("AMBIGUOUS")
    if lob["matched"] and lob["matchMethod"] in {"EXACT_NAME_OR_CODE", "KEYWORD_MATCH"}:
        chosen = lob["lob"]
        canonical_lob = chosen.get("canonicalLob") or chosen.get("lobName")
        entity_ids.append(str(chosen["lobId"]))
        entity_names.append(str(chosen["lobName"]))
        match_types.append(LOB_EXACT if lob["matchMethod"] == "EXACT_NAME_OR_CODE" else LOB_KEYWORD)
        normalised = normalise(text)
        evidence.extend(keyword for keyword in chosen.get("keywords") or [] if keyword_in_text(normalised, normalise(keyword)))
    if not entity_ids:
        return _unbound(pack, "AMBIGUOUS_LOB" if ambiguous else "NO_REGISTER_HIT")
    has_entity = any(kind in {REGISTER_EXACT, REGISTER_ALIAS} for kind in match_types)
    return BindResult(
        pack=pack.pack,
        bound=True,
        link_level="ENTITY" if has_entity else "LOB",
        entity_ids=tuple(entity_ids),
        match_types=tuple(match_types),
        canonical_lob=canonical_lob,
        unbound_reason=None,
        evidence_spans=tuple(dict.fromkeys(evidence))[:MAX_EVIDENCE],
        entity_names=tuple(entity_names),
    )


def _unbound(pack: OntologyPack, reason: str) -> BindResult:
    return BindResult(pack.pack, False, None, (), (), None, reason, ())
