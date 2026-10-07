from pathlib import Path

from grit_scan.ontology import load_ontology_pack
from grit_scan.search_inputs import (
    LIBRARY_SHEETS,
    _is_note,
    parse_competitor_rows,
    parse_keyword_library_rows,
    parse_management_focus_rows,
)

_ROOT = Path(__file__).resolve().parent.parent


def _library(*lines):
    return [(line, "") if isinstance(line, str) or line is None else line for line in (line or "" for line in lines)]


_LIBRARY = _library(
    "1. Tier 1 Strategic Keywords (Highest Priority)", None,
    "Smart Metro & Rail Digitalisation", None,
    "Smart Metro", "Digital Rail", None,
    "A note about the portfolio. [stengg.com], [Details | Word]", None, None,
    "2. Rail Operations & Control Centres (Critical)", None,
    "Core Keywords", None,
    "Control centre", "ITOC", None,
    "Emerging Risk Themes", None,
    "Control centre outages", "Signal failures", None,
    "These are major drivers for demand.", None,
    "6. Commercial Vehicles (MAN)", None,
    "Keywords", None,
    "Electric Van", "Electric Bus", None,
    "12. GRIT Geopolitical & Market Watchlist", None,
    "Countries / Regions", None,
    "Singapore", "Taiwan", None,
    "Recommended GRIT Tag Clusters for MRL", None,
    ("Cluster", "Priority"), ("Smart Metro Systems", "Critical"), ("Platform Screen Doors", "High"), None,
    "Top 30 Keywords for GRIT Scan", None,
    "For signal-to-noise, I would prioritise:", None,
    "Smart Metro, Digital Rail, ITOC, Control centre, Electric Van. [Smart Metro | SharePoint], [stengg.com]", None,
    "Highest-Value Emerging Risk Themes for MRL", None,
    "1. Rail Cybersecurity & OT Threats", "2. Metro Expansion Projects", None,
)


def test_library_clusters_carry_a_priority_only_when_the_sheet_states_one() -> None:
    library = parse_keyword_library_rows(_LIBRARY)
    first, second = library["clusters"]
    assert (first["number"], first["name"], first["priority"]) == (2, "Rail Operations & Control Centres", "Critical")
    # "(MAN)" is part of the name. It is not a priority.
    assert (second["number"], second["name"], second["priority"]) == (6, "Commercial Vehicles (MAN)", None)
    assert library["tier1"] == [{"name": "Smart Metro & Rail Digitalisation", "terms": ["Smart Metro", "Digital Rail"]}]


def test_library_group_labels_inside_a_cluster_are_not_the_tail_headings() -> None:
    library = parse_keyword_library_rows(_LIBRARY)
    groups = {group["name"]: group["terms"] for group in library["clusters"][0]["groups"]}
    assert groups == {"Core Keywords": ["Control centre", "ITOC"], "Emerging Risk Themes": ["Control centre outages", "Signal failures"]}


def test_library_sentences_and_notes_are_never_terms() -> None:
    library = parse_keyword_library_rows(_LIBRARY)
    terms = [term for cluster in library["clusters"] for group in cluster["groups"] for term in group["terms"]]
    assert "These are major drivers for demand." not in terms
    assert library["notesSkipped"] >= 2
    assert library["dropped"] == []


def test_library_tail_sections_are_read() -> None:
    library = parse_keyword_library_rows(_LIBRARY)
    assert library["watchlist"] == [{"name": "Countries / Regions", "terms": ["Singapore", "Taiwan"]}]
    assert library["tagClusters"] == [{"name": "Smart Metro Systems", "priority": "Critical"}, {"name": "Platform Screen Doors", "priority": "High"}]
    assert library["scanKeywords"] == {"declared": 30, "terms": ["Smart Metro", "Digital Rail", "ITOC", "Control centre", "Electric Van"]}
    assert library["emergingThemes"] == ["Rail Cybersecurity & OT Threats", "Metro Expansion Projects"]


def test_a_note_glued_under_the_tag_table_is_not_a_tag_cluster() -> None:
    library = parse_keyword_library_rows(
        _library(
            "Recommended GRIT Tag Clusters for SSA", None,
            ("Cluster", "Priority"), ("Physical Security", "Critical"), ("Perimeter Intrusion Detection", "High"),
            "For GRIT Scan, I would prioritise five keyword baskets:",
            "(1) Critical Infrastructure Protection,(2) Airport/Border Security, as these are aligned to SSA's strategy.", None,
        )
    )
    assert [tag["name"] for tag in library["tagClusters"]] == ["Physical Security", "Perimeter Intrusion Detection"]
    assert library["notesSkipped"] == 2


def test_library_terms_with_no_section_are_dropped_with_a_reason() -> None:
    library = parse_keyword_library_rows(_library("Orphan term", "Another term", None))
    assert [item["reason"] for item in library["dropped"]] == ["TERMS_OUTSIDE_A_SECTION"]


def test_competitors_forward_fill_the_lob_skip_headers_and_deduplicate() -> None:
    rows = [
        ["List of top competitors/peers - URS 2026", "", "", ""],
        ["", "", "", ""],
        ["LOB", "Competitor", "Workscope", "Note (if any)"],
        ["Smart Security and Automation", "Amadeus", "Turnkey integration", ""],
        ["", "Bosch", "Video surveillance", "Low cost"],
        ["LOB", "Competitor", "Workscope", "Note (if any)"],
        ["", "Bosch", "Duplicate row", ""],
        ["Mobility (Rail)", "Bosch", "Rail", ""],
    ]
    parsed = parse_competitor_rows(rows)
    assert [(item["lobLabel"], item["competitor"]) for item in parsed["competitors"]] == [
        ("Smart Security and Automation", "Amadeus"),
        ("Smart Security and Automation", "Bosch"),
        ("Mobility (Rail)", "Bosch"),
    ]
    assert parsed["competitors"][1]["note"] == "Low cost" and parsed["competitors"][0]["note"] is None
    assert parsed["dropped"] == []


def test_a_name_with_no_workscope_that_heads_a_block_is_a_subheading_not_a_competitor() -> None:
    rows = [
        ["LOB", "Competitor", "Workscope", "Note (if any)"],
        ["Mobility (Road)", "Traffic Management", "", ""],
        ["", "Yunex Traffic", "Road traffic management", ""],
        ["", "Kapsch", "Tunnel management", ""],
        ["", "", "", ""],
        ["", "Fleet Management", "", ""],
        ["", "INIT", "Bus fleet management", ""],
        ["", "", "", ""],
        ["", "Lone Name", "", ""],
    ]
    parsed = parse_competitor_rows(rows)
    assert [(item["competitor"], item["category"]) for item in parsed["competitors"]] == [
        ("Yunex Traffic", "Traffic Management"),
        ("Kapsch", "Traffic Management"),
        ("INIT", "Fleet Management"),
        ("Lone Name", None),
    ]
    assert [(item["text"], item["reason"]) for item in parsed["dropped"]] == [
        ("Traffic Management", "SUBHEADING"),
        ("Fleet Management", "SUBHEADING"),
    ]


def test_management_focus_reads_requests_and_the_scope_table() -> None:
    rows = [
        ["10", "LOB", "", "Keywords", ""],
        ["1", "Smart Sustainable Utilities", "STE URS QFC Branch", "Workforce risk", ""],
        ["4", "Rail markets", "", "Analyse the sectors. Identify:", ""],
        ["", "1", "Thailand", "Opportunities", ""],
        ["", "2", "Malaysia", "Major projects", ""],
        ["", "Updates quarterly.", "", "PPPs", ""],
        ["", "", "", "Challenges", ""],
        ["", "", "", "Funding", ""],
        ["10. Management Focus", "", "", "", ""],
        ["LOB / Scope", "Management Focus", "Why It Matters", "Suggested Monitoring Indicators", "Status"],
        ["All LOBs", "Cybersecurity", "Critical infrastructure", "Incidents; remediation time", "Core control focus — validate KPI definitions"],
    ]
    parsed = parse_management_focus_rows(rows)
    first, market = parsed["requests"]
    assert first["entity"] == "STE URS QFC Branch" and first["countries"] == [] and first["topics"] == []
    assert market["countries"] == ["Thailand", "Malaysia"] and market["notes"] == ["Updates quarterly."]
    assert [(topic["name"], topic["items"]) for topic in market["topics"]] == [
        ("Opportunities", ["Major projects", "PPPs"]),
        ("Challenges", ["Funding"]),
    ]
    (item,) = parsed["focusItems"]
    assert item["allLobs"] is True and item["indicators"] == ["Incidents", "remediation time"]
    assert item["status"].startswith("Core control focus")


def test_management_focus_fails_loudly_on_another_layout() -> None:
    try:
        parse_management_focus_rows([["a", "b", "c", "d", "e"]])
    except ValueError as error:
        assert "LOB / Scope" in str(error)
    else:
        raise AssertionError("A sheet without the table must not parse")


def test_the_pack_carries_the_three_search_registers_without_inventing() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    libraries = pack.keyword_library["libraries"]
    assert [item["sheet"] for item in libraries] == list(LIBRARY_SHEETS)
    assert all(item["sheetState"] == "hidden" and item["canonicalLob"] for item in libraries)
    assert all(len(item["clusters"]) >= 8 and item["dropped"] == [] for item in libraries)
    terms = [term for item in libraries for cluster in item["clusters"] for group in cluster["groups"] for term in group["terms"]]
    assert terms and not any(term.endswith(".") for term in terms)
    # Every list in the register, including the top lists. None may carry the author's source citations.
    every_text = terms + [
        *(term for item in libraries for term in (item["scanKeywords"] or {}).get("terms", [])),
        *(term for item in libraries for section in ("tier1", "watchlist") for group in item[section] for term in group["terms"]),
        *(theme for item in libraries for theme in item["emergingThemes"]),
        *(tag["name"] for item in libraries for tag in item["tagClusters"]),
    ]
    assert not any("[" in text or "]" in text for text in every_text)
    assert not any(_is_note(tag["name"]) for item in libraries for tag in item["tagClusters"])

    items = pack.management_focus["focusItems"]
    assert len(items) == 10 and any(item["allLobs"] for item in items)
    market = pack.management_focus["requests"][3]
    assert market["countries"] == ["Thailand", "Malaysia", "India", "Vietnam", "Australia"]
    assert [topic["name"] for topic in market["topics"]] == ["Opportunities", "Challenges", "Key Risks", "Assess"]


def test_every_label_without_a_canonical_lob_is_listed_as_unresolved() -> None:
    pack = load_ontology_pack(_ROOT / "config", "urs")
    unresolved = {item["label"] for item in pack.lob_hierarchy["unresolvedLabels"]}
    competitor_labels = {item["lobLabel"] for item in pack.competitors["competitors"] if item["canonicalLob"] is None}
    scope_labels = {item["scope"] for item in pack.management_focus["focusItems"] if item["canonicalLob"] is None and not item["allLobs"]}
    assert competitor_labels and scope_labels
    assert competitor_labels <= unresolved and scope_labels <= unresolved
