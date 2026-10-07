"""Load canonical transmission-register entries from URS Entity Info.xlsx.

Workbook rows become primary RegistryEntry records, keyed by a slug of the
canonical name rather than row position: inserting a row upstream must not
silently re-point an existing alias at the wrong entity. Slug collisions fail
loudly rather than being disambiguated automatically.

Sheet '6. LOB_Keywords' is parsed too, via lob_keyword_loader, and folded
into the same TransmissionRegistry so resolve_transmission's SECTOR/
PERIMETER rungs are driven by the governed keyword taxonomy, not a guess.

``TransmissionRegistry.snapshot_hash`` is derived from the seeded entries,
aliases, and keywords themselves, not from the workbook's raw file bytes: an
.xlsx is a zip container with embedded per-part timestamps, so a no-op
re-save changes the file bytes without changing any content, and the
reverse (silently) is not guaranteed either.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from grit.core.impact_scoring import AliasRecord, LobKeyword, NullProjectRegistry, RegistryEntry, RegistryKind, TransmissionRegistry
from grit.ontology.lob_keyword_loader import load_lob_keywords
from grit.ontology.xlsx_reader import read_worksheet


def _clean_text(value: object) -> str:
    """Normalise non-breaking spaces and stray whitespace from workbook cells."""
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _normalise_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean_text(value).casefold())


def _value(row: dict[str, object], *names: str) -> str | None:
    for name in names:
        raw = row.get(_normalise_header(name))
        cleaned = _clean_text(raw)
        if cleaned:
            return cleaned
    return None


def slugify(name: str) -> str:
    """Derive a stable, human-legible id fragment from a canonical name."""
    slug = re.sub(r"[^a-z0-9]+", "-", _clean_text(name).casefold()).strip("-")
    if not slug:
        raise ValueError(f"Canonical name produces an empty slug: {name!r}")
    return slug


def _add_entry(entries: list[RegistryEntry], seen_ids: dict[str, str], kind: RegistryKind, canonical_name: str, ba: str | None, country: str | None) -> None:
    entity_id = f"{kind.value}-{slugify(canonical_name)}"
    if entity_id in seen_ids and seen_ids[entity_id] != canonical_name:
        raise ValueError(
            f"Slug collision on '{entity_id}': '{seen_ids[entity_id]}' and '{canonical_name}' "
            "produce the same entity_id. Disambiguate the canonical names before seeding."
        )
    if entity_id in seen_ids:
        return  # identical canonical name repeated in the source sheet; not an error
    seen_ids[entity_id] = canonical_name
    entries.append(RegistryEntry(entity_id=entity_id, canonical_name=canonical_name, registry_kind=kind, ba=ba, country=country))


def _sheet_rows(workbook_path: Path, sheet_name: str) -> list[dict[str, object]]:
    worksheet = read_worksheet(workbook_path, sheet_name)
    header_index = next(
        (index for index, row in enumerate(worksheet) if any(value is not None and str(value).strip() for value in row)),
        None,
    )
    if header_index is None:
        raise ValueError(f"Workbook sheet has no header row: {sheet_name}")
    headers = [_normalise_header(value) for value in worksheet[header_index]]
    result: list[dict[str, object]] = []
    for values in worksheet[header_index + 1 :]:
        if any(value is not None and str(value).strip() for value in values):
            result.append(dict(zip(headers, values, strict=False)))
    return result


def _seed_entries(workbook_path: Path) -> tuple[RegistryEntry, ...]:
    """Seed the three actual source-sheet layouts without guessing headers."""
    entries: list[RegistryEntry] = []
    seen_ids: dict[str, str] = {}

    entity_rows = _sheet_rows(workbook_path, "2. Entity Address")
    # Some rows leave NameOfEntity blank and put the legal name in LOB1 instead;
    # only treat LOB1 as a name when it never appears as a real LOB alongside a filled NameOfEntity.
    lob_names = {keyword.lob for keyword in load_lob_keywords(workbook_path)}
    lob_names.update(_value(row, "LOB1") for row in entity_rows if _value(row, "NameOfEntity") and _value(row, "LOB1"))
    for row in entity_rows:
        name = _value(row, "NameOfEntity")
        lob = _value(row, "LOB1")
        if not name and lob and lob not in lob_names:
            name = lob
        if name:
            _add_entry(entries, seen_ids, RegistryKind.ENTITY, name, _value(row, "BusinessArea"), _value(row, "CountryOfIncorporation"))

    for row in read_worksheet(workbook_path, "4. Customers"):
        if len(row) < 2 or not str(row[0] or "").strip().isdigit():
            continue
        name = _clean_text(row[1])
        if name:
            _add_entry(entries, seen_ids, RegistryKind.CUSTOMER, name, None, None)

    supplier_rows = read_worksheet(workbook_path, "5. Suppliers")
    supplier_header_index = next(
        (
            index
            for index, row in enumerate(supplier_rows)
            if _normalise_header(row[0] if row else None) == "name" and _normalise_header(row[1] if len(row) > 1 else None) == "ba"
        ),
        None,
    )
    if supplier_header_index is None:
        raise ValueError("Suppliers sheet is missing its Name/BA header row.")
    supplier_headers = [_normalise_header(value) for value in supplier_rows[supplier_header_index]]
    for values in supplier_rows[supplier_header_index + 1 :]:
        row = dict(zip(supplier_headers, values, strict=False))
        name = _value(row, "Name")
        if name:
            _add_entry(entries, seen_ids, RegistryKind.SUPPLIER, name, _value(row, "BA"), None)

    if not entries:
        raise ValueError("No canonical RegistryEntry rows could be seeded from URS Entity Info.xlsx sheets 2, 4, and 5.")
    return tuple(entries)


def _canonical_snapshot_hash(
    entries: tuple[RegistryEntry, ...], aliases: tuple[AliasRecord, ...], lob_keywords: tuple[LobKeyword, ...]
) -> str:
    """Content-derived, order-independent hash of the seeded register."""
    canonical_entries = sorted(
        [entry.entity_id, entry.canonical_name, entry.registry_kind.value, entry.ba or "", entry.country or ""]
        for entry in entries
    )
    canonical_aliases = sorted([alias.entity_id, alias.alias] for alias in aliases)
    canonical_keywords = sorted([keyword.lob, keyword.keyword, keyword.tier.value] for keyword in lob_keywords)
    payload = json.dumps(
        {"entries": canonical_entries, "aliases": canonical_aliases, "lob_keywords": canonical_keywords},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_transmission_registry(workbook_path: str | Path, aliases_path: str | Path) -> TransmissionRegistry:
    """Seed canonical records from sheets 2/4/5/6 and validate alias references."""
    workbook = Path(workbook_path)
    entries = _seed_entries(workbook)
    seen_ids = {entry.entity_id for entry in entries}
    lob_keywords = load_lob_keywords(workbook)

    alias_data: dict[str, Any] = json.loads(Path(aliases_path).read_text(encoding="utf-8-sig"))
    aliases = tuple(AliasRecord(entity_id=item["entity_id"], alias=item["alias"]) for item in alias_data.get("aliases", []))
    unknown_alias_ids = {alias.entity_id for alias in aliases} - seen_ids
    if unknown_alias_ids:
        raise ValueError(f"Aliases reference unknown entity ids: {sorted(unknown_alias_ids)}")

    return TransmissionRegistry(
        entries=entries,
        aliases=aliases,
        project_registry=NullProjectRegistry(),
        snapshot_hash=_canonical_snapshot_hash(entries, aliases, lob_keywords),
        lob_keywords=lob_keywords,
    )
