"""Build a business-area reader pack from its Entity Info workbook.

The workbook follows the BA Entity Info template (sheets 2 to 10, News-Pri, News-Sec, and hidden LOB keyword sheets). What
differs per business area sits in the pack's pack.json under "build":

    prefix                   the id prefix, such as URS or DSC
    defaultWorkbook          the workbook's file name in secrets/
    lobHierarchy             the hand-authored LOB hierarchy file
    keywordSheets            the hidden LOB keyword sheets. Omitted: the URS sheets. []: none.
    themeCounts              rows per sheet 7 section, such as {"EMERGING": 5}. Default 6, 5 and 3.
    themeVocabulary          general vocabulary for theme names, keyed by the start of the name
    themeActionPlans         true keeps each Key Risk's Action Plans column as sheetActionPlans (spec 36)
    sourceSheets             a sheet's column layout, such as {"9. Specific Industry News": {"name_col": 1, "numbered": true}}
    managementFocusLayout    "requests" (URS) or "areas" (one row per focus area, DS&C)
    supplierUnit             the business area's own label in the supplier sheet's BA column, such as "DS&C"
    sharedSupplierSeparator  the separator in a BA value naming two business areas, such as "/" in "URS/DS&C"
    entitySheet              the entity sheet, if not "2. Entity Address". Its header row is found by its NameOfEntity (or plain
                             Entity Name) column. Plain headers (Entity Name, LOB Grouping, LOB, Country, Ownership Type) are
                             read as the template's.
    assetSheet               a finance asset listing (entity, LOB grouping, country per asset), such as CA's
                             "2. Asset Listing (Finance)". It adds assetCountries to each entity, and adds an entity only it names.
    themeLayout              "sections" (default: Key, Tier-2 and Emerging tables) or "classified" (Marine: one 'Risk Theme |
                             Rationale | Classification' table, the tier read from the first tier the classification names)
    themeClassificationRequire  with "classified": only rows whose classification contains this text become themes, such as
                             "BA Annual Risk Refresh". The others are listed under draftThemes, not scanned.
    classRows                {"customers": regex, "suppliers": regex}. A row whose cells match names a class of counterparty,
                             such as "Marine equipment vendors", not an organisation. It stays in the register, marked
                             counterpartyClass, and is not searched by name.

Customer and supplier sheets are read by their header row. Beyond the name, a column the sheet has is copied: short name
(match key), country, customer type, sector, primary LOB, workscope, contracting entity, customer group, category, goods or
services, concentration flag, linked risk codes, and basis or confidence. A short name also becomes an alias.

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
from grit.ontology.registry_loader import entity_sheet_rows, slugify, supplier_header
from grit.ontology.workbook import workbook_path as default_workbook
from grit.ontology.xlsx_reader import read_worksheet
from grit.settings import CONFIG_DIR, ROOT

ENTITY_SHEET = "2. Entity Address"
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
    classes = build.get("classRows") or {}
    documents = {
        "source_register": _sources(workbook, prefix, _source_layouts(build.get("sourceSheets"))),
        "exposure": _exposure(workbook, prefix, own_label=build.get("supplierUnit"), shared_separator=build.get("sharedSupplierSeparator"),
                              entity_sheet=str(build.get("entitySheet") or ENTITY_SHEET), asset_sheet=build.get("assetSheet"),
                              customer_class=classes.get("customers"), supplier_class=classes.get("suppliers")),
        "lob_profiles": _lobs(workbook, hierarchy, prefix, entity_sheet=str(build.get("entitySheet") or ENTITY_SHEET)),
        "themes": _themes(workbook, prefix, counts=build.get("themeCounts"), vocabulary=build.get("themeVocabulary"), action_plans=bool(build.get("themeActionPlans")),
                          layout=str(build.get("themeLayout") or "sections"), require=build.get("themeClassificationRequire")),
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


# Column headers (by key, see _key) mapped to the register field they fill. A header matches when its key starts with the
# pattern. The first matching pattern wins, so a longer pattern is listed before a shorter one.
_COUNTERPARTY_FIELDS: tuple[tuple[str, str], ...] = (
    ("shortname", "shortName"),
    ("hqcountry", "country"), ("suppliercountry", "country"), ("countryofincorporation", "country"), ("countryregion", "country"),
    ("parentgrouphqcountry", "parentCountry"), ("spheracountry", "spheraCountry"), ("country", "country"),
    ("sectorcustomertype", "sector"), ("customertype", "customerType"), ("sector", "sector"),
    ("customergroup", "customerGroup"), ("suppliercategory", "category"), ("indicativegoods", "goodsServices"),
    ("primary", "lob"), ("indicativedsclob", "lob"), ("indicativeursworkscope", "workscope"),
    ("likely", "contractingEntity"), ("entityname", "contractingEntity"),
    ("geopolitical", "concentrationFlag"), ("alsoacompetitor", "alsoCompetitor"),
    ("linkedriskcodes", "linkedRiskCodes"), ("basisconfidence", "confidence"), ("datastatus", "confidence"),
    ("publicreference", "reference"), ("spheraibu", "ibu"), ("ibu", "ibu"),
    ("validationstatus", "confidence"), ("dependency", "dependency"), ("engagement", "engagement"),
)
_LIST_FIELDS = {"linkedRiskCodes"}
_CUSTOMER_NAMES = ("customer", "existingcustomers", "harmonisedcustomername", "customername")
_SERIALS = {"", "#", "sn", "no", "ref"}


def _counterparty_columns(header: list) -> dict[int, str]:
    columns: dict[int, str] = {}
    for index, cell in enumerate(header):
        key = _key(cell)
        field = next((name for pattern, name in _COUNTERPARTY_FIELDS if key.startswith(pattern)), None)
        if field and field not in columns.values():
            columns[index] = field
    return columns


def _counterparty_details(values: list, columns: dict[int, str], name: str) -> dict:
    """The stated columns for one customer or supplier. A blank cell, 'N/A' and a dash stay absent."""
    details: dict[str, object] = {}
    for index, field in columns.items():
        text = _clean(values[index] if index < len(values) else None)
        if not text or text.casefold() in {"n/a", "na", "-", "none", "nil"}:
            continue
        details[field] = [part.strip() for part in re.split(r"[;,]", text) if part.strip()] if field in _LIST_FIELDS else text
    if details.get("country") and details.get("spheraCountry") == details["country"]:
        details.pop("spheraCountry")
    short = str(details.get("shortName") or "")
    aliases = [name]
    if short and short.casefold() != name.casefold() and len(short) >= 3:
        aliases.append(short)
    return {"aliases": aliases, **{key: value for key, value in details.items() if key != "shortName"}}


def _customers(workbook: Path, class_pattern: str | None = None) -> list[dict]:
    """Customers from sheet 4. A sheet with a serial column (URS, DS&C) keeps numbered rows only. A sheet without one
    (CA's harmonised customer list) keeps every named row. A name listed twice is kept once. A row matching class_pattern
    names a class of customer and is marked counterpartyClass."""
    rows = read_worksheet(workbook, "4. Customers")
    header_index = name_col = None
    for index, row in enumerate(rows):
        found = next((col for col, cell in enumerate(row) if _key(cell).startswith(_CUSTOMER_NAMES)), None)
        if found is not None:
            header_index, name_col = index, found
            break
    if header_index is None:  # the template layout: a serial number in column A and the name in column B
        header, name_col, numbered, body = [], 1, True, rows
    else:
        header = rows[header_index]
        numbered = name_col > 0 and _key(header[0]) in _SERIALS
        body = rows[header_index + 1:]
    columns = {index: field for index, field in _counterparty_columns(header).items() if index != name_col}
    customers, seen = [], set()
    for row in body:
        if numbered and not _clean(row[0] if row else None).isdigit():
            continue
        name = _clean(row[name_col] if len(row) > name_col else None)
        if not name or name in seen:
            continue
        seen.add(name)
        customer = {"customerId": f"CUSTOMER-{slugify(name)}", "customerName": name, **_counterparty_details(row, columns, name)}
        if _ANONYMISED.search(name):
            customer["anonymised"] = True
        if _is_class_row(row, class_pattern):
            customer["counterpartyClass"] = True
        customers.append(customer)
    return customers


def _is_class_row(row: list, pattern: str | None) -> bool:
    return bool(pattern) and re.search(str(pattern), " | ".join(_clean(cell) for cell in row), re.I) is not None


def _entity_rows(workbook: Path, sheet: str) -> list[dict[str, object]]:
    """The entity sheet's rows, keyed by template header (see entity_sheet_rows)."""
    return entity_sheet_rows(workbook, sheet)


def _assets(workbook: Path, sheet: str) -> dict[str, dict[str, object]]:
    """Entity name -> its LOB grouping and the countries of its listed assets, from a finance asset listing."""
    found: dict[str, dict[str, object]] = {}
    for row in _sheet_dicts(workbook, sheet):
        name = _value(row, "Entity Name", "NameOfEntity")
        if not name:
            continue
        slot = found.setdefault(name, {"lobGrouping": _value(row, "LOB Grp", "LOBGrouping1"), "countries": [], "assets": 0})
        slot["assets"] = int(slot["assets"]) + 1
        country = _value(row, "Country")
        if country and country not in slot["countries"]:
            slot["countries"].append(country)
    return found


def _exposure(
    workbook: Path, prefix: str = "URS", *, own_label: str | None = None, shared_separator: str | None = None,
    entity_sheet: str = ENTITY_SHEET, asset_sheet: str | None = None, customer_class: str | None = None, supplier_class: str | None = None,
) -> dict:
    sheet_rows = _entity_rows(workbook, entity_sheet)
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
    asset_only = 0
    if asset_sheet:
        assets = _assets(workbook, asset_sheet)
        by_key = {_key(row["entityName"]): row for row in entities}
        for name, asset in assets.items():
            row = by_key.get(_key(name))
            if row is None:
                row = {"entityId": f"ENTITY-{slugify(name)}", "sourceEntityId": None, "entityName": name, "cluster": None, "businessArea": None,
                       "lobGrouping": asset["lobGrouping"], "lob": None, "countryOfIncorporation": None, "natureOfShareholding": None,
                       "reportingSegment": None, "aliases": [name], "source": asset_sheet}
                entities.append(row)
                by_key[_key(name)] = row
                asset_only += 1
            row["assetCountries"] = list(asset["countries"])
            row["assetCount"] = asset["assets"]
    customers = _customers(workbook, customer_class)
    suppliers = []
    seen.clear()
    supplier_rows = read_worksheet(workbook, "5. Suppliers")
    header_index, has_unit = supplier_header(supplier_rows)
    first_detail = 2 if has_unit else 1
    supplier_columns = {index: field for index, field in _counterparty_columns(supplier_rows[header_index]).items() if index >= first_detail}
    shared_count = 0
    for values in supplier_rows[header_index + 1 :]:
        name = _clean(values[0] if values else None)
        if not name or name in seen:
            continue
        seen.add(name)
        unit = (_clean(values[1] if len(values) > 1 else None) or None) if has_unit else None
        supplier = {"supplierId": f"SUPPLIER-{slugify(name)}", "supplierName": name, "businessArea": unit, **_counterparty_details(values, supplier_columns, name)}
        if _is_class_row(values, supplier_class):
            supplier["counterpartyClass"] = True
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
        **(
            {
                "counterpartyClassNote": (
                    f"{sum(1 for row in [*customers, *suppliers] if row.get('counterpartyClass'))} customer and supplier rows name a "
                    "class of counterparty, such as 'Marine equipment vendors', not an organisation. They stay in the register "
                    "and are not searched by name."
                )
            }
            if any(row.get("counterpartyClass") for row in [*customers, *suppliers])
            else {}
        ),
        **(
            {
                "assetNote": (
                    f"assetCountries lists the countries of each entity's assets in '{asset_sheet}'. They are where the entity "
                    f"holds assets, not where it is incorporated. {asset_only} entities are named only in that listing."
                )
            }
            if asset_sheet
            else {}
        ),
        "entities": entities,
        "projects": [],
        "customers": customers,
        "suppliers": suppliers,
        "sites": [],
    }


def _lobs(workbook: Path, hierarchy: dict | None = None, prefix: str = "URS", *, entity_sheet: str = ENTITY_SHEET) -> dict:
    entity_lobs: dict[str, dict[str, str | None]] = {}
    for row in _entity_rows(workbook, entity_sheet):
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


def _themes(
    workbook: Path, prefix: str = "URS", *, counts: dict[str, int] | None = None, vocabulary: dict[str, list[str]] | None = None, action_plans: bool = False,
    layout: str = "sections", require: str | None = None,
) -> dict:
    """counts is the number of rows each sheet 7 section must have. A different count fails loudly, so a section that
    lost or gained a row is never read silently. action_plans keeps a Key Risk's fifth column, Action Plans, which the
    Proposer builds on (spec 36). It is opt-in, so a pack's register changes only when it is rebuilt with the flag.
    layout "classified" reads one table whose third column states each theme's tier (see _classified)."""
    rows = read_worksheet(workbook, "7. Annual Risks")
    drafts: list[dict] = []
    if layout == "classified":
        grouped, drafts = _classified(rows, require)
        expected = dict(counts or {})
        found_sections = [(tier, grouped[tier], expected.get(tier)) for tier in ("KEY", "TIER_2", "EMERGING")]
    else:
        expected = {**_THEME_COUNTS, **(counts or {})}
        sections = (
            ("KEY", "ref", "keyrisk", expected["KEY"], "keyrisks"),
            ("TIER_2", "sn", "tier2risk", expected["TIER_2"], "tier2risks"),
            ("EMERGING", "no", "emergingrisk", expected["EMERGING"], "emergingrisks"),
        )
        found_sections = [(tier, _section(rows, ref_key, name_key) or _headed_section(rows, heading), count) for tier, ref_key, name_key, count, heading in sections]
    themes = []
    for tier, found, expected_count in found_sections:
        if expected_count is not None and len(found) != expected_count:
            raise ValueError(f"Sheet 7 {tier} section has {len(found)} rows; expected {expected_count}.")
        for ref, name, detail, extra, plans in found:
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
                    **({"sheetActionPlans": plans or None} if action_plans and tier == "KEY" else {}),
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
        **({"draftThemes": drafts, "draftNote": f"Rows whose classification does not name '{require}' are drafts. They are listed, not scanned."} if drafts else {}),
        "themes": themes,
    }


# The tier a classification names first wins: "Key/Tier 2" is KEY, "Emerging/Tier 2" is EMERGING.
_TIER_WORDS = re.compile(r"\b(key|tier[\s-]*2|emerging)\b", re.I)


def _classified(rows: list[list], require: str | None) -> tuple[dict[str, list[tuple[str, str, str, str, str]]], list[dict]]:
    """Sheet 7 as one table, 'Risk Theme | Exposure Rationale | Proposed Classification' (Marine). Each row's tier is the
    first tier its classification names. With require, a row whose classification lacks that text is a draft."""
    header = next(
        (index for index, row in enumerate(rows) if len(row) > 2 and _key(row[0]).startswith("risk") and "classification" in _key(row[2])),
        None,
    )
    if header is None:
        raise ValueError("Sheet 7 is missing its 'Risk Theme | Rationale | Classification' header.")
    grouped: dict[str, list[tuple[str, str, str, str, str]]] = {"KEY": [], "TIER_2": [], "EMERGING": []}
    drafts: list[dict] = []
    ordered: list[tuple[str, str, str, str]] = []
    for row in rows[header + 1:]:
        name, detail, classification = (_clean(row[index] if len(row) > index else None) for index in (0, 1, 2))
        if not name:
            continue
        word = _TIER_WORDS.search(classification)
        if word is None:
            drafts.append({"themeName": name, "sheetDetail": detail or None, "classification": classification or None, "reason": "NO_TIER_STATED"})
            continue
        if require and require.casefold() not in classification.casefold():
            drafts.append({"themeName": name, "sheetDetail": detail or None, "classification": classification, "reason": "NOT_IN_GOVERNED_REGISTER"})
            continue
        tier = {"key": "KEY", "emerging": "EMERGING"}.get(word.group(1).casefold(), "TIER_2")
        ordered.append((tier, name, detail, classification))
    number = 0
    for tier in grouped:
        for row_tier, name, detail, classification in ordered:
            if row_tier == tier:
                number += 1
                grouped[tier].append((str(number), name, detail, classification, ""))
    return grouped, drafts


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


def _headed_section(rows: list[list], heading: str) -> list[tuple[str, str, str, str, str]]:
    """A section whose header is only 'Ref | Risk' (CA): found by its heading row, such as '2. Tier-2 Risks'."""
    at = next((index for index, row in enumerate(rows) if row and _key(row[0]).lstrip("0123456789") == heading), None)
    if at is None:
        return []
    header = next((index for index in range(at + 1, min(at + 4, len(rows))) if rows[index] and _key(rows[index][0]) in {"ref", "sn", "no"}), None)
    if header is None:
        return []
    return _section(rows[header:], _key(rows[header][0]), _key(rows[header][1] if len(rows[header]) > 1 else None))


def _section(rows: list[list], ref_key: str, name_key: str) -> list[tuple[str, str, str, str, str]]:
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
            found.append((ref, name, *(_clean(row[index] if len(row) > index else None) for index in (2, 3, 4))))
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
