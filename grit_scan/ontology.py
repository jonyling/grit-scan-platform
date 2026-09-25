"""URS ontology pack. Registers are loaded. They are not invented."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGISTER_FILES = {
    "source_register": "URS-TW-Source-Register.json",
    "exposure": "URS-TW-Exposure-Ontology.json",
    "lob_profiles": "URS-TW-LOB-Profiles.json",
    "themes": "URS-TW-STE-Annual-Risks.json",
    "baseline": "URS-TW-Global-Baseline.json",
}


@dataclass(frozen=True)
class OntologyPack:
    source_register: dict[str, Any]
    exposure: dict[str, Any]
    lob_profiles: dict[str, Any]
    themes: dict[str, Any]
    baseline: dict[str, Any]
    config_dir: Path


def load_ontology_pack(config_dir: Path) -> OntologyPack:
    loaded: dict[str, Any] = {}
    missing: list[str] = []
    for key, name in REGISTER_FILES.items():
        path = config_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        loaded[key] = json.loads(path.read_text(encoding="utf-8-sig"))
    if missing:
        raise FileNotFoundError(
            "URS ontology pack is incomplete. Place the governed registers in "
            f"{config_dir}: {', '.join(missing)}"
        )
    return OntologyPack(config_dir=config_dir, **loaded)
