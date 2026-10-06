from __future__ import annotations

from decimal import Decimal

import pytest

from odds_arb.core.arbitrage import find_arbitrage_opportunities


def test_three_way_arbitrage_detected_with_known_margin(make_odd) -> None:
    odds = [
        make_odd("home", "2.20", "betano"),
        make_odd("draw", "3.60", "kto"),
        make_odd("away", "4.20", "superbet"),
    ]

    opportunities = find_arbitrage_opportunities(odds, bankroll=Decimal("1000"))

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.match_id == "flamengo-vasco"
    assert opportunity.market_key == "1x2"
    assert opportunity.implied_probability_sum.quantize(Decimal("0.000001")) == Decimal("0.970418")
    assert opportunity.profit_pct.quantize(Decimal("0.01")) == Decimal("3.05")


def test_two_way_arbitrage_detected_with_known_margin(make_odd) -> None:
    odds = [
        make_odd("over", "2.10", "betano", market_key="over_under_2_5"),
        make_odd("under", "2.05", "kto", market_key="over_under_2_5"),
    ]

    opportunities = find_arbitrage_opportunities(odds, bankroll=Decimal("1000"))

    assert len(opportunities) == 1
    opportunity = opportunities[0]
    assert opportunity.market_key == "over_under_2_5"
    assert opportunity.implied_probability_sum.quantize(Decimal("0.000001")) == Decimal("0.963995")
    assert opportunity.profit_pct.quantize(Decimal("0.01")) == Decimal("3.73")


def test_exactly_one_implied_probability_boundary_is_not_arbitrage(make_odd) -> None:
    odds = [
        make_odd("yes", "2.00", "betano", market_key="both_teams_score"),
        make_odd("no", "2.00", "kto", market_key="both_teams_score"),
    ]

    assert find_arbitrage_opportunities(odds) == []


def test_sum_above_one_is_not_arbitrage(make_odd) -> None:
    odds = [
        make_odd("yes", "1.90", "betano", market_key="both_teams_score"),
        make_odd("no", "1.90", "kto", market_key="both_teams_score"),
    ]

    assert find_arbitrage_opportunities(odds) == []


def test_sum_below_one_is_arbitrage(make_odd) -> None:
    odds = [
        make_odd("yes", "2.01", "betano", market_key="both_teams_score"),
        make_odd("no", "2.01", "kto", market_key="both_teams_score"),
    ]

    opportunities = find_arbitrage_opportunities(odds)

    assert len(opportunities) == 1
    assert opportunities[0].profit_pct.quantize(Decimal("0.01")) == Decimal("0.50")


def test_double_chance_is_not_detected_with_naive_one_of_n_formula(make_odd) -> None:
    odds = [
        make_odd("home_draw", "3.10", "betano", market_key="double_chance"),
        make_odd("home_away", "3.20", "kto", market_key="double_chance"),
        make_odd("draw_away", "3.30", "superbet", market_key="double_chance"),
    ]

    assert find_arbitrage_opportunities(odds) == []


@pytest.mark.parametrize(
    "price",
    [Decimal("1.00"), Decimal("Infinity"), Decimal("NaN"), Decimal("-2.00")],
    ids=["one", "infinity", "nan", "negative"],
)
def test_invalid_odd_prices_are_rejected(make_odd, price: Decimal) -> None:
    odds = [
        make_odd("yes", price, "betano", market_key="both_teams_score"),
        make_odd("no", "2.20", "kto", market_key="both_teams_score"),
    ]

    with pytest.raises(ValueError, match="odd"):
        find_arbitrage_opportunities(odds)


def test_best_odd_per_outcome_is_selected_across_bookmakers(make_odd) -> None:
    odds = [
        make_odd("home", "2.10", "betano"),
        make_odd("home", "2.20", "kto"),
        make_odd("draw", "3.40", "betano"),
        make_odd("draw", "3.50", "superbet"),
        make_odd("away", "3.80", "betano"),
        make_odd("away", "3.90", "superbet"),
    ]

    opportunities = find_arbitrage_opportunities(odds)

    assert len(opportunities) == 1
    best_odds = opportunities[0].best_odds
    assert best_odds["home"].bookmaker == "kto"
    assert best_odds["draw"].bookmaker == "superbet"
    assert best_odds["away"].bookmaker == "superbet"


def test_duplicate_same_bookmaker_outcome_does_not_inflate_arbitrage(make_odd) -> None:
    # A mesma casa manda o mesmo outcome duas vezes; a odd alta (9.90) e suspeita/stale.
    # Com a odd conservadora (2.20) nao ha arb; a engine nao pode fabricar arb com a duplicata.
    odds = [
        make_odd("home", "2.20", "kto"),
        make_odd("home", "9.90", "kto"),
        make_odd("draw", "2.50", "kto"),
        make_odd("away", "2.50", "kto"),
    ]

    assert find_arbitrage_opportunities(odds) == []


def test_duplicate_same_bookmaker_outcome_keeps_conservative_price(make_odd) -> None:
    odds = [
        make_odd("home", "2.20", "kto"),
        make_odd("home", "9.90", "kto"),
        make_odd("draw", "3.60", "superbet"),
        make_odd("away", "4.20", "superbet"),
    ]

    opportunities = find_arbitrage_opportunities(odds)

    assert len(opportunities) == 1
    assert opportunities[0].best_odds["home"].price == Decimal("2.20")


def test_missing_outcome_does_not_create_opportunity(make_odd) -> None:
    odds = [
        make_odd("home", "2.50", "betano"),
        make_odd("draw", "4.00", "kto"),
    ]

    assert find_arbitrage_opportunities(odds) == []


def test_scans_independent_match_market_groups(make_odd) -> None:
    odds = [
        make_odd("over", "1.90", "betano", match_id="match-a", market_key="over_under_2_5"),
        make_odd("under", "1.90", "kto", match_id="match-a", market_key="over_under_2_5"),
        make_odd("over", "2.10", "betano", match_id="match-b", market_key="over_under_2_5"),
        make_odd("under", "2.05", "kto", match_id="match-b", market_key="over_under_2_5"),
    ]

    opportunities = find_arbitrage_opportunities(odds)

    assert [opportunity.match_id for opportunity in opportunities] == ["match-b"]


def test_min_profit_filter_excludes_small_arbitrage(make_odd) -> None:
    odds = [
        make_odd("yes", "2.01", "betano", market_key="both_teams_score"),
        make_odd("no", "2.01", "kto", market_key="both_teams_score"),
    ]

    opportunities = find_arbitrage_opportunities(odds, min_profit_pct=Decimal("1.50"))

    assert opportunities == []


def test_invalid_bankroll_is_rejected(make_odd) -> None:
    odds = [
        make_odd("yes", "2.20", "betano", market_key="both_teams_score"),
        make_odd("no", "2.20", "kto", market_key="both_teams_score"),
    ]

    with pytest.raises(ValueError, match="bankroll"):
        find_arbitrage_opportunities(odds, bankroll=Decimal("-100"))


def test_invalid_min_profit_pct_is_rejected(make_odd) -> None:
    odds = [
        make_odd("yes", "2.20", "betano", market_key="both_teams_score"),
        make_odd("no", "2.20", "kto", market_key="both_teams_score"),
    ]

    with pytest.raises(ValueError, match="min_profit_pct"):
        find_arbitrage_opportunities(odds, min_profit_pct=Decimal("-0.01"))


def test_unknown_outcome_is_ignored_when_selecting_best_odds(make_odd) -> None:
    odds = [
        make_odd("yes", "2.20", "betano", market_key="both_teams_score"),
        make_odd("no", "2.20", "kto", market_key="both_teams_score"),
        make_odd("maybe", "100.00", "superbet", market_key="both_teams_score"),
    ]

    opportunities = find_arbitrage_opportunities(odds)

    assert len(opportunities) == 1
    assert set(opportunities[0].best_odds) == {"yes", "no"}
