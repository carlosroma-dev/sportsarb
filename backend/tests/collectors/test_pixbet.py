from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.pixbet import (
    PIXBET_HOST,
    PIXBET_IFRAME_URL,
    PIXBET_MARKET_TYPE_IDS,
    parse_pixbet_payload,
)

PAYLOAD = json.loads(Path("fixtures/pixbet_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/pixbet_multi_market_sample.json").read_text(encoding="utf-8")
)


def test_parse_pixbet_fixture_extracts_1x2_markets() -> None:
    events = parse_pixbet_payload(PAYLOAD)

    assert events
    assert all(event.bookmaker == "pixbet" for event in events)
    assert PIXBET_HOST == "https://prod20383.fssb.io"
    assert PIXBET_HOST in PIXBET_IFRAME_URL
    assert sum(len(event.odds()) for event in events) > 0
    assert {odd.market_key for event in events for odd in event.odds()} == {"1x2"}
    assert {"home", "draw", "away"}.issubset(
        {odd.outcome_key for event in events for odd in event.odds()}
    )
    assert all(odd.price > Decimal("1") for event in events for odd in event.odds())


def test_parse_pixbet_fixture_extracts_core_markets() -> None:
    events = parse_pixbet_payload(MULTI_MARKET)

    assert len(events) == 1
    assert PIXBET_MARKET_TYPE_IDS == "ML0,OU0,QA158,QA61"
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


def test_parse_pixbet_uses_home_away_participant_markers() -> None:
    events = parse_pixbet_payload(PAYLOAD)

    first = next(event for event in events if event.event_id == "784926069194293248")
    assert first.match.home_team == "Gana"
    assert first.match.away_team == "Panamá"
    # start_time at positional index 11 is ISO-UTC and must be timezone-aware.
    assert first.match.starts_at.tzinfo is not None
    odds_by_outcome = {odd.outcome_key: odd.price for odd in first.odds()}
    assert odds_by_outcome["home"] == Decimal("2.13")
    assert odds_by_outcome["draw"] == Decimal("3.21")
    assert odds_by_outcome["away"] == Decimal("3.22")


def test_parse_pixbet_is_defensive_against_malformed_positional_arrays() -> None:
    # Short arrays, wrong types, and missing markets must be skipped, not crash.
    assert parse_pixbet_payload({"data": "not-a-list"}) == []
    assert parse_pixbet_payload({"data": [[], "x", 123, None]}) == []
    assert parse_pixbet_payload({"data": [["only-id"]]}) == []
    assert parse_pixbet_payload({}) == []


def test_parse_pixbet_skips_event_when_markets_slot_is_not_a_list() -> None:
    # A full-length array whose positional markets slot drifted to a non-list (layout shift)
    # must degrade to "no event", never raise.
    event = [None] * 20
    event[0] = "evt-1"
    event[8] = [["1", {"BR-PT": "A"}, "Home"], ["2", {"BR-PT": "B"}, "Away"]]
    event[11] = "2026-06-17T23:00:00.000Z"
    event[19] = {"unexpected": "object-not-list"}
    assert parse_pixbet_payload({"data": [event]}) == []
