from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from odds_arb.collectors.registry import REGISTRY
from odds_arb.collectors.sportingbet import (
    SPORTINGBET_FIXTURES_URL,
    SPORTINGBET_FOOTBALL_SPORT_ID,
    SPORTINGBET_HOST,
    SportingbetAdapter,
    parse_sportingbet_payload,
)

PAYLOAD = json.loads(Path("fixtures/sportingbet_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/sportingbet_multi_market_sample.json").read_text(encoding="utf-8")
)
CATALOG_SAMPLE = Path("fixtures/sportingbet_catalog_sample.json").read_bytes()


def test_parse_sportingbet_fixture_extracts_1x2_markets() -> None:
    events = parse_sportingbet_payload(PAYLOAD)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert SPORTINGBET_HOST == "https://www.sportingbet.bet.br"
    assert SPORTINGBET_FIXTURES_URL.endswith("/cds-api/bettingoffer/fixtures")
    assert SPORTINGBET_FOOTBALL_SPORT_ID == "4"
    assert all(event.bookmaker == "sportingbet" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert {odd.market_key for odd in odds} == {"1x2"}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_sportingbet_fixture_extracts_core_markets() -> None:
    events = parse_sportingbet_payload(MULTI_MARKET)

    assert len(events) == 1
    odds = events[0].odds()
    assert {odd.market_key for odd in odds} == {
        "1x2",
        "over_under_2_5",
        "both_teams_score",
        "double_chance",
    }
    assert {(odd.market_key, odd.outcome_key) for odd in odds} == {
        ("1x2", "home"),
        ("1x2", "draw"),
        ("1x2", "away"),
        ("over_under_2_5", "over"),
        ("over_under_2_5", "under"),
        ("both_teams_score", "yes"),
        ("both_teams_score", "no"),
        ("double_chance", "home_draw"),
        ("double_chance", "home_away"),
        ("double_chance", "draw_away"),
    }
    assert len([odd for odd in odds if odd.market_key == "over_under_2_5"]) == 2
    assert all("2.5" in odd.raw_label for odd in odds if odd.market_key == "over_under_2_5")


def test_sportingbet_full_catalog_parameters() -> None:
    adapter = SportingbetAdapter()

    params = adapter._params(access_id="public-id", skip=50, take=50)

    assert params["offerMapping"] == "Filtered"
    assert params["offerCategories"] == "Gridable"
    assert params["fixtureCategories"] == "Gridable,NonGridable,Other"
    assert params["sortBy"] == "StartDate"
    assert params["skip"] == "50"
    assert params["take"] == "50"
    assert "fixtureIds" not in params


@pytest.mark.asyncio
async def test_sportingbet_paginates_until_total_count(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = SportingbetAdapter(page_size=2, max_pages=10, page_delay_seconds=0)
    requested_skips: list[str] = []

    async def fake_get_json(client: object, params: dict[str, str]) -> object:
        del client
        requested_skips.append(params["skip"])
        skip = int(params["skip"])
        fixtures = [{"id": f"fixture-{index}"} for index in range(skip, min(skip + 2, 5))]
        return {"fixtures": fixtures, "totalCount": 5}

    monkeypatch.setattr(adapter, "_get_json", fake_get_json)

    payload = await adapter._fetch_all_pages(object(), "public-id")  # type: ignore[arg-type]

    assert requested_skips == ["0", "2", "4"]
    assert payload["totalCount"] == 5
    assert len(payload["fixtures"]) == 5


def test_sportingbet_report_sample_normalizes_vilavelhense() -> None:
    adapter = REGISTRY["sportingbet"]
    events = adapter.parse(CATALOG_SAMPLE)

    assert len(events) == 1
    event = events[0]
    assert event.match.home_team == "Vilavelhense FC"
    assert event.match.away_team == "Vitória ES"
    assert {(odd.outcome_key, odd.price) for odd in adapter.normalize(event)} == {
        ("home", Decimal("6.5")),
        ("draw", Decimal("2.65")),
        ("away", Decimal("1.65")),
    }


def test_parse_sportingbet_is_defensive_against_malformed_payloads() -> None:
    assert parse_sportingbet_payload({}) == []
    assert parse_sportingbet_payload({"fixtures": "not-a-list"}) == []
    assert parse_sportingbet_payload({"fixtures": [None, "x", [], {"id": "evt-1"}]}) == []


def test_parse_sportingbet_discards_live_and_past_events() -> None:
    fixture = {
        "id": "evt-1",
        "name": {"value": "Flamengo - Vasco"},
        "participants": [
            {"name": {"value": "Flamengo"}, "properties": {"type": "HomeTeam"}},
            {"name": {"value": "Vasco"}, "properties": {"type": "AwayTeam"}},
        ],
        "stage": "Live",
        "isOpenForBetting": True,
        "scoreboard": {"started": True, "periodId": 1},
        "startDate": "2026-06-17T10:00:00Z",
        "optionMarkets": [
            {
                "id": "m1",
                "name": {"value": "Resultado da Partida"},
                "parameters": [
                    {"key": "Happening", "value": "Goal"},
                    {"key": "MarketType", "value": "3way"},
                    {"key": "Period", "value": "RegularTime"},
                ],
                "options": [
                    {
                        "id": "o1",
                        "status": "Visible",
                        "name": {"value": "Flamengo"},
                        "price": {"odds": 2.0},
                    }
                ],
            }
        ],
    }

    events = parse_sportingbet_payload(
        {"fixtures": [fixture]},
        now=datetime(2026, 6, 17, 9, 0, tzinfo=UTC),
    )

    assert events == []
