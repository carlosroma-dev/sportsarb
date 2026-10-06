from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx

from odds_arb.collectors.base import RawEvent, RawMarket
from odds_arb.core.arbitrage import find_arbitrage_opportunities
from odds_arb.core.models import MarketKey, Match, Odd
from odds_arb.server import create_app
from odds_arb.store import save_scan


def _match(
    *,
    match_id: str = "flamengo-vasco",
    home_team: str = "Flamengo",
    away_team: str = "Vasco",
) -> Match:
    return Match(
        match_id=match_id,
        home_team=home_team,
        away_team=away_team,
        starts_at=datetime(2026, 6, 15, 21, 0, tzinfo=UTC),
        league="Brasileirao Serie A",
    )


def _odd(
    *,
    bookmaker: str,
    match_id: str,
    market_key: MarketKey,
    outcome_key: str,
    price: str,
) -> Odd:
    return Odd(
        match_id=match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=Decimal(price),
        bookmaker=bookmaker,
        captured_at=datetime.now(UTC),
        event_id=f"{bookmaker}:{match_id}",
        market_id=f"{bookmaker}:{match_id}:{market_key}",
        selection_id=f"{bookmaker}:{match_id}:{market_key}:{outcome_key}",
        raw_label=outcome_key,
    )


def _event(
    bookmaker: str,
    match: Match,
    markets: dict[MarketKey, dict[str, str]],
) -> RawEvent:
    raw_markets = [
        RawMarket(
            market_id=f"{bookmaker}:{match.match_id}:{market_key}",
            label=market_key,
            selections=[
                _odd(
                    bookmaker=bookmaker,
                    match_id=match.match_id,
                    market_key=market_key,
                    outcome_key=outcome_key,
                    price=price,
                )
                for outcome_key, price in prices.items()
            ],
        )
        for market_key, prices in markets.items()
    ]
    return RawEvent(
        event_id=f"{bookmaker}:{match.match_id}",
        bookmaker=bookmaker,
        match=match,
        markets=raw_markets,
    )


async def test_qa_route_renders_with_temp_db(tmp_path) -> None:
    db_path = tmp_path / "qa-dashboard.db"
    match = _match()
    await save_scan(
        [
            _event(
                "betano",
                match,
                {"1x2": {"home": "2.00", "draw": "3.30", "away": "4.00"}},
            )
        ],
        [],
        db_path=db_path,
    )

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/qa")

    assert response.status_code == 200
    assert "QA / Odds por casa" in response.text
    assert "Odds salvas" in response.text
    assert "betano" in response.text
    assert "Flamengo vs Vasco" in response.text


async def test_qa_marks_two_bookmakers_on_same_match(tmp_path) -> None:
    db_path = tmp_path / "qa-two-houses.db"
    match = _match()
    await save_scan(
        [
            _event(
                "betano",
                match,
                {"1x2": {"home": "2.00", "draw": "3.30", "away": "4.00"}},
            ),
            _event(
                "kto",
                match,
                {"1x2": {"home": "2.05", "draw": "3.20", "away": "3.80"}},
            ),
        ],
        [],
        db_path=db_path,
    )

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/qa", params={"multi": "true"})

    assert response.status_code == 200
    assert "2 casas" in response.text
    assert "betano" in response.text
    assert "kto" in response.text
    assert "home 2.00" in response.text
    assert "draw 3.20" in response.text
    assert "somente betano" not in response.text


async def test_qa_filters_by_market(tmp_path) -> None:
    db_path = tmp_path / "qa-market-filter.db"
    target_match = _match()
    other_match = _match(
        match_id="botafogo-fluminense",
        home_team="Botafogo",
        away_team="Fluminense",
    )
    await save_scan(
        [
            _event(
                "betano",
                target_match,
                {"both_teams_score": {"yes": "1.80", "no": "1.95"}},
            ),
            _event(
                "kto",
                other_match,
                {"1x2": {"home": "2.20", "draw": "3.00", "away": "3.40"}},
            ),
        ],
        [],
        db_path=db_path,
    )

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/qa", params={"market": "both_teams_score"})

    assert response.status_code == 200
    assert "Flamengo vs Vasco" in response.text
    assert "Botafogo vs Fluminense" not in response.text
    assert "both_teams_score" in response.text


async def test_qa_renders_latest_selection_prices(tmp_path) -> None:
    db_path = tmp_path / "qa-latest-prices.db"
    match = _match()
    await save_scan(
        [
            _event(
                "betano",
                match,
                {"1x2": {"home": "2.00", "draw": "3.00", "away": "4.00"}},
            )
        ],
        [],
        db_path=db_path,
    )
    await save_scan(
        [
            _event(
                "betano",
                match,
                {"1x2": {"home": "2.20", "draw": "3.10", "away": "4.20"}},
            )
        ],
        [],
        db_path=db_path,
    )

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/qa")

    assert response.status_code == 200
    assert "home 2.20" in response.text
    assert "draw 3.10" in response.text
    assert "away 4.20" in response.text
    assert "home 2.00" not in response.text


async def test_qa_top_candidates_show_real_and_near_arbs(tmp_path) -> None:
    db_path = tmp_path / "qa-top-candidates.db"
    real_match = _match(
        match_id="palmeiras-santos",
        home_team="Palmeiras",
        away_team="Santos",
    )
    near_match = _match(
        match_id="corinthians-sao-paulo",
        home_team="Corinthians",
        away_team="Sao Paulo",
    )
    await save_scan(
        [
            _event(
                "betano",
                real_match,
                {"1x2": {"home": "3.00", "draw": "3.00", "away": "3.00"}},
            ),
            _event(
                "kto",
                real_match,
                {"1x2": {"home": "2.00", "draw": "4.00", "away": "3.00"}},
            ),
            _event(
                "superbet",
                real_match,
                {"1x2": {"home": "2.00", "draw": "3.00", "away": "5.00"}},
            ),
            _event(
                "betano",
                near_match,
                {"1x2": {"home": "2.10", "draw": "3.00", "away": "3.00"}},
            ),
            _event(
                "kto",
                near_match,
                {"1x2": {"home": "2.00", "draw": "3.40", "away": "3.00"}},
            ),
            _event(
                "superbet",
                near_match,
                {"1x2": {"home": "2.00", "draw": "3.00", "away": "3.60"}},
            ),
        ],
        [],
        db_path=db_path,
    )

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/qa")

    assert response.status_code == 200
    assert "Top 3 quase arbs" in response.text
    assert "Palmeiras vs Santos" in response.text
    assert "arb real: lucro" in response.text
    assert "home 3.00 @ betano" in response.text
    assert "draw 4.00 @ kto" in response.text
    assert "away 5.00 @ superbet" in response.text
    assert "Corinthians vs Sao Paulo" in response.text
    assert "falta" in response.text


async def test_qa_renders_all_time_arbitrage_hall_of_fame(tmp_path) -> None:
    db_path = tmp_path / "qa-hall-of-fame.db"
    match = _match(
        match_id="palmeiras-santos-record",
        home_team="Palmeiras",
        away_team="Santos",
    )
    events = [
        _event(
            "betano",
            match,
            {"1x2": {"home": "3.00", "draw": "3.00", "away": "3.00"}},
        ),
        _event(
            "kto",
            match,
            {"1x2": {"home": "2.00", "draw": "4.00", "away": "3.00"}},
        ),
        _event(
            "superbet",
            match,
            {"1x2": {"home": "2.00", "draw": "3.00", "away": "5.00"}},
        ),
    ]
    opportunities = find_arbitrage_opportunities(
        [odd for event in events for odd in event.odds()],
        min_profit_pct=Decimal("0"),
    )
    await save_scan(events, opportunities, db_path=db_path)

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/qa")

    assert response.status_code == 200
    assert "Top 3 Arbitragens All-Time" in response.text
    assert "Recorde #1" in response.text
    assert "Palmeiras vs Santos" in response.text
    assert "+27.66%" in response.text
    assert "home" in response.text
    assert "3.00 @ betano" in response.text
    assert "draw" in response.text
    assert "4.00 @ kto" in response.text
    assert "away" in response.text
    assert "5.00 @ superbet" in response.text
    assert "Brasileirao Serie A" in response.text
    assert "Detectada em:" in response.text
