from grit_scan.doc_type import EXPLAINER, FINANCE_COMMENTARY, MARKET_RESEARCH, NEWS, NON_EVENT_TYPES, PRESS_RELEASE, classify, is_wire


def _doc(title, text="", publisher="example.com"):
    return {"title": title, "text": text, "publisher": publisher}


def test_a_title_that_names_a_market_report_is_market_research() -> None:
    for title in (
        "Smart Water Meters Market worth $10.05 billion by 2031 | MarketsandMarkets™",
        "Singapore Data Center Market Report 2026-2034: Size, Share, Growth Drivers",
        "Smart Hospitals Market Expands Rapidly as Apollo Invests in a 400-Bed Facility",
        "Europe Utility Management System Market Size, Share, Trends, and Forecast, 2026-2033",
    ):
        assert classify(_doc(title)).doc_type == MARKET_RESEARCH, title


def test_two_kinds_of_report_phrase_in_the_text_make_market_research_and_one_does_not() -> None:
    body = "The segment is expected to reach USD 4.2 billion at a CAGR of 9%."
    assert classify(_doc("Smart metering rollout widens", body)).doc_type == MARKET_RESEARCH
    assert classify(_doc("Water utility signs contract", "Valued at USD 4 billion, the plan runs to 2030 by 2030.")).doc_type == NEWS


def test_real_news_that_mentions_markets_or_years_stays_news() -> None:
    for title in (
        "Smarter controls could make room for 330 GW on existing power grids - Market Business News",
        "Water risks threaten trade as companies face US$397 billion exposure: CDP",
        "UK off track to halt and reverse nature loss by 2030, says Wildlife Trusts",
        "LTA awards rail signalling contract to Thales",
    ):
        assert classify(_doc(title)).doc_type == NEWS, title


def test_a_wire_release_is_a_press_release_and_subdomains_count() -> None:
    assert classify(_doc("Thales wins Singapore rail contract", publisher="www.prnewswire.com")).doc_type == PRESS_RELEASE
    assert is_wire("uk.businesswire.com") and not is_wire("notbusinesswire.com") and not is_wire(None)


def test_an_investment_note_about_a_listed_company_is_commentary_and_not_an_event() -> None:
    for title in (
        "Amtech Gains 79% in the Past Year: Should It Be in Your Portfolio?",
        "Amtech Systems (ASYS) Gets a Buy from Titan Partners - The Globe and Mail",
        "Motorola Solutions stock carries a USD 519.56 consensus target",
        "Amtech Systems (NASDAQ:ASYS) Shares Cross Above 200-Day Moving Average",
        "High Insider Confidence In Leading Growth Stocks For September 2026",
        "DNB Carnegie cuts target for Securitas stock to SEK 185",
        "Motorola Solutions stock trades at EUR 398.35, plus 0.21 percent",
        "Amtech Systems Announces Participation in LD Micro Investor Conference on Oct. 20",
        "EFN recommends Securitas stock as adjusted margin reaches 7.30 percent",
    ):
        assert classify(_doc(title)).doc_type == FINANCE_COMMENTARY, title
    assert FINANCE_COMMENTARY in NON_EVENT_TYPES
    assert classify(_doc("Leonardo stock reports 45 percent order growth after H1 results")).doc_type == NEWS
    assert classify(_doc("Siemens shares fall after a plant fire halts output")).doc_type == NEWS
    assert classify(_doc("Rail operator hit by a signalling failure", "The price target for the project was missed and the buy rating on the contract fell.")).doc_type == FINANCE_COMMENTARY
    assert classify(_doc("Rail operator hit by a signalling failure", "The consensus was that the line would reopen on time.")).doc_type == NEWS


def test_a_how_to_a_guide_a_paid_post_or_an_opinion_piece_is_an_explainer_and_not_an_event() -> None:
    for title in (
        "Competitor analysis: how to perform one in 5 easy steps",
        "Understanding Forex Market Forces Driving Major Trade Moves",
        "Council Post: AI And Cybersecurity: Why Securing The Intelligent Enterprise Requires A New Security Model",
        "Cybersecurity and the Challenges of Supporting an OT Network Today explained",
        "The complete guide to platform screen doors",
        "7 ways to cut energy costs in your building",
        "Competitor Analysis for Digital Marketing: A 6-Step Guide",
        "Smart metering | A Practical Guide",
    ):
        assert classify(_doc(title)).doc_type == EXPLAINER, title
    assert EXPLAINER in NON_EVENT_TYPES
    for title in ("LTA awards a signalling contract after how long a wait", "Singapore explains new fare rules after the vote", "Why the rail strike collapsed"):
        assert classify(_doc(title)).doc_type in {NEWS, EXPLAINER}
    assert classify(_doc("Minister says rail strike will end", "")).doc_type == NEWS
    assert classify(_doc("Rail guide dogs banned from a station in a safety row")).doc_type == NEWS
