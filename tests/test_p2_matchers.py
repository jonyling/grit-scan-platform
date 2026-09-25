from grit_scan.matchers import credibility_lookup, exposure_entity_match, lob_match, theme_match


_SOURCES = {
    "sources": [
        {"sourceName": "Reuters", "sourceDomain": "reuters.com", "sourceClass": "WIRE", "credibilityRating": "HIGH", "active": True},
        {"sourceName": "ISW", "sourceDomain": "understandingwar.org", "sourceClass": "RESEARCH", "credibilityRating": "HIGH", "active": True},
    ]
}
_LOBS = {
    "lobs": [
        {
            "lobId": "LOB-004",
            "lobCode": "URS-SM-MRL",
            "lobName": "Mobility Rail",
            "baName": "Urban Solutions",
            "owner": "URS",
            "active": True,
            "keywords": ["platform screen doors", "rail"],
            "priorityCountries": ["Taiwan"],
        },
        {
            "lobId": "LOB-INACTIVE",
            "lobCode": "URS-SSA",
            "lobName": "Smart Security & Automation",
            "active": False,
            "keywords": ["security"],
            "priorityCountries": [],
        },
    ]
}
_EXPOSURE = {
    "projects": [
        {
            "projectId": "PRJ-YELLOW",
            "projectName": "KMRT Yellow Line",
            "aliases": ["Yellow Line"],
            "linkedCustomerIds": ["CUST-KMRT"],
        },
        {
            "projectId": "PRJ-TY-GREEN-EXT",
            "projectName": "Taoyuan Green Line Extension",
            "aliases": ["Green Line Extension"],
            "linkedCustomerIds": ["CUST-DORTS"],
            "linkedSiteIds": ["SITE-ZHONGLI"],
        },
        {
            "projectId": "PRJ-RK",
            "projectName": "Kaohsiung MRT Gangshan-Luzhu",
            "aliases": ["Gangshan-Luzhu", "RKC02"],
        },
    ],
    "customers": [
        {"customerId": "CUST-KMRT", "customerName": "Kaohsiung MRT", "aliases": ["KMRT"], "linkedProjectIds": ["PRJ-YELLOW"]},
        {"customerId": "CUST-DORTS", "customerName": "Taoyuan DORTS", "aliases": ["DORTS"]},
    ],
    "suppliers": [
        {"supplierId": "SUP-3S", "supplierName": "3S", "aliases": [{"text": "3S", "matchable": False}]},
    ],
    "sites": [
        {"siteId": "SITE-ZHONGLI", "siteName": "Zhongli", "aliases": ["Zhongli"]},
        {"siteId": "SITE-RENWU", "siteName": "Renwu", "aliases": ["Renwu"]},
    ],
}
_THEMES = {
    "themes": [
        {"themeId": "KEY-02", "themeName": "Financial volatility", "tier": "KEY", "keywords": ["interest rates", "financing costs"]},
        {"themeId": "T2-01", "themeName": "Taiwan business concentration", "tier": "TIER_2", "keywords": ["Taiwan concentration"]},
        {"themeId": "EMG-01", "themeName": "Geopolitical volatility", "tier": "EMERGING", "keywords": ["Taiwan Strait"]},
    ]
}


def _ids(result):
    return [item["entityId"] for item in result["matchedEntities"]]


def test_credibility_matches_registered_domains_and_refuses_unknown() -> None:
    reuters = credibility_lookup("https://www.reuters.com/world/asia-pacific/some-article-2026-09-06/", _SOURCES)
    assert reuters["matched"] is True and reuters["credibilityRating"] == "HIGH" and reuters["active"] is True
    isw = credibility_lookup("https://www.understandingwar.org/backgrounder/china-taiwan-update", _SOURCES)
    assert isw["matched"] is True and isw["credibilityRating"] == "HIGH"
    unknown = credibility_lookup("https://www.some-random-travel-blog.example/taipei-metro-map", _SOURCES)
    assert unknown["matched"] is False and unknown["credibilityRating"] == "UNRATED"
    invalid = credibility_lookup("not-a-url", _SOURCES)
    assert invalid["matchMethod"] == "INVALID_URL"


def test_exposure_binds_real_sentences_and_one_hop_links() -> None:
    funding = exposure_entity_match(
        "Record NT$49.9bn national MRT subsidy budget announced; Kaohsiung flags NT$13.6bn shortfall touching KMRT Yellow Line",
        _EXPOSURE,
    )
    assert _ids(funding) == ["PRJ-YELLOW", "CUST-KMRT"]
    yellow = next(item for item in funding["matchedEntities"] if item["entityId"] == "PRJ-YELLOW")
    assert any(link["entityId"] == "CUST-KMRT" for link in yellow["linkedExposure"])

    green = exposure_entity_match(
        "Taoyuan DORTS confirms Green Line Extension (Zhongli) E&M works commenced on schedule",
        _EXPOSURE,
    )
    assert {"CUST-DORTS", "PRJ-TY-GREEN-EXT", "SITE-ZHONGLI"} <= set(_ids(green))

    rk = exposure_entity_match(
        "injured at the RKC02 work area of the Kaohsiung MRT Gangshan\u2013Luzhu extension",
        _EXPOSURE,
    )
    assert "PRJ-RK" in _ids(rk)

    warehouses = exposure_entity_match("the Zhongli/Renwu warehouses", _EXPOSURE)
    assert {"SITE-ZHONGLI", "SITE-RENWU"} <= set(_ids(warehouses))


def test_short_alias_and_embedded_tokens_do_not_match() -> None:
    guarded = exposure_entity_match("This week the 3S structural steel supplier report was reviewed.", _EXPOSURE)
    assert "SUP-3S" not in _ids(guarded)
    register = {
        "projects": [],
        "customers": [],
        "suppliers": [
            {"supplierId": "SUP-AI", "supplierName": "AI", "aliases": [{"text": "ai", "matchable": True}]},
            {"supplierId": "SUP-WAR", "supplierName": "WAR", "aliases": ["war"]},
        ],
        "sites": [],
    }
    assert exposure_entity_match("Taiwan warehouses", register)["matchCount"] == 0
    assert exposure_entity_match("Taiwan Central Weather Administration recorded four earthquakes.", _EXPOSURE)["matchCount"] == 0


def test_lob_match_respects_active_flag_and_short_keywords() -> None:
    assert lob_match("Mobility Rail", _LOBS)["lob"]["lobId"] == "LOB-004"
    assert lob_match("URS-SM-MRL", _LOBS)["matchMethod"] == "EXACT_NAME_OR_CODE"
    assert lob_match("platform screen doors", _LOBS)["lob"]["lobId"] == "LOB-004"
    assert lob_match("Taiwan", _LOBS)["matchMethod"] == "PRIORITY_COUNTRY_MATCH"
    assert lob_match("Smart Security & Automation", _LOBS)["matched"] is False
    assert lob_match("completely unrelated nonsense text", _LOBS)["matchMethod"] == "NO_ACTIVE_LOB_MATCH"
    short = {"lobs": [{"lobId": "X", "lobName": "Other", "lobCode": "X", "active": True, "keywords": ["ai", "war"], "priorityCountries": []}]}
    assert lob_match("Taiwan warehouses", short)["matched"] is False
    assert lob_match("the ai programme", short)["matched"] is True


def test_theme_match_returns_every_theme_and_validates_hints() -> None:
    fed = theme_match("Rising interest rates increase financing costs for URS project margins.", _THEMES)
    assert "KEY-02" in [item["themeId"] for item in fed["matchedThemes"]]
    multi = theme_match(
        "Financing costs are rising for the Taiwan concentration exposure amid Taiwan Strait military exercises.",
        _THEMES,
    )
    assert {"KEY-02", "T2-01", "EMG-01"} <= {item["themeId"] for item in multi["matchedThemes"]}
    hinted = theme_match("A generic statement with no obvious keyword overlap.", _THEMES, ["Financial volatility"])
    assert hinted["matchedThemes"][0]["themeId"] == "KEY-02"
    assert hinted["matchedThemes"][0]["matchMethod"] == "VALIDATED_HINT"
    assert theme_match("A generic statement with no obvious keyword overlap.", _THEMES, ["Not a real theme"])["matchCount"] == 0
    assert theme_match("A pleasant afternoon with no risk-relevant content whatsoever.", _THEMES)["matchCount"] == 0
