from __future__ import annotations

from decimal import Decimal

import pytest

from odds_arb.core.stake import calculate_stakes


def test_calculate_stakes_two_way_equalizes_payouts() -> None:
    prices = {"over": Decimal("2.10"), "under": Decimal("2.05")}

    stakes = calculate_stakes(prices, Decimal("1000"))

    assert stakes == {"over": Decimal("493.98"), "under": Decimal("506.02")}
    payouts = {
        outcome: (stakes[outcome] * price).quantize(Decimal("0.01"))
        for outcome, price in prices.items()
    }
    assert max(payouts.values()) - min(payouts.values()) <= Decimal("0.02")


def test_calculate_stakes_three_way_uses_full_bankroll() -> None:
    prices = {"home": Decimal("2.20"), "draw": Decimal("3.60"), "away": Decimal("4.20")}

    stakes = calculate_stakes(prices, Decimal("1000"))

    assert stakes == {
        "home": Decimal("468.40"),
        "draw": Decimal("286.25"),
        "away": Decimal("245.35"),
    }
    assert sum(stakes.values()) == Decimal("1000.00")


def test_calculate_stakes_arbitrary_bankroll_rounds_to_cents() -> None:
    prices = {"yes": Decimal("2.25"), "no": Decimal("1.95")}

    stakes = calculate_stakes(prices, Decimal("137.53"))

    assert stakes == {"yes": Decimal("63.85"), "no": Decimal("73.68")}
    assert sum(stakes.values()) == Decimal("137.53")
    assert all(stake.as_tuple().exponent == -2 for stake in stakes.values())


def test_calculate_stakes_rejects_zero_bankroll() -> None:
    with pytest.raises(ValueError, match="bankroll"):
        calculate_stakes({"yes": Decimal("2.20"), "no": Decimal("2.10")}, Decimal("0"))


def test_calculate_stakes_rejects_nonpositive_or_unit_odds() -> None:
    with pytest.raises(ValueError, match="odd"):
        calculate_stakes({"yes": Decimal("1.00"), "no": Decimal("-2.10")}, Decimal("100"))


def test_calculate_stakes_rejects_single_outcome() -> None:
    with pytest.raises(ValueError, match="at least two"):
        calculate_stakes({"yes": Decimal("2.20")}, Decimal("100"))


def test_calculate_stakes_rejects_empty_outcome_key() -> None:
    with pytest.raises(ValueError, match="outcome"):
        calculate_stakes({"": Decimal("2.20"), "no": Decimal("2.10")}, Decimal("100"))


def test_calculate_stakes_allocates_positive_rounding_difference() -> None:
    stakes = calculate_stakes(
        {"home": Decimal("3.00"), "draw": Decimal("3.00"), "away": Decimal("3.00")},
        Decimal("100"),
    )

    assert sum(stakes.values()) == Decimal("100.00")


def test_calculate_stakes_allocates_negative_rounding_difference() -> None:
    stakes = calculate_stakes(
        {"first": Decimal("1.01"), "second": Decimal("1.01"), "third": Decimal("1.50")},
        Decimal("100"),
    )

    assert sum(stakes.values()) == Decimal("100.00")
