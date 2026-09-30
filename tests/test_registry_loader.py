from pathlib import Path

from grit_scan.impact_scoring import RegistryKind
from grit_scan.registry_loader import load_transmission_registry, slugify


ROOT = Path(__file__).parents[1]


def test_workbook_entries_are_primary_and_aliases_are_overlays() -> None:
    registry = load_transmission_registry(
        ROOT / "secrets" / "URS Entity Info.xlsx",
        ROOT / "config" / "registry_aliases.json",
    )
    counts = {kind: sum(entry.registry_kind is kind for entry in registry.entries) for kind in RegistryKind}
    assert counts[RegistryKind.ENTITY] == 32
    assert counts[RegistryKind.CUSTOMER] == 20
    assert counts[RegistryKind.SUPPLIER] == 15
    assert counts[RegistryKind.PROJECT] == 0

    by_id = {entry.entity_id: entry for entry in registry.entries}
    assert by_id["ENTITY-st-engineering-taiwan-ltd"].canonical_name == "ST Engineering (Taiwan) Ltd."
    assert by_id["SUPPLIER-leonardo-s-p-a"].canonical_name == "LEONARDO S.P.A."
    # These names live in LOB1, not NameOfEntity, on sheet "2. Entity Address".
    assert by_id["ENTITY-tlp-holdings-llc"].canonical_name == "TLP Holdings, LLC"
    # 33 address rows for TransCore, LP - Dubai collapse into one entity, not two.
    assert by_id["ENTITY-transcore-lp-dubai"].canonical_name == "TransCore, LP - Dubai"

    entry_ids = set(by_id)
    assert all(alias.entity_id in entry_ids for alias in registry.aliases)
    assert any(alias.alias == "STE Urban Solutions Taiwan" for alias in registry.aliases)
    assert len(registry.snapshot_hash) == 64
    assert len(registry.lob_keywords) > 0


def test_entity_ids_are_slug_derived_not_row_position_derived() -> None:
    assert slugify("LEONARDO S.P.A.") == "leonardo-s-p-a"
    assert slugify("L&S Electric Co., Ltd.") == "l-s-electric-co-ltd"
    # The same canonical name always yields the same id, independent of row order.
    assert slugify("Tatung System Technologies Inc.") == slugify("Tatung System Technologies Inc.")


def test_snapshot_hash_is_content_derived_and_order_independent() -> None:
    """Same fix as the severity-definition version: hash content, not file
    bytes -- an .xlsx no-op re-save changes zip-embedded timestamps without
    changing content."""
    from grit_scan.impact_scoring import AliasRecord, RegistryEntry
    from grit_scan.registry_loader import _canonical_snapshot_hash

    entry_a = RegistryEntry(entity_id="SUPPLIER-a", canonical_name="A Corp", registry_kind=RegistryKind.SUPPLIER, ba=None, country=None)
    entry_b = RegistryEntry(entity_id="SUPPLIER-b", canonical_name="B Corp", registry_kind=RegistryKind.SUPPLIER, ba=None, country=None)
    alias = AliasRecord(entity_id="SUPPLIER-a", alias="A")

    forward = _canonical_snapshot_hash((entry_a, entry_b), (alias,), ())
    reversed_order = _canonical_snapshot_hash((entry_b, entry_a), (alias,), ())
    assert forward == reversed_order

    changed = _canonical_snapshot_hash((entry_a,), (alias,), ())
    assert changed != forward


def test_slug_collision_between_distinct_canonical_names_fails_loudly() -> None:
    from grit_scan.registry_loader import _add_entry
    from grit_scan.impact_scoring import RegistryKind

    entries: list = []
    seen: dict[str, str] = {}
    _add_entry(entries, seen, RegistryKind.SUPPLIER, "Acme, Inc.", None, None)
    try:
        _add_entry(entries, seen, RegistryKind.SUPPLIER, "Acme Inc", None, None)
    except ValueError as error:
        assert "collision" in str(error).lower()
    else:
        raise AssertionError("Expected a ValueError on slug collision between distinct canonical names.")
