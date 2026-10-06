from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from odds_arb.core.models import MarketKey, Match, Odd


@pytest.fixture
def kickoff() -> datetime:
    return datetime(2026, 6, 15, 21, 0, tzinfo=UTC)


@pytest.fixture
def make_match(kickoff: datetime) -> Callable[..., Match]:
    def _make_match(
        home_team: str = "Flamengo",
        away_team: str = "Vasco",
        starts_at: datetime | None = None,
        *,
        match_id: str = "flamengo-vasco",
        league: str = "Brasileirao Serie A",
    ) -> Match:
        return Match(
            match_id=match_id,
            home_team=home_team,
            away_team=away_team,
            starts_at=starts_at or kickoff,
            league=league,
        )

    return _make_match


@pytest.fixture
def make_odd() -> Callable[..., Odd]:
    def _make_odd(
        outcome_key: str,
        price: str | Decimal,
        bookmaker: str = "betano",
        *,
        match_id: str = "flamengo-vasco",
        market_key: MarketKey = "1x2",
    ) -> Odd:
        return Odd(
            match_id=match_id,
            market_key=market_key,
            outcome_key=outcome_key,
            price=Decimal(str(price)),
            bookmaker=bookmaker,
            captured_at=datetime(2026, 6, 15, 18, 0, tzinfo=UTC),
            event_id=f"{bookmaker}:{match_id}",
            market_id=f"{bookmaker}:{match_id}:{market_key}",
            selection_id=f"{bookmaker}:{match_id}:{market_key}:{outcome_key}",
        )

    return _make_odd
