from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.betnacional import (
    BETNACIONAL_MARKET_1X2,
    BETNACIONAL_ODDS_URL,
    _request_params,
    parse_betnacional_payload,
)
from odds_arb.collectors.registry import REGISTRY

PAYLOAD = json.loads(Path("fixtures/betnacional_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/betnacional_multi_market_sample.json").read_text(encoding="utf-8")
)
CATALOG_SAMPLE = Path("fixtures/betnacional_catalog_sample.json").read_bytes()


def test_parse_betnacional_fixture_extracts_1x2_markets() -> None:
    events = parse_betnacional_payload(PAYLOAD)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert BETNACIONAL_ODDS_URL.endswith("/events-by-seasons")
    assert BETNACIONAL_MARKET_1X2 == 1
    assert all(event.bookmaker == "betnacional" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert "1x2" in {odd.market_key for odd in odds}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_betnacional_fixture_extracts_core_markets() -> None:
    events = parse_betnacional_payload(MULTI_MARKET)

    assert len(events) == 1
    odds = events[0].odds()
    assert {
        ("1x2", "home"),
        ("1x2", "draw"),
        ("1x2", "away"),
        ("over_under_2_5", "over"),
        ("over_under_2_5", "under"),
        ("both_teams_score", "yes"),
        ("both_teams_score", "no"),
    }.issubset({(odd.market_key, odd.outcome_key) for odd in odds})
    over_under = [odd for odd in odds if odd.market_key == "over_under_2_5"]
    assert len(over_under) == 2
    assert all("2.5" in odd.raw_label for odd in over_under)


def test_betnacional_requests_full_catalog_without_ramp_provider() -> None:
    params = _request_params(BETNACIONAL_MARKET_1X2)

    assert params["filter_time_event"] == 4
    assert params["markets"] == BETNACIONAL_MARKET_1X2
    assert "provider" not in params


def test_betnacional_local_timestamp_is_converted_to_utc() -> None:
    events = parse_betnacional_payload(MULTI_MARKET)

    assert events[0].match.starts_at == datetime(2026, 6, 17, 22, 30, tzinfo=UTC)


def test_betnacional_report_sample_normalizes_mexico_coreia() -> None:
    adapter = REGISTRY["betnacional"]
    events = adapter.parse(CATALOG_SAMPLE)

    assert len(events) == 1
    event = events[0]
    assert event.match.home_team == "México"
    assert event.match.away_team == "Coreia do Sul"
    assert event.match.starts_at == datetime(2026, 6, 19, 1, 0, tzinfo=UTC)
    assert {(odd.outcome_key, odd.price) for odd in adapter.normalize(event)} == {
        ("home", Decimal("2.02")),
        ("draw", Decimal("3.23")),
        ("away", Decimal("3.94")),
    }


def test_parse_betnacional_is_defensive_against_malformed_payloads() -> None:
    assert parse_betnacional_payload({}) == []
    assert parse_betnacional_payload({"odds": "not-a-list"}) == []
    assert parse_betnacional_payload({"odds": [None, "x", [], {"event_id": "1"}]}) == []


def test_parse_betnacional_discards_live_and_past_events() -> None:
    base_row = {
        "event_id": 99,
        "home": "Flamengo",
        "away": "Vasco",
        "date_start": "2026-06-17 14:00:00",
        "market_id": 1,
        "outcome_id": 1,
        "outcome_code": "{$competitor1}",
        "outcome_name": "Flamengo",
        "odd": 1.8,
        "is_live": 1,
        "event_status_id": 1,
    }

    assert parse_betnacional_payload({"odds": [base_row]}) == []

    prematch_past = {**base_row, "is_live": 0, "event_status_id": 0}
    assert (
        parse_betnacional_payload(
            {"odds": [prematch_past]},
            now=datetime(2026, 6, 17, 18, 0, tzinfo=UTC),
        )
        == []
    )


def test_parse_betnacional_filters_total_line_to_2_5() -> None:
    rows = [
        {
            "event_id": 5,
            "home": "A",
            "away": "B",
            "date_start": "2027-01-01 12:00:00",
            "market_id": 18,
            "outcome_id": 12,
            "outcome_code": "over {total}",
            "outcome_name": "mais de 3.5",
            "specifier": "total=3.5",
            "odd": 2.0,
            "is_live": 0,
            "event_status_id": 0,
        },
        {
            "event_id": 5,
            "home": "A",
            "away": "B",
            "date_start": "2027-01-01 12:00:00",
            "market_id": 18,
            "outcome_id": 12,
            "outcome_code": "over {total}",
            "outcome_name": "mais de 2.5",
            "specifier": "total=2.5",
            "odd": 1.7,
            "is_live": 0,
            "event_status_id": 0,
        },
        {
            "event_id": 5,
            "home": "A",
            "away": "B",
            "date_start": "2027-01-01 12:00:00",
            "market_id": 18,
            "outcome_id": 13,
            "outcome_code": "under {total}",
            "outcome_name": "menos de 2.5",
            "specifier": "total=2.5",
            "odd": 2.1,
            "is_live": 0,
            "event_status_id": 0,
        },
    ]

    events = parse_betnacional_payload({"odds": rows})

    assert len(events) == 1
    odds = events[0].odds()
    assert {(odd.market_key, odd.outcome_key) for odd in odds} == {
        ("over_under_2_5", "over"),
        ("over_under_2_5", "under"),
    }
