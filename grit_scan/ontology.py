"""Reader ontology packs. Registers are loaded. They are not invented.

``urs`` is Urban Solutions, built from URS Entity Info.xlsx.
``urs-tw`` is the Taiwan reader pack. Taiwan is layer 5, not the whole ontology.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKS = {
    "urs-tw": {
        "source_register": "URS-TW-Source-Register.json",
        "exposure": "URS-TW-Exposure-Ontology.json",
        "lob_profiles": "URS-TW-LOB-Profiles.json",
        "themes": "URS-TW-STE-Annual-Risks.json",
        "baseline": "URS-TW-Global-Baseline.json",
    },
    "urs": {
        "source_register": "URS-Source-Register.json",
        "exposure": "URS-Exposure-Ontology.json",
        "lob_profiles": "URS-LOB-Profiles.json",
        "themes": "URS-STE-Themes.json",
        "baseline": "URS-Global-Baseline.json",
    },
}
REGISTER_FILES = PACKS["urs-tw"]


@dataclass(frozen=True)
class OntologyPack:
    source_register: dict[str, Any]
    exposure: dict[str, Any]
    lob_profiles: dict[str, Any]
    themes: dict[str, Any]
    baseline: dict[str, Any]
    config_dir: Path
    pack: str = "urs-tw"


def load_ontology_pack(config_dir: Path, pack: str = "urs-tw") -> OntologyPack:
    try:
        files = PACKS[pack]
    except KeyError:
        known = ", ".join(sorted(PACKS))
        raise ValueError(f"Unknown ontology pack '{pack}'. Known packs: {known}") from None
    root = Path(config_dir) / pack
    if not root.is_dir():
        root = Path(config_dir)
    loaded: dict[str, Any] = {}
    missing: list[str] = []
    for key, name in files.items():
        path = root / name
        if not path.is_file():
            missing.append(name)
            continue
        loaded[key] = json.loads(path.read_text(encoding="utf-8-sig"))
    if missing:
        raise FileNotFoundError(
            f"Ontology pack '{pack}' is incomplete. Place the governed registers in "
            f"{root}: {', '.join(missing)}"
        )
    return OntologyPack(config_dir=root, pack=pack, **loaded)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Load one reader ontology pack and print what it actually contains.")
    parser.add_argument("--pack", default="urs", choices=sorted(PACKS))
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent / "config")
    args = parser.parse_args()
    loaded = load_ontology_pack(args.config, args.pack)
    sources = list(loaded.source_register.get("sources") or [])
    exposure = loaded.exposure
    print(f"Pack: {loaded.pack}")
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
    print(f"Themes: {len(loaded.themes.get('themes') or [])}")
    print(f"Baseline conditions: {len(loaded.baseline.get('conditions') or [])}")
    if loaded.baseline.get("absence"):
        print(f"Baseline: {loaded.baseline['absence']}")
    if loaded.source_register.get("absence"):
        print(f"Sources: {loaded.source_register['absence']}")


if __name__ == "__main__":
    _main()
