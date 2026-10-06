from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from odds_arb.collectors import betano
from odds_arb.collectors.betano import (
    BETANO_DEFAULT_IMPERSONATE,
    BetanoCollector,
    parse_betano_payload,
)


def test_parse_betano_fixture_extracts_events_markets_and_odds() -> None:
    payload = json.loads(Path("fixtures/betano_sample.json").read_text(encoding="utf-8"))

    events = parse_betano_payload(payload)
    odds = [odd for event in events for odd in event.odds()]

    assert events
    assert all(event.bookmaker == "betano" for event in events)
    assert sum(len(event.markets) for event in events) > 0
    assert len(odds) > 0
    assert {odd.market_key for odd in odds} == {
        "1x2",
        "over_under_2_5",
        "both_teams_score",
        "double_chance",
    }
    assert all(odd.price > 1 for odd in odds)


def test_parse_betano_epoch_millis_start_time() -> None:
    payload = json.loads(Path("fixtures/betano_sample.json").read_text(encoding="utf-8"))

    events = parse_betano_payload(payload)
    first = next(event for event in events if event.event_id == "77353568")

    assert first.match.starts_at == datetime.fromtimestamp(1781636400000 / 1000, tz=UTC)


def test_parse_betano_btts_sample_extracts_main_yes_no_market() -> None:
    payload = json.loads(Path("fixtures/betano_btts_sample.json").read_text(encoding="utf-8"))

    events = parse_betano_payload(payload)
    odds = [odd for event in events for odd in event.odds()]

    assert {("both_teams_score", "yes"), ("both_teams_score", "no")} <= {
        (odd.market_key, odd.outcome_key) for odd in odds
    }
    assert {odd.raw_label for odd in odds if odd.market_key == "both_teams_score"} == {
        "Ambas equipes Marcam | Sim",
        "Ambas equipes Marcam | Nao",
    }


def test_betano_fetch_uses_curl_cffi_chrome_impersonation(monkeypatch: Any) -> None:
    payload = json.loads(Path("fixtures/betano_sample.json").read_text(encoding="utf-8"))
    future_start_ms = int((datetime.now(UTC) + timedelta(days=1)).timestamp() * 1000)
    raw_events = payload["data"]["topEventsV2"]["events"]
    for raw_event in raw_events.values():
        raw_event["startTime"] = future_start_ms
    calls: dict[str, object] = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            calls["raised"] = True

        def json(self) -> object:
            return payload

    class FakeSession:
        def __init__(self, *, impersonate: str) -> None:
            calls["impersonate"] = impersonate

        def __enter__(self) -> FakeSession:
            return self

        def __exit__(self, *_args: object) -> None:
            calls["closed"] = True

        def get(self, url: str, **kwargs: object) -> FakeResponse:
            calls["url"] = url
            calls["kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(betano.requests, "Session", FakeSession)

    events = asyncio.run(BetanoCollector().fetch())

    assert events
    assert calls["impersonate"] == BETANO_DEFAULT_IMPERSONATE == "chrome131"
    assert calls["url"] == "https://www.betano.bet.br/api/home/top-events-v2/"
    assert calls["raised"] is True
    assert calls["closed"] is True
