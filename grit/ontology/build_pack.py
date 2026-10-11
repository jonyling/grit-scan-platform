"""Build a business-area reader pack from its Entity Info workbook.

The workbook follows the BA Entity Info template (sheets 2 to 10, News-Pri, News-Sec, and hidden LOB keyword sheets). What
differs per business area sits in the pack's pack.json under "build":

    prefix                   the id prefix, such as URS or DSC
    defaultWorkbook          the workbook's file name in secrets/
    lobHierarchy             the hand-authored LOB hierarchy file
    keywordSheets            the hidden LOB keyword sheets. Omitted: the URS sheets. []: none.
    themeCounts              rows per sheet 7 section, such as {"EMERGING": 5}. Default 6, 5 and 3.
    themeVocabulary          general vocabulary for theme names, keyed by the start of the name
    sourceSheets             a sheet's column layout, such as {"9. Specific Industry News": {"name_col": 1, "numbered": true}}
    managementFocusLayout    "requests" (URS) or "areas" (one row per focus area, DS&C)
    supplierUnit             the business area's own label in the supplier sheet's BA column, such as "DS&C"
    sharedSupplierSeparator  the separator in a BA value naming two business areas, such as "/" in "URS/DS&C"

The register file names come from the manifest's registers. The workbook is the source. A missing rating, domain, project,
site, or Layer 1 condition stays absent. An entity pack's conditions are not copied.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from grit.ontology.lob_hierarchy import resolve_lob
from grit.ontology.lob_keyword_loader import load_lob_keywords
from grit.ontology.search_inputs import load_competitors, load_keyword_library, load_management_focus
from grit.ontology.registry_loader import slugify
from grit.ontology.workbook import workbook_path as default_workbook
from grit.ontology.xlsx_reader import read_worksheet
from grit.settings import CONFIG_DIR, ROOT

GENERATED = ("source_register", "exposure", "lob_profiles", "themes", "baseline", "keyword_library", "competitors", "management_focus")


def build_pack(
    workbook_path: str | Path,
    output_dir: str | Path,
    manifest: dict,
    lob_hierarchy: str | Path | None = None,
) -> dict[str, Path]:
    """Build the generated registers named in the manifest. The LOB hierarchy is a hand-authored input and is never overwritten."""
    build = manifest.get("build") or {}
    prefix = str(build.get("prefix") or manifest.get("shortLabel") or manifest["pack"].upper())
    ba_name = str(manifest.get("businessArea") or manifest.get("label") or prefix)
    files = {**manifest["registers"], **(manifest.get("optional") or {})}
    workbook = Path(workbook_path)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    hierarchy_file = Path(lob_hierarchy) if lob_hierarchy else CONFIG_DIR / manifest["pack"] / str(build.get("lobHierarchy") or "-")
    hierarchy = json.loads(hierarchy_file.read_text(encoding="utf-8-sig")) if hierarchy_file.is_file() else None
    # A manifest without keywordSheets reads the URS keyword sheets. An empty list reads none.
    sheets = tuple(build["keywordSheets"]) if "keywordSheets" in build else None
    documents = {
        "source_register": _sources(workbook, prefix, _source_layouts(build.get("sourceSheets"))),
        "exposure": _exposure(workbook, prefix, own_label=build.get("supplierUnit"), shared_separator=build.get("sharedSupplierSeparator")),
        "lob_profiles": _lobs(workbook, hierarchy, prefix),
        "themes": _themes(workbook, prefix, counts=build.get("themeCounts"), vocabulary=build.get("themeVocabulary")),
        "baseline": _baseline(prefix, ba_name, list(manifest.get("children") or [])),
        "keyword_library": load_keyword_library(workbook, hierarchy, sheets=sheets, prefix=prefix),
        "competitors": load_competitors(workbook, hierarchy, prefix=prefix),
        "management_focus": load_management_focus(workbook, hierarchy, prefix=prefix, layout=str(build.get("managementFocusLayout") or "requests")),
    }
    written: dict[str, Path] = {}
    for key in GENERATED:
        name = files[key]
        path = output / name
        path.write_text(json.dumps(documents[key], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written[key] = path
    return written


def build_urs_pack(workbook_path: str | Path, output_dir: str | Path, lob_hierarchy: str | Path | None = None) -> dict[str, Path]:
    """The Urban Solutions pack, from config/urs/pack.json."""
    from grit.ontology.pack import manifests

    return build_pack(workbook_path, output_dir, manifests()["urs"], lob_hierarchy)


# name_col is where a source's name sits. heading_col is where a section heading sits, if the sheet has them. numbered
# strips a leading "1. " from the name. A manifest's build.sourceSheets overrides a sheet's layout.
_SOURCE_SHEETS = {
    "News-Pri": {"name_col": 2, "heading_col": 1},
    "News-Sec": {"name_col": 0, "heading_col": None},
    "9. Specific Industry News": {"name_col": 0, "heading_col": None},
}
_SOURCE_HEADERS = {"source", "organisation", "organization", "link", "focus", "keyreports"}
# A customer the workbook names only by country and programme, with no organisation name.
_ANONYMISED = re.compile(r"\bCustomer$")


def _source_layouts(overrides: dict | None) -> dict[str, dict]:
    return {sheet: {**layout, **((overrides or {}).get(sheet) or {})} for sheet, layout in _SOURCE_SHEETS.items()}


def _sources(workbook: Path, prefix: str = "URS", layouts: dict[str, dict] | None = None) -> dict:
    layouts = layouts or _SOURCE_SHEETS
    sources: list[dict] = []
    seen: set[str] = set()
    skipped_without_url: list[str] = []
    for sheet, layout in layouts.items():
        name_col, heading_col = layout["name_col"], layout["heading_col"]
        group: str | None = None
        for row in read_worksheet(workbook, sheet):
            cells = [_clean(cell) for cell in row]
            filled = [index for index, cell in enumerate(cells) if cell]
            if not filled:
                continue
            link = next((cell for cell in cells if _domain(cell)), None)
            name = cells[name_col].lstrip("-").strip() if name_col < len(cells) else ""
            if layout.get("numbered"):
                name = re.sub(r"^\d+\.\s+", "", name)
            if link is None:
                if heading_col is not None and filled == [heading_col]:
                    group = cells[heading_col]
                elif name and _key(name) not in _SOURCE_HEADERS and (heading_col is not None or len(filled) > 1):
                    if name not in skipped_without_url:
                        skipped_without_url.append(name)
                continue
            domain = _domain(link)
            if not name or _key(name) in _SOURCE_HEADERS or domain in seen:
                continue
            seen.add(domain)
            focus = next((cells[index] for index in filled if index != name_col and cells[index] != link), None)
            source = {
                "sourceName": name,
                "sourceDomain": domain,
                "sourceUrl": link,
                "sourceClass": None,
                "credibilityRating": "UNRATED",
                "active": True,
                "workbookSheet": sheet,
            }
            if group:
                source["group"] = group
            if focus:
                source["focus"] = focus
            sources.append(source)
    return {
        "registerId": f"{prefix}-NEWS-OUTLETS",
        "scope": "sources named with a URL on the News-Pri, News-Sec, and 9. Specific Industry News sheets",
        "ratingStatus": "UNRATED",
        "workbook": workbook.name,
        "sheets": list(_SOURCE_SHEETS),
        "absence": (
            "These sheets name sources. They do not assign credibility ratings, "
            "and rows without a URL were not given an invented domain. "
            "A source listed on more than one sheet appears once, under the first sheet read. "
            "This is not the company source-credibility register."
        ),
        "skippedWithoutUrl": skipped_without_url,
        "sources": sources,
    }


def _exposure(workbook: Path, prefix: str = "URS", *, own_label: str | None = None, shared_separator: str | None = None) -> dict:
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
                "sourceEntityId": _value(row, "EntityId"),
                "entityName": name,
                "cluster": _value(row, "Cluster"),
                "businessArea": _value(row, "BusinessArea"),
                "lobGrouping": _value(row, "LOBGrouping1"),
                "lob": lob,
                "countryOfIncorporation": _value(row, "CountryOfIncorporation"),
                "natureOfShareholding": _value(row, "NatureOfShareholding"),
                "reportingSegment": _value(row, "Reporting Segment"),
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
        customer = {"customerId": f"CUSTOMER-{slugify(name)}", "customerName": name, "aliases": [name]}
        if _ANONYMISED.search(name):
            customer["anonymised"] = True
        customers.append(customer)
    suppliers = []
    seen.clear()
    supplier_rows = read_worksheet(workbook, "5. Suppliers")
    # The name is column A and the business area column B. Column A's header is "Name" in the URS sheet and a stray
    # number in the DS&C sheet, so the header row is found by column B.
    header_index = next(
        (index for index, row in enumerate(supplier_rows) if _key(row[1] if len(row) > 1 else None) == "ba"),
        None,
    )
    if header_index is None:
        raise ValueError("Suppliers sheet is missing its Name/BA header row.")
    shared_count = 0
    for values in supplier_rows[header_index + 1 :]:
        name = _clean(values[0] if values else None)
        if not name or name in seen:
            continue
        seen.add(name)
        unit = _clean(values[1] if len(values) > 1 else None) or None
        supplier = {"supplierId": f"SUPPLIER-{slugify(name)}", "supplierName": name, "businessArea": unit, "aliases": [name]}
        parts = [part.strip() for part in unit.split(shared_separator)] if unit and shared_separator and own_label else []
        if own_label in parts and len(parts) > 1:
            supplier["businessArea"] = own_label
            supplier["sharedWith"] = [part for part in parts if part != own_label]
            shared_count += 1
        suppliers.append(supplier)
    _require_unique_ids(entities, "entityId")
    _require_unique_ids(customers, "customerId")
    _require_unique_ids(suppliers, "supplierId")
    if not entities or not customers or not suppliers:
        raise ValueError("Exposure pack is missing entities, customers, or suppliers from the workbook.")
    return {
        "registerId": f"{prefix}-EXPOSURE",
        "workbook": workbook.name,
        "absence": {
            "projects": f"{workbook.name} has no project sheet. No project was invented.",
            "sites": "Registered addresses were not promoted to site aliases. A repeated legal name was not split into a second site.",
        },
        **(
            {
                "nameColumnNote": (
                    f"{shifted_names} rows leave NameOfEntity blank and put the legal name in LOB1. "
                    "Those names are entities, not lines of business."
                )
            }
            if shifted_names
            else {}
        ),
        **(
            {
                "sharedSupplierNote": (
                    f"{shared_count} suppliers are listed for more than one business area in column B. "
                    f"They are recorded under {own_label}, and sharedWith names the other business areas."
                )
            }
            if shared_count
            else {}
        ),
        **(
            {
                "anonymisedCustomerNote": (
                    f"{sum(1 for row in customers if row.get('anonymised'))} customers are named only by country and programme, "
                    "such as 'Thailand NPE Programme Customer'. They stay in the register and are not searched by name."
                )
            }
            if any(row.get("anonymised") for row in customers)
            else {}
        ),
        "entities": entities,
        "projects": [],
        "customers": customers,
        "suppliers": suppliers,
        "sites": [],
    }


def _lobs(workbook: Path, hierarchy: dict | None = None, prefix: str = "URS") -> dict:
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
        resolved = resolve_lob(name, hierarchy)
        keywords = [*tiers["KEY_TIER_2"], *tiers["EMERGING"]]
        record = {
            "lobId": f"LOB-{slugify(name)}",
            "lobName": name,
            "canonicalLob": resolved["lobName"] if resolved else None,
            "baName": hierarchy["businessArea"] if resolved else entity.get("businessArea"),
            "lobGrouping": resolved["lobGrouping"] if resolved else entity.get("lobGrouping"),
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
        "registerId": f"{prefix}-LOB-PROFILES",
        "workbook": workbook.name,
        "sheet": "6. LOB_Keywords",
        "absence": "priorityCountries is empty. Country of incorporation is not a priority country, and none was invented.",
        "lobHierarchy": None if hierarchy is None else {"registerId": hierarchy["registerId"], "status": hierarchy["status"]},
        "lobs": lobs,
    }


# Keyed by the start of the theme's own name, so a business area whose sheet 7 orders its themes differently still gets
# the vocabulary for the right theme. A theme whose name starts with none of these keeps its title only.
# Entity-wide STE Annual Risk vocabulary, not a Taiwan baseline. Sheet 7 of
# URS Entity Info.xlsx is a single, entity-wide risk register: the same 14
# themes were reused for the Taiwan reader pack's own keyword list, which is
# why that list is safe to draw generic business-risk vocabulary from here.
# Anything specific to a country or a Layer-1 condition/development (e.g.
# "Taiwan concentration", "Taiwan rail", "maritime pressure") is left out --
# that is the Taiwan baseline, and it is still not copied. What remains is
# the same generic business-risk terms (cybersecurity, fraud, regulatory
# compliance, ...) that describe the governed theme itself, independent of
# any one reader's geography.
_GENERAL_RISK_KEYWORDS: dict[str, list[str]] = {
    "competition": ["competition", "competitor", "pricing pressure", "technology differentiation", "market access"],
    "financial volatility": [
        "financial volatility", "interest rate", "inflation", "forex", "foreign exchange",
        "credit risk", "tariff", "financing cost", "borrowing cost", "recessionary",
    ],
    "escalating costs": [
        "escalating cost", "project cost", "margin", "cost overrun", "labour cost",
        "material cost", "contractual requirement", "project risk management",
    ],
    "cybersecurity": [
        "cybersecurity", "cyber attack", "data exfiltration", "IT outage", "ransomware",
        "network security", "data privacy",
    ],
    "geopolitical impact": [
        "geopolitical", "sanctions", "export control", "regulatory scrutiny", "reputational exposure",
        "strategic competition",
    ],
    "fraud": ["fraud", "bribery", "corruption", "code of conduct", "conflict of interest"],
    "business & supply chain disruptions": ["natural disaster", "typhoon", "earthquake", "pandemic", "supply chain disruption", "flood"],
    "regulatory compliance": ["regulatory compliance", "regulation", "licensing", "compliance requirement"],
    "workplace health": ["workplace safety", "health and safety", "occupational safety"],
    "climate change": ["climate change", "climate adaptation", "esg", "green practices", "carbon", "sustainability reporting"],
    "geopolitical volatility": [
        "staff safety", "evacuation", "crisis escalation", "wartime", "act of war", "conflict zone",
        "military exercise", "civil-military",
    ],
    "emerging technologies": [
        "genai", "artificial intelligence", "smart city os", "edge ai", "emerging technology",
        "semiconductor", "ai chip", "ai-enabled", "chip export",
    ],
    "talent scarcity": ["talent scarcity", "workforce planning", "talent mapping", "skills shortage", "hiring", "labour shortage"],
}


def _general_keywords(name: str, vocabulary: dict[str, list[str]] | None = None) -> list[str]:
    """A manifest's build.themeVocabulary is read first, so a business area can add vocabulary for its own theme names."""
    lowered = name.casefold()
    for table in (vocabulary or {}, _GENERAL_RISK_KEYWORDS):
        found = next((words for start, words in table.items() if lowered.startswith(start.casefold())), None)
        if found is not None:
            return list(found)
    return []


_THEME_COUNTS = {"KEY": 6, "TIER_2": 5, "EMERGING": 3}


def _themes(workbook: Path, prefix: str = "URS", *, counts: dict[str, int] | None = None, vocabulary: dict[str, list[str]] | None = None) -> dict:
    """counts is the number of rows each sheet 7 section must have. A different count fails loudly, so a section that
    lost or gained a row is never read silently."""
    rows = read_worksheet(workbook, "7. Annual Risks")
    expected = {**_THEME_COUNTS, **(counts or {})}
    sections = (
        ("KEY", "ref", "keyrisk", expected["KEY"]),
        ("TIER_2", "sn", "tier2risk", expected["TIER_2"]),
        ("EMERGING", "no", "emergingrisk", expected["EMERGING"]),
    )
    themes = []
    for tier, ref_key, name_key, expected in sections:
        found = _section(rows, ref_key, name_key)
        if len(found) != expected:
            raise ValueError(f"Sheet 7 {tier} section has {len(found)} rows; expected {expected}.")
        for ref, name, detail, extra in found:
            theme_id = f"{prefix}-{tier}-{int(ref):02d}"
            short = name.split("(", 1)[0].strip()
            keywords = [short] if short and short != name and len(short) >= 6 else []
            seen_lower = {k.lower() for k in keywords}
            for keyword in _general_keywords(name, vocabulary):
                if keyword.lower() not in seen_lower:
                    keywords.append(keyword)
                    seen_lower.add(keyword.lower())
            themes.append(
                {
                    "themeId": theme_id,
                    "themeName": name,
                    "tier": tier,
                    "keywords": keywords,
                    "sheetDetail": detail or None,
                    "sheetExtra": extra or None,
                }
            )
    return {
        "registerId": f"{prefix}-STE-THEMES",
        "workbook": workbook.name,
        "sheet": "7. Annual Risks",
        "absence": (
            "Keywords beyond the governed risk title are generic business-risk vocabulary "
            "for the same entity-wide theme (see _GENERAL_RISK_KEYWORDS), not per-condition "
            "prose. Country- or condition-specific terms -- the Taiwan Layer-1 baseline -- "
            "are still not copied. Driver text is stored and is not used as a keyword."
        ),
        "themes": themes,
    }


def _baseline(prefix: str = "URS", ba_name: str = "Urban Solutions", children: list[str] | None = None) -> dict:
    kept_apart = f" The entity packs' conditions ({', '.join(children)}) belong to those packs and are not copied here." if children else ""
    return {
        "baselineId": f"{prefix}-LAYER1-ABSENT",
        "readerId": prefix,
        "readerName": ba_name,
        "revisionPrefix": prefix,
        "deltaTitle": f"{prefix} WEEKLY STRATEGIC DELTA",
        "effectiveDate": None,
        "conditions": [],
        "absence": f"No governed Layer 1 condition set exists for {ba_name}. Its weekly brief stands on the business area's risk themes.{kept_apart}",
        "posture": (
            f"{ba_name} has no governed Layer 1 condition set yet. Do not treat an entity pack's conditions as the {ba_name} picture. "
            "Entity-level action stays evidence-led."
        ),
        "idleLine": f"No material change was recorded. {ba_name} has no governed Layer 1 condition set to revise.",
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
    import argparse

    from grit.ontology.pack import manifests

    known = {name: manifest for name, manifest in manifests().items() if manifest.get("build")}
    parser = argparse.ArgumentParser(description="Rebuild a business-area pack's generated registers from its Entity Info workbook.")
    parser.add_argument("--pack", default="urs", choices=sorted(known))
    parser.add_argument("--workbook", type=Path, help="Defaults to GRIT_WORKBOOK, then secrets/ and the manifest's defaultWorkbook.")
    args = parser.parse_args()
    manifest = known[args.pack]
    build = manifest.get("build") or {}
    workbook = args.workbook or default_workbook(str(build.get("defaultWorkbook") or "") or None, prefix=build.get("prefix"))
    written = build_pack(workbook, ROOT / "config" / args.pack, manifest)
    for key, path in written.items():
        print(f"{key}: {path}")


if __name__ == "__main__":
    _main()
