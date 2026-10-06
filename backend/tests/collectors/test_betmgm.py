from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.betmgm import (
    BETMGM_BRAND,
    BETMGM_EVENTS_URL,
    BETMGM_GROUP_ID_FOOTBALL,
    parse_betmgm_payload,
)

PAYLOAD = json.loads(Path("fixtures/betmgm_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/betmgm_multi_market_sample.json").read_text(encoding="utf-8")
)


def test_parse_betmgm_fixture_extracts_1x2_markets() -> None:
    events = parse_betmgm_payload(PAYLOAD)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert BETMGM_EVENTS_URL.endswith("/program/v1/api/events")
    assert BETMGM_GROUP_ID_FOOTBALL == "11"
    assert BETMGM_BRAND == "betmgm"
    assert all(event.bookmaker == "betmgm" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert "1x2" in {odd.market_key for odd in odds}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_betmgm_fixture_extracts_core_markets() -> None:
    events = parse_betmgm_payload(MULTI_MARKET)

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


def test_parse_betmgm_is_defensive_against_malformed_payloads() -> None:
    assert parse_betmgm_payload({}) == []
    assert parse_betmgm_payload({"data": "not-a-list"}) == []
    assert parse_betmgm_payload({"data": [None, "x", [], {"id": "evt-1"}]}) == []


def test_parse_betmgm_discards_live_and_past_events() -> None:
    event = {
        "id": 1,
        "eventType": "MATCH",
        "matchState": "ONGOING",
        "sportType": "FOOTBALL",
        "startTime": "2026-06-17T10:00:00Z",
        "participants": [
            {"name": "Flamengo", "position": "HOME"},
            {"name": "Vasco", "position": "AWAY"},
        ],
        "markets": [],
    }

    events = parse_betmgm_payload(
        {"data": [event]},
        now=datetime(2026, 6, 17, 9, 0, tzinfo=UTC),
    )

    assert events == []
