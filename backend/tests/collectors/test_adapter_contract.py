from __future__ import annotations

from pathlib import Path

import pytest

from odds_arb.collectors.registry import REGISTRY

FIXTURES = {
    "bateubet": Path("fixtures/bateubet_sample.json"),
    "betfair": Path("fixtures/betfair_sample.json"),
    "betmgm": Path("fixtures/betmgm_sample.json"),
    "betnacional": Path("fixtures/betnacional_sample.json"),
    "betano": Path("fixtures/betano_sample.json"),
    "br4bet": Path("fixtures/br4bet_sample.json"),
    "esportesdasorte": Path("fixtures/esportesdasorte_sample.json"),
    "estrelabet": Path("fixtures/estrelabet_sample.json"),
    "kto": Path("fixtures/kto_sample.json"),
    "novibet": Path("fixtures/novibet_sample.json"),
    "pixbet": Path("fixtures/pixbet_sample.json"),
    "sportingbet": Path("fixtures/sportingbet_sample.json"),
    "superbet": Path("fixtures/superbet_sample.json"),
}


def test_registry_contains_current_bookmakers() -> None:
    assert set(REGISTRY) == {
        "bateubet",
        "betfair",
        "betmgm",
        "betnacional",
        "betano",
        "br4bet",
        "esportesdasorte",
        "estrelabet",
        "kto",
        "novibet",
        "pixbet",
        "sportingbet",
        "superbet",
    }


@pytest.mark.parametrize("name", sorted(FIXTURES))
def test_adapter_fixture_normalizes_to_canonical_schema(name: str) -> None:
    adapter = REGISTRY[name]
    raw = FIXTURES[name].read_bytes()

    events = adapter.parse(raw)

    assert events
    assert all(event.bookmaker == name for event in events)
    first_event = events[0]
    assert first_event.event_id
    assert first_event.match.match_id
    assert first_event.match.home_team
    assert first_event.match.away_team
    assert first_event.match.starts_at.tzinfo is not None
    assert first_event.markets

    odds = adapter.normalize(first_event)
    assert odds
    assert all(odd.bookmaker == name for odd in odds)
    assert all(odd.match_id == first_event.match.match_id for odd in odds)
    assert all(odd.market_key for odd in odds)
    assert all(odd.outcome_key for odd in odds)
    assert all(odd.price > 1 for odd in odds)
    assert all(odd.captured_at.tzinfo is not None for odd in odds)
