"""Bind free text to the URS exposure ontology. No inference, one declared hop.

Legal entities are a separate collection. They are not relabelled as sites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from grit_scan.matchers.text_norm import normalise_exposure, standalone_pattern

_COLLECTIONS = (
    ("ENTITY", "entities", "entityId", "entityName"),
    ("PROJECT", "projects", "projectId", "projectName"),
    ("CUSTOMER", "customers", "customerId", "customerName"),
    ("SUPPLIER", "suppliers", "supplierId", "supplierName"),
    ("SITE", "sites", "siteId", "siteName"),
)
_LINK_FIELDS = (
    ("linkedProjectIds", "PROJECT"),
    ("linkedCustomerIds", "CUSTOMER"),
    ("linkedSiteIds", "SITE"),
)
_ID_FIELD = {kind: id_field for kind, _collection, id_field, _name in _COLLECTIONS}
_NAME_FIELD = {kind: name_field for kind, _collection, _id, name_field in _COLLECTIONS}
_COLLECTION = {kind: collection for kind, collection, _id, _name in _COLLECTIONS}


def exposure_entity_match(text: str, register: dict[str, Any] | Path | str) -> dict[str, Any]:
    payload = _load(register)
    if not text or not text.strip():
        return {"matchedEntities": [], "matchCount": 0}

    normalised = normalise_exposure(text)
    matched: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in _alias_index(payload):
        if not row["matchable"]:
            continue
        key = f"{row['entityType']}|{row['entityId']}"
        if key in seen:
            continue
        if standalone_pattern(row["normalisedAlias"]).search(normalised):
            seen.add(key)
            matched.append(
                {
                    "entityType": row["entityType"],
                    "entityId": row["entityId"],
                    "entityName": row["entityName"],
                    "matchedAlias": row["aliasText"],
                    "matchMethod": "WORD_BOUNDARY_ALIAS",
                    "linkedExposure": _linked(payload, row["entity"]),
                }
            )
    return {"matchedEntities": matched, "matchCount": len(matched)}


def _alias_index(register: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entity_type, collection, id_field, name_field in _COLLECTIONS:
        for entity in list(register.get(collection) or []):
            for alias_raw in list(entity.get("aliases") or []):
                matchable = True
                if isinstance(alias_raw, str):
                    alias_text = alias_raw
                elif isinstance(alias_raw, dict):
                    alias_text = str(alias_raw.get("text") or "")
                    if "matchable" in alias_raw:
                        matchable = bool(alias_raw.get("matchable"))
                else:
                    continue
                if not alias_text.strip():
                    continue
                normalised_alias = normalise_exposure(alias_text)
                if len(normalised_alias) < 4:
                    matchable = False
                rows.append(
                    {
                        "entityType": entity_type,
                        "entityId": str(entity.get(id_field) or ""),
                        "entityName": str(entity.get(name_field) or ""),
                        "aliasText": alias_text,
                        "normalisedAlias": normalised_alias,
                        "matchable": matchable,
                        "entity": entity,
                    }
                )
    return rows


def _entity_by_id(register: dict[str, Any], entity_type: str, entity_id: str) -> dict[str, Any] | None:
    for entity in list(register.get(_COLLECTION[entity_type]) or []):
        if str(entity.get(_ID_FIELD[entity_type]) or "") == entity_id:
            return entity
    return None


def _linked(register: dict[str, Any], entity: dict[str, Any]) -> list[dict[str, str]]:
    linked: list[dict[str, str]] = []
    for field, target_type in _LINK_FIELDS:
        for linked_id in list(entity.get(field) or []):
            if not str(linked_id).strip():
                continue
            target = _entity_by_id(register, target_type, str(linked_id))
            if target is None:
                continue
            linked.append(
                {
                    "entityType": target_type,
                    "entityId": str(linked_id),
                    "entityName": str(target.get(_NAME_FIELD[target_type]) or ""),
                }
            )
    return linked


def _load(register: dict[str, Any] | Path | str) -> dict[str, Any]:
    if isinstance(register, dict):
        return register
    return json.loads(Path(register).read_text(encoding="utf-8-sig"))
