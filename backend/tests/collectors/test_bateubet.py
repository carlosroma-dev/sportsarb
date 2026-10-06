from __future__ import annotations

import json
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.altenar import (
    ALTENAR_BASE_URL,
    ALTENAR_CULTURE,
    ALTENAR_MARKET_1X2,
    AltenarAdapter,
    _prioritized_champ_ids,
    parse_altenar_payload,
)
from odds_arb.collectors.registry import REGISTRY

PAYLOAD = json.loads(Path("fixtures/bateubet_sample.json").read_text(encoding="utf-8"))
MULTI_MARKET = json.loads(
    Path("fixtures/bateubet_multi_market_sample.json").read_text(encoding="utf-8")
)


def test_parse_bateubet_fixture_extracts_1x2_markets() -> None:
    events = parse_altenar_payload(PAYLOAD, bookmaker="bateubet")
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert ALTENAR_BASE_URL.endswith("/api/widget")
    assert ALTENAR_CULTURE == "pt-BR"
    assert ALTENAR_MARKET_1X2 == 1
    assert all(event.bookmaker == "bateubet" for event in events)
    assert all(event.match.starts_at.tzinfo is not None for event in events)
    assert "1x2" in {odd.market_key for odd in odds}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})
    assert all(odd.price > Decimal("1") for odd in odds)


def test_parse_bateubet_fixture_extracts_core_markets() -> None:
    events = parse_altenar_payload(MULTI_MARKET, bookmaker="bateubet")

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


def test_bateubet_real_report_fixture_normalizes_atletico_mg_bahia() -> None:
    adapter = REGISTRY["bateubet"]

    events = adapter.parse(json.dumps(MULTI_MARKET).encode())

    assert len(events) == 1
    event = events[0]
    assert event.match.home_team == "Atlético MG"
    assert event.match.away_team == "Bahia"
    assert event.match.raw_event_id == "16027579"
    assert {(odd.market_key, odd.outcome_key, odd.price) for odd in adapter.normalize(event)} >= {
        ("1x2", "home", Decimal("2.0834")),
        ("1x2", "draw", Decimal("3.2")),
        ("1x2", "away", Decimal("3.25")),
    }


def test_bateubet_registry_disables_the_menu_championship_limit() -> None:
    adapter = REGISTRY["bateubet"]

    assert isinstance(adapter, AltenarAdapter)
    assert adapter.champ_limit is None
    assert AltenarAdapter(name="br4bet", integration="br4bet").champ_limit == 3


def test_all_championship_ids_are_selected_when_limit_is_disabled() -> None:
    menu = {
        "sports": [{"id": 66, "name": "Futebol", "iconName": "soccer", "catIds": [593]}],
        "categories": [{"id": 593, "champIds": [10, 20, 30, 40]}],
        "champs": [
            {"id": 10, "name": "Liga A", "eventsCount": 1},
            {"id": 20, "name": "Liga B", "eventsCount": 2},
            {"id": 30, "name": "Liga C", "eventsCount": 3},
            {"id": 40, "name": "Liga D", "eventsCount": 4},
            {"id": 99, "name": "Outro esporte", "eventsCount": 5},
        ],
    }

    assert _prioritized_champ_ids(menu, limit=None) == ["10", "20", "30", "40", "99"]
    assert _prioritized_champ_ids(menu, limit=3) == ["10", "20", "30"]


def test_parse_bateubet_filters_non_prematch_event_type() -> None:
    payload = deepcopy(MULTI_MARKET)
    payload["events"][0]["et"] = 1

    assert parse_altenar_payload(payload, bookmaker="bateubet") == []


def test_parse_bateubet_filters_other_sports_returned_for_all_champ_ids() -> None:
    payload = deepcopy(MULTI_MARKET)
    payload["events"][0]["sportId"] = 77

    assert parse_altenar_payload(payload, bookmaker="bateubet") == []


def test_parse_bateubet_is_defensive_against_malformed_payloads() -> None:
    assert parse_altenar_payload({}, bookmaker="bateubet") == []
    assert parse_altenar_payload({"events": "not-a-list"}, bookmaker="bateubet") == []
    assert (
        parse_altenar_payload({"events": [None, "x", [], {"id": "evt-1"}]}, bookmaker="bateubet")
        == []
    )
