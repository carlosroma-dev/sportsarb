from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from odds_arb.collectors.betfair import (
    BETFAIR_BFF_URL,
    BETFAIR_CARD_DOCUMENT_FALLBACK,
    BetfairAdapter,
    _build_events_from_coupon,
    _competition_links,
    _extract_app_key,
    _extract_card_document_id,
    _page_info,
    parse_betfair_payload,
)
from odds_arb.collectors.registry import REGISTRY

PAYLOAD = json.loads(Path("fixtures/betfair_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/betfair_multi_market_sample.json").read_text(encoding="utf-8")
)
CATALOG_SAMPLE = json.loads(
    Path("fixtures/betfair_catalog_sample.json").read_text(encoding="utf-8")
)


def test_parse_betfair_fixture_extracts_1x2_markets() -> None:
    events = parse_betfair_payload(PAYLOAD)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert BETFAIR_BFF_URL.endswith("/bff-gql/v11/")
    assert BETFAIR_CARD_DOCUMENT_FALLBACK.startswith("Card#")
    assert all(event.bookmaker == "betfair" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert "1x2" in {odd.market_key for odd in odds}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_betfair_fixture_extracts_totals_and_1x2() -> None:
    events = parse_betfair_payload(MULTI_MARKET)

    assert len(events) == 1
    odds = events[0].odds()
    market_keys = {odd.market_key for odd in odds}
    assert "1x2" in market_keys
    assert "over_under_2_5" in market_keys
    assert {("1x2", "home"), ("1x2", "draw"), ("1x2", "away")}.issubset(
        {(odd.market_key, odd.outcome_key) for odd in odds}
    )
    over_under = [odd for odd in odds if odd.market_key == "over_under_2_5"]
    assert {odd.outcome_key for odd in over_under} == {"over", "under"}
    assert all("2,5" in odd.raw_label for odd in over_under)


def test_parse_betfair_maps_both_teams_score() -> None:
    # Betfair tags BTTS as BOTH_TEAMS_TO_SCORE with named Sim/Nao runners; the World Cup
    # coupon available at capture time did not offer it, so this exercises the mapping
    # against the real wire shape.
    payload = {
        "events": [
            {
                "eventId": "evt-1",
                "openDate": "2027-01-01T18:00:00.000Z",
                "home": "Flamengo",
                "away": "Vasco",
                "inplay": False,
                "markets": [
                    {
                        "marketId": "ppb:sbkMarket:1.1",
                        "marketType": "BOTH_TEAMS_TO_SCORE",
                        "name": "Ambas Equipes Marcam",
                        "line": None,
                        "runners": [
                            {"selectionId": "1", "name": "Sim", "role": None, "price": 1.8},
                            {"selectionId": "2", "name": "Nao", "role": None, "price": 1.95},
                        ],
                    }
                ],
            }
        ]
    }

    events = parse_betfair_payload(payload)

    assert len(events) == 1
    odds = events[0].odds()
    assert {(odd.market_key, odd.outcome_key) for odd in odds} == {
        ("both_teams_score", "yes"),
        ("both_teams_score", "no"),
    }


def test_betfair_extracts_dynamic_bootstrap_values() -> None:
    page = """
    <script>window.__PRELOADED_STATE__ = {"entities":{"appkey":"dynamicKey123"}}</script>
    """
    bundle = 'getCards("Card#0123456789abcdef0123456789abcdef"); FullCard#ffff'

    assert _extract_app_key(page) == "dynamicKey123"
    assert _extract_card_document_id(bundle) == "Card#0123456789abcdef0123456789abcdef"


def test_betfair_extracts_football_competition_links() -> None:
    payload = {
        "data": {
            "Cards": [
                {
                    "competitionRegions": [
                        {
                            "competitionViewLinks": [
                                {
                                    "viewLink": {
                                        "viewUrl": "futebol/usl/c-141",
                                        "viewUrn": "ppb:tbd:view:competition:141",
                                    },
                                    "competition": {
                                        "name": "EUA - United Soccer League",
                                        "sport": {"sportId": 1},
                                    },
                                },
                                {
                                    "viewLink": {
                                        "viewUrl": "tenis/atp/c-2",
                                        "viewUrn": "ppb:tbd:view:competition:2",
                                    },
                                    "competition": {
                                        "name": "ATP",
                                        "sport": {"sportId": 2},
                                    },
                                },
                            ]
                        }
                    ]
                }
            ]
        }
    }

    assert _competition_links(payload) == [
        {
            "name": "EUA - United Soccer League",
            "view_url": "futebol/usl/c-141",
            "view_urn": "ppb:tbd:view:competition:141",
        }
    ]


def test_betfair_reads_cursor_page_info() -> None:
    payload = {
        "data": {
            "Cards": [
                {
                    "full": {
                        "pageInfo": {
                            "hasNextPage": True,
                            "endCursor": "next-page",
                        }
                    }
                }
            ]
        }
    }

    assert _page_info(payload) == (True, "next-page")


@pytest.mark.asyncio
async def test_betfair_posts_card_document_id_instead_of_old_persisted_query() -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return {"data": {"Cards": []}}

    class FakeClient:
        async def post(self, url: str, *, params: object, json: object) -> FakeResponse:
            captured.update({"url": url, "params": params, "json": json})
            return FakeResponse()

    adapter = BetfairAdapter()
    await adapter._post_card(  # type: ignore[arg-type]
        FakeClient(),
        app_key="app-key",
        document_id="Card#document",
        urn="ppb:tbd:card:test",
    )

    body = captured["json"]
    assert isinstance(body, dict)
    assert body["documentId"] == "Card#document"
    assert "extensions" not in body


def test_betfair_report_sample_normalizes_indy_eleven() -> None:
    adapter = REGISTRY["betfair"]
    events = _build_events_from_coupon(CATALOG_SAMPLE)
    raw = json.dumps({"events": events}).encode()

    parsed = adapter.parse(raw)

    assert len(parsed) == 1
    event = parsed[0]
    assert event.match.home_team == "Indy Eleven"
    assert event.match.away_team == "Brooklyn FC"
    assert event.match.league == "EUA - United Soccer League"
    assert {odd.outcome_key for odd in adapter.normalize(event)} == {"home", "draw", "away"}
    assert next(odd.price for odd in event.odds() if odd.outcome_key == "home") == Decimal("1.93")


def test_parse_betfair_is_defensive_against_malformed_payloads() -> None:
    assert parse_betfair_payload({}) == []
    assert parse_betfair_payload({"events": "not-a-list"}) == []
    assert parse_betfair_payload({"events": [None, "x", [], {"eventId": "1"}]}) == []


def test_parse_betfair_discards_live_and_past_events() -> None:
    live_event = {
        "eventId": "live-1",
        "openDate": "2026-06-17T20:00:00.000Z",
        "home": "Inglaterra",
        "away": "Croacia",
        "inplay": True,
        "markets": [
            {
                "marketId": "m",
                "marketType": "MATCH_ODDS",
                "name": "Resultado da partida",
                "runners": [
                    {"selectionId": "1", "name": None, "role": "home", "price": 1.95},
                    {"selectionId": "2", "name": None, "role": "draw", "price": 3.4},
                    {"selectionId": "3", "name": None, "role": "away", "price": 4.0},
                ],
            }
        ],
    }
    past_event = {**live_event, "eventId": "past-1", "inplay": False}

    assert parse_betfair_payload({"events": [live_event]}) == []
    assert (
        parse_betfair_payload(
            {"events": [past_event]},
            now=datetime(2026, 6, 17, 21, 0, tzinfo=UTC),
        )
        == []
    )
