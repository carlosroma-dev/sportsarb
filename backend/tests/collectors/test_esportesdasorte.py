from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.esportesdasorte import (
    ESPORTESDASORTE_CATEGORY_DETAILS_BASE_URL,
    ESPORTESDASORTE_HOST,
    ESPORTESDASORTE_MARKET_1X2,
    ESPORTESDASORTE_TRADER,
    parse_esportesdasorte_payload,
)

PAYLOAD = json.loads(Path("fixtures/esportesdasorte_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/esportesdasorte_multi_market_sample.json").read_text(encoding="utf-8")
)


def test_parse_esportesdasorte_fixture_extracts_1x2_markets() -> None:
    events = parse_esportesdasorte_payload(PAYLOAD)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert ESPORTESDASORTE_HOST == "https://esportesdasorte.bet.br"
    assert ESPORTESDASORTE_CATEGORY_DETAILS_BASE_URL.endswith("/api-v2/fixture/category-details")
    assert ESPORTESDASORTE_TRADER == "esportesdasortevip"
    assert ESPORTESDASORTE_MARKET_1X2 == 7988
    assert all(event.bookmaker == "esportesdasorte" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert "1x2" in {odd.market_key for odd in odds}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_esportesdasorte_fixture_extracts_core_markets() -> None:
    events = parse_esportesdasorte_payload(MULTI_MARKET)

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


def test_parse_esportesdasorte_is_defensive_against_malformed_payloads() -> None:
    assert parse_esportesdasorte_payload({}) == []
    assert parse_esportesdasorte_payload({"data": "not-a-list"}) == []
    assert parse_esportesdasorte_payload({"data": [None, "x", [], {"cs": "wrong"}]}) == []


def test_parse_esportesdasorte_discards_live_and_past_events() -> None:
    fixture = {
        "fId": 1,
        "hcN": "Flamengo",
        "acN": "Vasco",
        "fsd": 1784743200000,
        "vld": True,
        "frz": False,
        "mDat": {"st": "Ao vivo", "sud": 120},
        "btgs": [
            {
                "btgId": 7988,
                "btgN": "Resultado",
                "fos": [
                    {"foId": 1, "pSh": "Home", "hSh": "Flamengo", "hO": 2.0},
                    {"foId": 2, "pSh": "Empate", "hSh": "Empate", "hO": 3.0},
                    {"foId": 3, "pSh": "Away", "hSh": "Vasco", "hO": 4.0},
                ],
            }
        ],
    }
    payload = {
        "data": [
            {
                "stSURL": "soccer",
                "cs": [{"cN": "Brasil", "sns": [{"seaN": "Teste", "fs": [fixture]}]}],
            }
        ]
    }

    events = parse_esportesdasorte_payload(
        payload,
        now=datetime(2026, 6, 17, 9, 0, tzinfo=UTC),
    )

    assert events == []
