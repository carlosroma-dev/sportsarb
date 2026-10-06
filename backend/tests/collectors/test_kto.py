import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.kto import DEFAULT_HEADERS, KTO_FOOTBALL_URL, parse_kto_payload


def test_parse_kto_sample_extracts_current_list_view_core_markets() -> None:
    payload = json.loads(Path("fixtures/kto_sample.json").read_text(encoding="utf-8"))

    events = parse_kto_payload(payload)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert all(event.bookmaker == "kto" for event in events)
    assert "us.offering-api.kambicdn.com" in KTO_FOOTBALL_URL
    assert "client_id=200" in KTO_FOOTBALL_URL
    assert DEFAULT_HEADERS["Origin"] == "https://www.kto.bet.br"
    assert len(odds) > 0
    assert {odd.market_key for odd in odds} == {"1x2", "over_under_2_5"}
    assert Decimal("2.85") in {odd.price for odd in odds}


def test_parse_kto_btts_market_accepts_plain_sim_nao_outcomes() -> None:
    payload = {
        "events": [
            {
                "event": {
                    "id": 123,
                    "name": "Flamengo - Vasco",
                    "homeName": "Flamengo",
                    "awayName": "Vasco",
                    "start": "2026-06-15T21:00:00Z",
                    "group": "Brasil",
                },
                "betOffers": [
                    {
                        "id": 10,
                        "criterion": {
                            "label": "Ambos os times marcam",
                            "englishLabel": "Both Teams To Score",
                        },
                        "betOfferType": {"name": "Sim/Nao", "englishName": "Yes/No"},
                        "outcomes": [
                            {"id": 1, "label": "Sim", "odds": 1950, "status": "OPEN"},
                            {"id": 2, "label": "Nao", "odds": 1850, "status": "OPEN"},
                        ],
                    }
                ],
            }
        ]
    }

    events = parse_kto_payload(payload)

    assert {(odd.market_key, odd.outcome_key) for event in events for odd in event.odds()} == {
        ("both_teams_score", "yes"),
        ("both_teams_score", "no"),
    }


def test_parse_kto_discards_started_and_past_events_when_now_is_provided() -> None:
    payload = {
        "events": [
            {
                "event": {
                    "id": 123,
                    "name": "Flamengo - Vasco",
                    "homeName": "Flamengo",
                    "awayName": "Vasco",
                    "start": "2026-06-17T11:00:00Z",
                    "state": "STARTED",
                },
                "betOffers": [
                    {
                        "id": 10,
                        "criterion": {"label": "Resultado", "englishLabel": "Full Time"},
                        "betOfferType": {"name": "Match", "englishName": "Match"},
                        "outcomes": [
                            {"id": 1, "label": "1", "odds": 2100, "status": "OPEN"},
                            {"id": 2, "label": "X", "odds": 3300, "status": "OPEN"},
                            {"id": 3, "label": "2", "odds": 3400, "status": "OPEN"},
                        ],
                    }
                ],
            },
            {
                "event": {
                    "id": 456,
                    "name": "Botafogo - Fluminense",
                    "homeName": "Botafogo",
                    "awayName": "Fluminense",
                    "start": "2026-06-17T10:00:00Z",
                    "state": "NOT_STARTED",
                },
                "betOffers": [
                    {
                        "id": 11,
                        "criterion": {"label": "Resultado", "englishLabel": "Full Time"},
                        "betOfferType": {"name": "Match", "englishName": "Match"},
                        "outcomes": [
                            {"id": 4, "label": "1", "odds": 2100, "status": "OPEN"},
                            {"id": 5, "label": "X", "odds": 3300, "status": "OPEN"},
                            {"id": 6, "label": "2", "odds": 3400, "status": "OPEN"},
                        ],
                    }
                ],
            },
            {
                "event": {
                    "id": 789,
                    "name": "Palmeiras - Santos",
                    "homeName": "Palmeiras",
                    "awayName": "Santos",
                    "start": "2026-06-17T13:00:00Z",
                    "state": "NOT_STARTED",
                },
                "betOffers": [
                    {
                        "id": 12,
                        "criterion": {"label": "Resultado", "englishLabel": "Full Time"},
                        "betOfferType": {"name": "Match", "englishName": "Match"},
                        "outcomes": [
                            {"id": 7, "label": "1", "odds": 2100, "status": "OPEN"},
                            {"id": 8, "label": "X", "odds": 3300, "status": "OPEN"},
                            {"id": 9, "label": "2", "odds": 3400, "status": "OPEN"},
                        ],
                    }
                ],
            },
        ]
    }

    events = parse_kto_payload(payload, now=datetime(2026, 6, 17, 12, 0, tzinfo=UTC))

    assert [event.event_id for event in events] == ["789"]


def test_kto_kambi_fixture_without_bet_offer_type_is_ignored() -> None:
    payload = json.loads(Path("fixtures/kto_kambi_sample.json").read_text(encoding="utf-8"))

    assert parse_kto_payload(payload) == []
