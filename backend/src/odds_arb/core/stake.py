from __future__ import annotations

from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MIN_ODD = Decimal("1")


def calculate_stakes(prices: Mapping[str, Decimal], bankroll: Decimal) -> dict[str, Decimal]:
    if not bankroll.is_finite() or bankroll <= Decimal("0"):
        msg = "bankroll must be a positive finite Decimal"
        raise ValueError(msg)
    if len(prices) < 2:
        msg = "at least two odds are required to calculate stakes"
        raise ValueError(msg)

    for outcome, price in prices.items():
        if not outcome:
            msg = "outcome key must not be empty"
            raise ValueError(msg)
        if not price.is_finite() or price <= MIN_ODD:
            msg = "odd prices must be finite and greater than 1"
            raise ValueError(msg)

    implied_probability_sum = sum(Decimal("1") / price for price in prices.values())
    raw_stakes = {
        outcome: (bankroll / price) / implied_probability_sum for outcome, price in prices.items()
    }
    rounded = {
        outcome: stake.quantize(CENT, rounding=ROUND_HALF_UP)
        for outcome, stake in raw_stakes.items()
    }

    difference = bankroll.quantize(CENT, rounding=ROUND_HALF_UP) - sum(rounded.values())
    if difference != Decimal("0.00"):
        remainders = {outcome: raw_stakes[outcome] - rounded[outcome] for outcome in raw_stakes}
        target = (
            max(remainders, key=lambda outcome: remainders[outcome])
            if difference > Decimal("0")
            else min(remainders, key=lambda outcome: remainders[outcome])
        )
        rounded[target] = (rounded[target] + difference).quantize(CENT, rounding=ROUND_HALF_UP)

    return rounded
