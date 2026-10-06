"""Deterministic fictional odds for the credential-free portfolio demo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.scanner import InMemorySignalStore, SignalSnapshot
from odds_arb.core.deep_markets import (
    DeepMarketOdd,
    LineSource,
    MarketFamily,
    Metric,
    Period,
    Side,
    detect_deep_market_arbs,
)


def seed_demo_signals(store: InMemorySignalStore) -> None:
    now = datetime.now(UTC)
    kickoff = now + timedelta(days=1)
    odds = tuple(
        DeepMarketOdd(
            bookmaker=bookmaker,
            raw_event_id=f"demo-{bookmaker}",
            raw_market_id=f"demo-market-{bookmaker}",
            raw_selection_id=f"demo-{bookmaker}-{side}",
            event_name="Aurora x Horizonte",
            home_team="Aurora",
            away_team="Horizonte",
            start_time=kickoff,
            period=Period.FULL_TIME,
            market_family=MarketFamily.MATCH_TOTAL,
            metric=Metric.CORNERS,
            subject=None,
            side=Side(side),
            odd=Decimal(price),
            raw_market_name="Total de escanteios",
            raw_selection_name=side,
            line=Decimal("9.5"),
            line_source=LineSource.SELECTION_HANDICAP,
            source_event_url=None,
        )
        for bookmaker, side, price in (
            ("demo_a", "over", "2.25"),
            ("demo_a", "under", "1.75"),
            ("demo_b", "over", "1.80"),
            ("demo_b", "under", "2.10"),
        )
    )
    store.set(
        SignalSnapshot(
            opportunities=tuple(detect_deep_market_arbs(odds)),
            collected_by_house={"demo_a": 1, "demo_b": 1},
            competition_alias="demo",
            updated_at=now,
            raw_odds=odds,
        )
    )
