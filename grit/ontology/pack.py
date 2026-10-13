"""Reader ontology packs. Registers are loaded. They are not invented.

A pack is a folder under config/ with a pack.json manifest: its level (GROUP, BA, or ENTITY), its parent and children,
and the register files it loads. ``urs`` is Urban Solutions, a business-area pack built from the URS Entity Info workbook.
``urs-tw`` is URS Taiwan, an entity pack with ten standing conditions and named projects. Taiwan is layer 5, not the whole
ontology.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from grit.settings import CONFIG_DIR

MANIFEST = "pack.json"
LEVELS = ("ENTITY", "BA", "GROUP")


def manifests(config_dir: Path = CONFIG_DIR) -> dict[str, dict[str, Any]]:
    """Every pack under config/ that has a pack.json. A pack is a folder of registers plus this manifest, so adding a
    business area or an entity is a data change."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(config_dir).glob(f"*/{MANIFEST}")):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("level") not in LEVELS:
            raise ValueError(f"{path}: level must be one of {', '.join(LEVELS)}")
        if manifest["pack"] != path.parent.name:
            raise ValueError(f"{path}: pack '{manifest['pack']}' must match its folder name '{path.parent.name}'")
        found[manifest["pack"]] = manifest
    return found


PACKS = {name: manifest["registers"] for name, manifest in manifests().items()}


@dataclass(frozen=True)
class OntologyPack:
    source_register: dict[str, Any]
    exposure: dict[str, Any]
    lob_profiles: dict[str, Any]
    themes: dict[str, Any]
    baseline: dict[str, Any]
    config_dir: Path
    pack: str = "urs-tw"
    lob_hierarchy: dict[str, Any] | None = None
    keyword_library: dict[str, Any] | None = None
    competitors: dict[str, Any] | None = None
    management_focus: dict[str, Any] | None = None
    relationships: dict[str, Any] | None = None
    scan_plan: dict[str, Any] | None = None
    order_book: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None

    @property
    def level(self) -> str:
        return (self.manifest or {}).get("level") or "BA"

    @property
    def label(self) -> str:
        return (self.manifest or {}).get("label") or self.pack

    @property
    def children(self) -> list[str]:
        return list((self.manifest or {}).get("children") or [])


def _read(path: Path) -> Any:
    """A register file. A JSON error names the file, so a hand edit that broke it is easy to find."""
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.") from exc


def load_ontology_pack(config_dir: Path, pack: str = "urs-tw") -> OntologyPack:
    known = manifests(config_dir)
    if pack not in known:
        raise ValueError(f"Unknown ontology pack '{pack}'. Known packs: {', '.join(sorted(known))}")
    manifest = known[pack]
    root = Path(config_dir) / pack
    loaded: dict[str, Any] = {}
    missing: list[str] = []
    for key, name in manifest["registers"].items():
        path = root / name
        if not path.is_file():
            missing.append(name)
            continue
        loaded[key] = _read(path)
    if missing:
        raise FileNotFoundError(f"Ontology pack '{pack}' is incomplete. Place the governed registers in {root}: {', '.join(missing)}")
    for key, name in (manifest.get("optional") or {}).items():
        path = root / name
        loaded[key] = _read(path) if path.is_file() else None
    return OntologyPack(config_dir=root, pack=pack, manifest=manifest, **loaded)


def scanned_packs(config_dir: Path = CONFIG_DIR) -> list[str]:
    """Packs with a scan plan, entity packs first, so a business-area brief can summarise its entity packs' runs."""
    found = [name for name, manifest in manifests(config_dir).items() if (Path(config_dir) / name / (manifest.get("optional") or {}).get("scan_plan", "-")).is_file()]
    return sorted(found, key=lambda name: (LEVELS.index(manifests(config_dir)[name]["level"]), name))


def _main() -> None:
    parser = argparse.ArgumentParser(description="Load one reader ontology pack and print what it actually contains.")
    parser.add_argument("--pack", default="urs", choices=sorted(manifests()))
    parser.add_argument("--config", type=Path, default=CONFIG_DIR)
    args = parser.parse_args()
    loaded = load_ontology_pack(args.config, args.pack)
    sources = list(loaded.source_register.get("sources") or [])
    exposure = loaded.exposure
    print(f"Pack: {loaded.pack} ({loaded.label}, {loaded.level} level; children: {', '.join(loaded.children) or 'none'})")
    print(f"Dir: {loaded.config_dir}")
    print(f"Sources: {len(sources)} (rated: {sum(1 for row in sources if row.get('credibilityRating') not in {None, 'UNRATED'})})")
    print(
        "Exposure: "
        f"{len(exposure.get('entities') or [])} entities, "
        f"{len(exposure.get('customers') or [])} customers, "
        f"{len(exposure.get('suppliers') or [])} suppliers, "
        f"{len(exposure.get('projects') or [])} projects, "
        f"{len(exposure.get('sites') or [])} sites"
    )
    print(f"LOBs: {len(loaded.lob_profiles.get('lobs') or [])}")
    if loaded.lob_hierarchy:
        print(f"LOB hierarchy: {len(loaded.lob_hierarchy.get('lobs') or [])} LOBs, status {loaded.lob_hierarchy.get('status')}")
    if loaded.keyword_library:
        libraries = loaded.keyword_library.get("libraries") or []
        print(f"Keyword libraries: {len(libraries)} sheets, {sum(len(item['clusters']) for item in libraries)} clusters")
    if loaded.competitors:
        print(f"Competitors: {len(loaded.competitors.get('competitors') or [])} rows")
    if loaded.management_focus:
        print(
            f"Management focus: {len(loaded.management_focus.get('requests') or [])} requests, "
            f"{len(loaded.management_focus.get('focusItems') or [])} focus items"
        )
    print(f"Themes: {len(loaded.themes.get('themes') or [])}")
    print(f"Baseline conditions: {len(loaded.baseline.get('conditions') or [])}")
    if loaded.baseline.get("absence"):
        print(f"Baseline: {loaded.baseline['absence']}")
    if loaded.source_register.get("absence"):
        print(f"Sources: {loaded.source_register['absence']}")


if __name__ == "__main__":
    _main()
