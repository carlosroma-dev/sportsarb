from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from odds_arb.core.models import ArbitrageOpportunity, MarketKey, Odd
from odds_arb.core.stake import calculate_stakes

REQUIRED_OUTCOMES: dict[MarketKey, frozenset[str]] = {
    "1x2": frozenset({"home", "draw", "away"}),
    "over_under_2_5": frozenset({"over", "under"}),
    "both_teams_score": frozenset({"yes", "no"}),
}
MIN_ODD = Decimal("1")


def find_arbitrage_opportunities(
    odds: Sequence[Odd],
    *,
    bankroll: Decimal | None = None,
    min_profit_pct: Decimal = Decimal("0"),
) -> list[ArbitrageOpportunity]:
    if bankroll is not None and (not bankroll.is_finite() or bankroll <= Decimal("0")):
        msg = "bankroll must be a positive finite Decimal"
        raise ValueError(msg)
    if not min_profit_pct.is_finite() or min_profit_pct < Decimal("0"):
        msg = "min_profit_pct must be a non-negative finite Decimal"
        raise ValueError(msg)

    for odd in odds:
        if not odd.price.is_finite() or odd.price <= MIN_ODD:
            msg = "odd prices must be finite and greater than 1"
            raise ValueError(msg)

    grouped: dict[tuple[str, MarketKey], list[Odd]] = defaultdict(list)
    for odd in odds:
        grouped[(odd.match_id, odd.market_key)].append(odd)

    opportunities: list[ArbitrageOpportunity] = []
    for (match_id, market_key), market_odds in grouped.items():
        if market_key not in REQUIRED_OUTCOMES:
            # Double chance is a 2-of-3 winning market; correct DC arb needs a
            # cross-market model against 1x2 complements, not the naive 1-of-N formula.
            continue
        required_outcomes = REQUIRED_OUTCOMES[market_key]
        best_odds = _best_odds_by_outcome(market_odds, required_outcomes)
        if set(best_odds) != required_outcomes:
            continue

        implied_probability_sum = sum(
            (Decimal("1") / odd.price for odd in best_odds.values()),
            Decimal("0"),
        )
        if implied_probability_sum >= Decimal("1"):
            continue

        profit_pct = ((Decimal("1") / implied_probability_sum) - Decimal("1")) * Decimal("100")
        if profit_pct < min_profit_pct:
            continue

        best_prices = {outcome: odd.price for outcome, odd in best_odds.items()}
        stakes = calculate_stakes(best_prices, bankroll) if bankroll is not None else {}
        opportunities.append(
            ArbitrageOpportunity(
                match_id=match_id,
                market_key=market_key,
                best_odds=best_odds,
                implied_probability_sum=implied_probability_sum,
                profit_pct=profit_pct,
                stakes=stakes,
            )
        )

    return sorted(opportunities, key=lambda opportunity: opportunity.profit_pct, reverse=True)


def _best_odds_by_outcome(odds: Sequence[Odd], required_outcomes: frozenset[str]) -> dict[str, Odd]:
    # Colapsa duplicatas da MESMA casa para o mesmo outcome usando a menor odd (conservadora):
    # nao da pra apostar o mesmo outcome duas vezes na mesma casa, e uma duplicata com odd alta
    # costuma ser dado stale/lixo que fabricaria um arb fantasma.
    per_bookmaker: dict[tuple[str, str], Odd] = {}
    for odd in odds:
        if odd.outcome_key not in required_outcomes:
            continue
        key = (odd.outcome_key, odd.bookmaker)
        current = per_bookmaker.get(key)
        if current is None or odd.price < current.price:
            per_bookmaker[key] = odd

    best_odds: dict[str, Odd] = {}
    for (outcome_key, _bookmaker), odd in per_bookmaker.items():
        current = best_odds.get(outcome_key)
        if current is None or odd.price > current.price:
            best_odds[outcome_key] = odd
    return best_odds
