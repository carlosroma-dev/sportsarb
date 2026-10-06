from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from odds_arb.core.models import ArbitrageOpportunity, Match, Odd


def test_match_rejects_naive_start_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Match(
            match_id="flamengo-vasco",
            home_team="Flamengo",
            away_team="Vasco",
            starts_at=datetime(2026, 6, 15, 21, 0),
        )


def test_odd_rejects_naive_capture_time() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        Odd(
            match_id="flamengo-vasco",
            market_key="both_teams_score",
            outcome_key="yes",
            price=Decimal("2.10"),
            bookmaker="betano",
            captured_at=datetime(2026, 6, 15, 18, 0),
        )


def test_arbitrage_opportunity_rejects_naive_detection_time(make_odd) -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        ArbitrageOpportunity(
            match_id="flamengo-vasco",
            market_key="both_teams_score",
            best_odds={
                "yes": make_odd("yes", "2.20", market_key="both_teams_score"),
                "no": make_odd("no", "2.20", market_key="both_teams_score"),
            },
            implied_probability_sum=Decimal("0.90"),
            profit_pct=Decimal("11.11"),
            detected_at=datetime(2026, 6, 15, 18, 0),
        )


def test_default_datetimes_are_timezone_aware(make_odd) -> None:
    odd = make_odd("yes", "2.20", market_key="both_teams_score")
    opportunity = ArbitrageOpportunity(
        match_id="flamengo-vasco",
        market_key="both_teams_score",
        best_odds={
            "yes": odd,
            "no": make_odd("no", "2.20", "kto", market_key="both_teams_score"),
        },
        implied_probability_sum=Decimal("0.90"),
        profit_pct=Decimal("11.11"),
    )

    assert opportunity.detected_at.tzinfo is UTC
