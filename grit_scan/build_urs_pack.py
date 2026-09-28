"""Build the Urban Solutions reader pack from URS Entity Info.xlsx.

The workbook is the source. A missing rating, domain, project, site, or
Layer 1 condition stays absent. The Taiwan baseline is not copied.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from grit_scan.lob_keyword_loader import load_lob_keywords
from grit_scan.registry_loader import slugify
from grit_scan.xlsx_reader import read_worksheet

_WORKBOOK = "URS Entity Info.xlsx"
_FILES = {
    "source_register": "URS-Source-Register.json",
    "exposure": "URS-Exposure-Ontology.json",
    "lob_profiles": "URS-LOB-Profiles.json",
    "themes": "URS-STE-Themes.json",
    "baseline": "URS-Global-Baseline.json",
}


def build_urs_pack(workbook_path: str | Path, output_dir: str | Path) -> dict[str, Path]:
    workbook = Path(workbook_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "source_register": _sources(workbook),
        "exposure": _exposure(workbook),
        "lob_profiles": _lobs(workbook),
        "themes": _themes(workbook),
        "baseline": _baseline(),
    }
    written: dict[str, Path] = {}
    for key, name in _FILES.items():
        path = output / name
        path.write_text(json.dumps(documents[key], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written[key] = path
    return written


def _sources(workbook: Path) -> dict:
    rows = read_worksheet(workbook, "News")
    sources: list[dict] = []
    seen: set[str] = set()
    skipped_without_url: list[str] = []
    for row in rows[1:]:
        name = _clean(row[0] if row else None)
        link = _clean(row[1] if len(row) > 1 else None)
        if not name or _key(name) in {"source", "organisation", "organization"}:
            continue
        domain = _domain(link)
        if domain is None:
            if name not in skipped_without_url:
                skipped_without_url.append(name)
            continue
        if domain in seen:
            continue
        seen.add(domain)
        sources.append(
            {
                "sourceName": name,
                "sourceDomain": domain,
                "sourceUrl": link,
                "sourceClass": None,
                "credibilityRating": "UNRATED",
                "active": True,
            }
        )
    return {
        "registerId": "URS-NEWS-OUTLETS",
        "scope": "outlets named with a URL on the News sheet",
        "ratingStatus": "UNRATED",
        "workbook": _WORKBOOK,
        "sheet": "News",
        "absence": (
            "The News sheet names outlets. It does not assign credibility ratings, "
            "and rows without a URL were not given an invented domain. "
            "This is not the company source-credibility register."
        ),
        "skippedWithoutUrl": skipped_without_url,
        "sources": sources,
    }


def _exposure(workbook: Path) -> dict:
    sheet_rows = _sheet_dicts(workbook, "2. Entity Address")
    lob_names = {keyword.lob for keyword in load_lob_keywords(workbook)}
    lob_names.update(_value(row, "LOB1") for row in sheet_rows if _value(row, "NameOfEntity") and _value(row, "LOB1"))
    entities = []
    seen: set[str] = set()
    shifted_names = 0
    for row in sheet_rows:
        name = _value(row, "NameOfEntity")
        lob = _value(row, "LOB1")
        if not name and lob and lob not in lob_names:
            name = lob
            lob = None
            shifted_names += 1
        if not name or name in seen:
            continue
        seen.add(name)
        entities.append(
            {
                "entityId": f"ENTITY-{slugify(name)}",
                "entityName": name,
                "businessArea": _value(row, "BusinessArea"),
                "lobGrouping": _value(row, "LOBGrouping1"),
                "lob": lob,
                "countryOfIncorporation": _value(row, "CountryOfIncorporation"),
                "aliases": [name],
            }
        )
    customers = []
    seen.clear()
    for row in read_worksheet(workbook, "4. Customers"):
        if len(row) < 2 or not _clean(row[0]).isdigit():
            continue
        name = _clean(row[1])
        if not name or name in seen:
            continue
        seen.add(name)
        customers.append({"customerId": f"CUSTOMER-{slugify(name)}", "customerName": name, "aliases": [name]})
    suppliers = []
    seen.clear()
    supplier_rows = read_worksheet(workbook, "5. Suppliers")
    header_index = next(
        (
            index
            for index, row in enumerate(supplier_rows)
            if _key(row[0] if row else None) == "name" and _key(row[1] if len(row) > 1 else None) == "ba"
        ),
        None,
    )
    if header_index is None:
        raise ValueError("Suppliers sheet is missing its Name/BA header row.")
    headers = [_key(value) for value in supplier_rows[header_index]]
    for values in supplier_rows[header_index + 1 :]:
        row = dict(zip(headers, values, strict=False))
        name = _value(row, "Name")
        if not name or name in seen:
            continue
        seen.add(name)
        suppliers.append(
            {
                "supplierId": f"SUPPLIER-{slugify(name)}",
                "supplierName": name,
                "businessArea": _value(row, "BA"),
                "aliases": [name],
            }
        )
    _require_unique_ids(entities, "entityId")
    _require_unique_ids(customers, "customerId")
    _require_unique_ids(suppliers, "supplierId")
    if not entities or not customers or not suppliers:
        raise ValueError("Exposure pack is missing entities, customers, or suppliers from the workbook.")
    return {
        "registerId": "URS-EXPOSURE",
        "workbook": _WORKBOOK,
        "absence": {
            "projects": "URS Entity Info.xlsx has no project sheet. No project was invented.",
            "sites": "Registered addresses were not promoted to site aliases. A repeated legal name was not split into a second site.",
        },
        "nameColumnNote": (
            f"{shifted_names} rows leave NameOfEntity blank and put the legal name in LOB1. "
            "Those names are entities, not lines of business."
        ),
        "entities": entities,
        "projects": [],
        "customers": customers,
        "suppliers": suppliers,
        "sites": [],
    }


def _lobs(workbook: Path) -> dict:
    entity_lobs: dict[str, dict[str, str | None]] = {}
    for row in _sheet_dicts(workbook, "2. Entity Address"):
        lob = _value(row, "LOB1")
        if not lob or lob in entity_lobs:
            continue
        entity_lobs[lob] = {
            "businessArea": _value(row, "BusinessArea"),
            "lobGrouping": _value(row, "LOBGrouping1"),
        }
    grouped: dict[str, dict[str, list[str]]] = {}
    for keyword in load_lob_keywords(workbook):
        bucket = grouped.setdefault(keyword.lob, {"KEY_TIER_2": [], "EMERGING": []})
        tier = keyword.tier.name
        if keyword.keyword not in bucket[tier]:
            bucket[tier].append(keyword.keyword)
    lobs = []
    for name, tiers in grouped.items():
        spelled = name.replace("Heatlhcare", "Healthcare")
        entity = entity_lobs.get(name) or entity_lobs.get(spelled) or {}
        keywords = [*tiers["KEY_TIER_2"], *tiers["EMERGING"]]
        record = {
            "lobId": f"LOB-{slugify(name)}",
            "lobName": name,
            "baName": entity.get("businessArea"),
            "lobGrouping": entity.get("lobGrouping"),
            "active": True,
            "keywords": keywords,
            "keywordTiers": tiers,
            "priorityCountries": [],
        }
        if spelled != name and spelled in entity_lobs and spelled not in keywords:
            record["keywords"] = [*keywords, spelled]
            record["spellingNote"] = f"Sheet 6 spells this LOB '{name}'. Sheet 2 spells it '{spelled}'."
        lobs.append(record)
    if not lobs:
        raise ValueError("No LOB profiles could be built from sheet 6.")
    return {
        "registerId": "URS-LOB-PROFILES",
        "workbook": _WORKBOOK,
        "sheet": "6. LOB_Keywords",
        "absence": "priorityCountries is empty. Country of incorporation is not a priority country, and none was invented.",
        "lobs": lobs,
    }


def _themes(workbook: Path) -> dict:
    rows = read_worksheet(workbook, "7. Annual Risks")
    sections = (
        ("KEY", "ref", "keyrisk", 6),
        ("TIER_2", "sn", "tier2risk", 5),
        ("EMERGING", "no", "emergingrisk", 3),
    )
    themes = []
    for tier, ref_key, name_key, expected in sections:
        found = _section(rows, ref_key, name_key)
        if len(found) != expected:
            raise ValueError(f"Sheet 7 {tier} section has {len(found)} rows; expected {expected}.")
        for ref, name, detail, extra in found:
            short = name.split("(", 1)[0].strip()
            keywords = [short] if short and short != name and len(short) >= 6 else []
            themes.append(
                {
                    "themeId": f"URS-{tier}-{int(ref):02d}",
                    "themeName": name,
                    "tier": tier,
                    "keywords": keywords,
                    "sheetDetail": detail or None,
                    "sheetExtra": extra or None,
                }
            )
    return {
        "registerId": "URS-STE-THEMES",
        "workbook": _WORKBOOK,
        "sheet": "7. Annual Risks",
        "absence": "Keywords are the governed risk title, not an invented search list. Driver text is stored and is not used as a keyword.",
        "themes": themes,
    }


def _baseline() -> dict:
    return {
        "baselineId": "URS-LAYER1-ABSENT",
        "readerId": "URS",
        "readerName": "Urban Solutions",
        "revisionPrefix": "URS",
        "deltaTitle": "URS WEEKLY STRATEGIC DELTA",
        "effectiveDate": None,
        "conditions": [],
        "absence": (
            "No governed Layer 1 condition set exists for Urban Solutions. "
            "The ten Taiwan conditions belong to the urs-tw reader pack and are not copied here."
        ),
        "posture": (
            "Urban Solutions has no governed Layer 1 condition set yet. "
            "Do not treat the Taiwan baseline as the Urban Solutions picture. "
            "Entity-level action stays evidence-led."
        ),
        "idleLine": "No material change was recorded. Urban Solutions has no governed Layer 1 condition set to revise.",
    }


def _require_unique_ids(rows: list[dict], field: str) -> None:
    seen: dict[str, str] = {}
    for row in rows:
        identity = str(row[field])
        name = str(row.get("entityName") or row.get("customerName") or row.get("supplierName") or "")
        if identity in seen and seen[identity] != name:
            raise ValueError(f"Slug collision on '{identity}': '{seen[identity]}' and '{name}'")
        seen[identity] = name


def _section(rows: list[list], ref_key: str, name_key: str) -> list[tuple[str, str, str, str]]:
    start = next(
        (
            index + 1
            for index, row in enumerate(rows)
            if _key(row[0] if row else None) == ref_key and name_key in _key(row[1] if len(row) > 1 else None)
        ),
        None,
    )
    if start is None:
        return []
    found = []
    for row in rows[start:]:
        ref = _clean(row[0] if row else None)
        if not re.fullmatch(r"\d+", ref):
            if found:
                break
            continue
        name = _clean(row[1] if len(row) > 1 else None)
        if name:
            found.append((ref, name, _clean(row[2] if len(row) > 2 else None), _clean(row[3] if len(row) > 3 else None)))
    return found


def _sheet_dicts(workbook: Path, sheet: str) -> list[dict[str, object]]:
    worksheet = read_worksheet(workbook, sheet)
    header_index = next((index for index, row in enumerate(worksheet) if any(_clean(value) for value in row)), None)
    if header_index is None:
        raise ValueError(f"Workbook sheet has no header row: {sheet}")
    headers = [_key(value) for value in worksheet[header_index]]
    return [
        dict(zip(headers, values, strict=False))
        for values in worksheet[header_index + 1 :]
        if any(_clean(value) for value in values)
    ]


def _value(row: dict[str, object], *names: str) -> str | None:
    for name in names:
        cleaned = _clean(row.get(_key(name)))
        if cleaned:
            return cleaned
    return None


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", _clean(value).casefold())


def _domain(link: str) -> str | None:
    if not link.lower().startswith(("http://", "https://")):
        return None
    host = urlparse(link).hostname
    if not host:
        return None
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _main() -> None:
    root = Path(__file__).resolve().parent.parent
    written = build_urs_pack(root / _WORKBOOK, root / "config" / "urs")
    for key, path in written.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    _main()
