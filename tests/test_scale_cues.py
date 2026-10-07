from grit.core.scale_cues import parse_figures, scale_cues


def _cue(title, text=""):
    return scale_cues({"title": title, "text": text})


def _counted(text):
    return [(item.currency, item.amount) for item in parse_figures(text) if item.counted]


def test_a_stated_amount_of_one_million_or_more_in_a_comparable_currency_counts() -> None:
    assert _counted("LTA awards a US$ 200 million rail contract") == [("USD", 200e6)]
    assert _counted("Contract valued at S$1.2 billion") == [("SGD", 1.2e9)]
    assert _counted("A fine of $5m and €2 bn in damages") == [("USD", 5e6), ("EUR", 2e9)]
    assert _counted("The order is worth USD 10.57 Billion") == [("USD", 10.57e9)]
    assert _counted("A loss of $1,200,000 was booked") == [("USD", 1.2e6)]
    assert _counted("Costs of 40 million dollars") == [("USD", 40e6)]
    assert _counted("Funding of $5-10 million") == [("USD", 10e6)]


def test_an_amount_below_a_million_in_another_currency_or_beside_a_projection_is_listed_and_not_counted() -> None:
    reasons = {item.text: item.reason for item in parse_figures("A $900,000 grant, INR 500 million spend, and a market expected to reach USD 62.6 Billion by 2036")}
    assert reasons == {"$900,000": "BELOW_MINIMUM", "INR 500 million": "CURRENCY_NOT_COMPARABLE", "USD 62.6 Billion": "MARKET_PROJECTION"}
    assert not _cue("Plain news", "A $900,000 grant and INR 500 million spend.").figure_cue
    assert _cue("Plain news", "The firm won a $3m order.").figure_cue


def test_a_bare_number_or_a_unit_with_no_currency_is_not_a_figure() -> None:
    assert parse_figures("The 5 m tall barrier cost 3 million tonnes of steel and 50 b parameters") == []


def test_a_regulator_or_government_instrument_or_act_in_the_title_or_lead_is_a_cue() -> None:
    for title in (
        "US imposes new export controls on chips",
        "Regulators fine rail operator over signalling failure",
        "New tariffs hit Asian suppliers",
        "EU orders a recall of charging units",
        "Singapore government announces a new cybersecurity mandate",
    ):
        assert "GOV_ACTION" in _cue(title).action_classes, title


def test_a_consumer_energy_tariff_is_a_price_plan_and_not_trade_policy() -> None:
    assert not _cue("Smart, standard or fixed? The new era of energy tariffs, explained").action_cue
    assert not _cue("Households switch to a time-of-use electricity tariff").action_cue
    assert "GOV_ACTION" in _cue("Washington raises tariffs on steel imports").action_classes


def test_war_and_a_current_pandemic_are_cues_and_recalled_ones_are_not() -> None:
    assert "WAR" in _cue("Airstrike closes the port, shipping rerouted").action_classes
    assert "WAR" in _cue("Ceasefire collapses as fighting spreads").action_classes
    assert "PANDEMIC" in _cue("New outbreak of cholera triggers a lockdown").action_classes
    for text in ("Post-pandemic demand for rail returns", "Since the pandemic, hiring has changed", "A price war and a trade war for talent"):
        assert not _cue(text).action_cue, text


def test_an_action_phrase_deep_in_the_body_is_background() -> None:
    body = "Station upgrades continue across the network. " * 20 + "The government fined a rival last year."
    assert not _cue("Station upgrades continue", body).action_cue
    assert _cue("Station upgrades continue", "The government fined a rival last week. " + body).action_cue


def test_a_cue_records_what_matched_and_serialises() -> None:
    import json

    cue = _cue("US imposes tariffs on Taiwan chips", "Costs rise by $4 million.")
    record = cue.as_record()
    assert record["figure_cue"] is True and record["action_classes"] == ["GOV_ACTION"] and record["action"]
    json.dumps(record)


def test_an_agency_acronym_matches_in_capitals_only_and_an_ordinary_word_does_not() -> None:
    for title in ("Jane Doe announces new coffee shop", "My mom launches a bakery", "The sec issues a ruling on its own", "A mas announces a festival"):
        assert not _cue(title).action_cue, title
    for title in ("DOE announces new efficiency rules", "MOM bans foreign worker hiring in the sector", "The SEC fines a rail supplier"):
        assert "GOV_ACTION" in _cue(title).action_classes, title
