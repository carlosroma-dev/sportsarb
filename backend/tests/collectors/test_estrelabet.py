from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.altenar import (
    ALTENAR_BASE_URL,
    ALTENAR_CULTURE,
    ALTENAR_MARKET_1X2,
    parse_altenar_payload,
)

PAYLOAD = json.loads(Path("fixtures/estrelabet_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/estrelabet_multi_market_sample.json").read_text(encoding="utf-8")
)


def test_parse_estrelabet_fixture_extracts_1x2_markets() -> None:
    events = parse_altenar_payload(PAYLOAD, bookmaker="estrelabet")
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert ALTENAR_BASE_URL.endswith("/api/widget")
    assert ALTENAR_CULTURE == "pt-BR"
    assert ALTENAR_MARKET_1X2 == 1
    assert all(event.bookmaker == "estrelabet" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert "1x2" in {odd.market_key for odd in odds}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_estrelabet_fixture_extracts_core_markets() -> None:
    events = parse_altenar_payload(MULTI_MARKET, bookmaker="estrelabet")

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


def test_parse_estrelabet_is_defensive_against_malformed_payloads() -> None:
    assert parse_altenar_payload({}, bookmaker="estrelabet") == []
    assert parse_altenar_payload({"events": "not-a-list"}, bookmaker="estrelabet") == []
    assert (
        parse_altenar_payload({"events": [None, "x", [], {"id": "evt-1"}]}, bookmaker="estrelabet")
        == []
    )
