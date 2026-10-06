from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.novibet import (
    NOVIBET_HOST,
    NOVIBET_SOCCER_GROUP_ID,
    parse_novibet_payload,
)

UPCOMING = json.loads(Path("fixtures/novibet_sample.json").read_text(encoding="utf-8"))
IN_PLAY = json.loads(Path("fixtures/novibet_inplay_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/novibet_multi_market_sample.json").read_text(encoding="utf-8")
)


def test_parse_novibet_upcoming_events_extracts_supported_markets() -> None:
    events = parse_novibet_payload(UPCOMING)

    assert events
    assert all(event.bookmaker == "novibet" for event in events)
    assert NOVIBET_HOST == "https://www.novibet.bet.br"
    assert str(NOVIBET_SOCCER_GROUP_ID) == "4372606"
    assert {odd.market_key for event in events for odd in event.odds()} == {"1x2", "double_chance"}
    assert "over_under_2_5" not in {odd.market_key for event in events for odd in event.odds()}
    assert {"home", "draw", "away"}.issubset(
        {odd.outcome_key for event in events for odd in event.odds()}
    )
    assert all(odd.price > Decimal("1") for event in events for odd in event.odds())


def test_parse_novibet_fixture_extracts_core_markets() -> None:
    events = parse_novibet_payload(MULTI_MARKET)

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
    assert all("2,5" in odd.raw_label or odd.market_key != "over_under_2_5" for odd in odds)


def test_parse_novibet_maps_codes_and_keeps_timezone_aware_start() -> None:
    events = parse_novibet_payload(UPCOMING)

    first = next(event for event in events if event.event_id == "46642602")
    assert first.match.home_team == "Rochedale Rovers"
    assert first.match.away_team == "Moreton City Excelsior FC"
    assert first.match.starts_at.tzinfo is not None
    odds_by_outcome = {odd.outcome_key: odd.price for odd in first.odds()}
    assert odds_by_outcome["home"] == Decimal("5.0")
    assert odds_by_outcome["draw"] == Decimal("4.7")
    assert odds_by_outcome["away"] == Decimal("1.45")


def test_parse_novibet_handles_competitions_container() -> None:
    # The in-play / league view nests events under betViews[].competitions[].events[].
    events = parse_novibet_payload(IN_PLAY)

    assert events
    assert all(event.bookmaker == "novibet" for event in events)
    assert {"home", "draw", "away"}.issubset(
        {odd.outcome_key for event in events for odd in event.odds()}
    )
    # League comes from the competition caption when present.
    assert any(event.match.league for event in events)


def test_parse_novibet_is_defensive_against_malformed_payloads() -> None:
    assert parse_novibet_payload([]) == []
    assert parse_novibet_payload([{"betViews": "nope"}]) == []
    assert parse_novibet_payload([{"betViews": [{"items": [{"markets": []}]}]}]) == []
    assert parse_novibet_payload({"unexpected": "object"}) == []
