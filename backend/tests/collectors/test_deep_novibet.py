from __future__ import annotations

from decimal import Decimal

from odds_arb.collectors.deep.novibet_deep import parse_novibet_deep_event
from odds_arb.collectors.deep.novibet_live import (
    discover_novibet_events,
    pair_novibet_to_betano_universe,
)
from odds_arb.core.dedup import canonical_team_name
from odds_arb.core.deep_markets import MarketFamily, Metric, Period


def _event() -> dict:
    return {
        "eventBetContextId": 46947279,
        "competitionCaption": "Copa Teste",
        "regionCaption": "Mundo",
        "startDateTime": "2026-06-24T15:00:00+00:00",
        "isLive": False,
        "path": "matches/egito-ira",
        "additionalCaptions": {
            "competitor1": "Egito",
            "competitor2": "Ira",
        },
        "markets": [
            {
                "marketId": 10,
                "betTypeSysname": "SOCCER_CORNERS_UNDER_OVER",
                "betItems": [
                    {
                        "id": 101,
                        "caption": "Mais de 9,5",
                        "code": "O",
                        "price": 1.80,
                        "isAvailable": True,
                    },
                    {
                        "id": 102,
                        "caption": "Menos de 9,5",
                        "code": "U",
                        "price": 2.20,
                        "isAvailable": True,
                    },
                ],
            },
            {
                "marketId": 11,
                "betTypeSysname": "SOCCER_CORNERS_FIRST_HALF_UNDER_OVER",
                "betItems": [
                    {
                        "id": 111,
                        "caption": "Mais de 4,5",
                        "code": "O",
                        "price": 2.05,
                        "isAvailable": True,
                    },
                    {
                        "id": 112,
                        "caption": "Menos de 4,5",
                        "code": "U",
                        "price": 1.72,
                        "isAvailable": True,
                    },
                ],
            },
            {
                "marketId": 12,
                "betTypeSysname": "SOCCER_POINT_CARDS_UNDER_OVER",
                "betItems": [
                    {
                        "id": 121,
                        "caption": "Mais de 3,5",
                        "code": "O",
                        "price": 2.05,
                        "isAvailable": True,
                    },
                    {
                        "id": 122,
                        "caption": "Menos de 3,5",
                        "code": "U",
                        "price": 1.72,
                        "isAvailable": True,
                    },
                ],
            },
        ],
    }


def test_parse_novibet_deep_event_accepts_corner_totals_only() -> None:
    results = parse_novibet_deep_event(_event())
    accepted = [result.odd for result in results if result.accepted and result.odd is not None]

    assert len(accepted) == 4
    assert {odd.metric for odd in accepted} == {Metric.CORNERS}
    assert {odd.period for odd in accepted} == {Period.FULL_TIME, Period.FIRST_HALF}
    assert all(odd.market_family is MarketFamily.MATCH_TOTAL for odd in accepted)
    assert {odd.line for odd in accepted} == {Decimal("9.5"), Decimal("4.5")}
    assert all(odd.bookmaker == "novibet" for odd in accepted)
    assert all(
        odd.source_event_url
        == "https://www.novibet.bet.br/apostas-esportivas/futebol/matches/egito-ira"
        for odd in accepted
    )


def test_parse_novibet_deep_event_accepts_detail_stats_betviews() -> None:
    event = _event() | {
        "startTimeUTC": "2026-06-24T15:00:00+00:00",
        "marketCategories": [
            {
                "groups": [
                    {
                        "betViews": [
                            {
                                "marketId": 20,
                                "marketSysname": "SOCCER_GOALKICKS_UNDER_OVER",
                                "betItems": [
                                    {
                                        "id": 201,
                                        "caption": "Mais de 17,5",
                                        "code": "O",
                                        "price": 1.91,
                                        "isAvailable": True,
                                    },
                                    {
                                        "id": 202,
                                        "caption": "Menos de 17,5",
                                        "code": "U",
                                        "price": 1.95,
                                        "isAvailable": True,
                                    },
                                ],
                            },
                            {
                                "marketId": 21,
                                "marketSysname": "SOCCER_FOULS_HOME_UNDER_OVER",
                                "betItems": [
                                    {
                                        "id": 211,
                                        "caption": "Mais de 10,5",
                                        "code": "O",
                                        "price": 2.05,
                                        "isAvailable": True,
                                    }
                                ],
                            },
                        ]
                    }
                ]
            }
        ],
    }

    results = parse_novibet_deep_event(event)
    accepted = [result.odd for result in results if result.accepted and result.odd is not None]

    assert any(odd.metric is Metric.GOAL_KICKS for odd in accepted)
    fouls = [odd for odd in accepted if odd.metric is Metric.FOULS]
    assert len(fouls) == 1
    assert fouls[0].market_family is MarketFamily.TEAM_TOTAL
    assert fouls[0].subject == "Egito"


def test_discover_novibet_events_extracts_and_pairs_by_team_set() -> None:
    payload = [{"betViews": [{"items": [_event()]}]}]
    events = discover_novibet_events(payload)

    assert len(events) == 1
    assert events[0].event_id == "46947279"
    paired = pair_novibet_to_betano_universe(
        events,
        {frozenset({canonical_team_name("Ira"), canonical_team_name("Egito")})},
    )
    assert paired == [_event()]
