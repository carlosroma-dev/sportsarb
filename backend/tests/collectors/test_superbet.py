from __future__ import annotations

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from odds_arb.collectors.superbet import (
    SUPERBET_EVENT_DETAIL_URL,
    SUPERBET_EVENTS_BY_DATE_URL,
    SUPERBET_OFFER_HOST,
    SuperbetCollector,
    parse_superbet_payload,
)

PAYLOAD = json.loads(Path("fixtures/superbet_sample.json").read_text(encoding="utf-8"))
EVENT_DETAIL = json.loads(
    Path("fixtures/superbet_event_detail_sample.json").read_text(encoding="utf-8")
)


def test_parse_superbet_fixture_extracts_embedded_odds() -> None:
    events = parse_superbet_payload(PAYLOAD)

    assert events
    assert all(event.bookmaker == "superbet" for event in events)
    assert f"{SUPERBET_OFFER_HOST}/v2/pt-BR/events/by-date" == SUPERBET_EVENTS_BY_DATE_URL
    assert sum(len(event.odds()) for event in events) > 0
    assert {odd.market_key for event in events for odd in event.odds()} == {"1x2"}
    assert {"home", "draw", "away"}.issubset(
        {odd.outcome_key for event in events for odd in event.odds()}
    )


def test_superbet_default_window_is_72_hours() -> None:
    assert SuperbetCollector().window_hours == 72


def test_superbet_fetch_falls_back_to_list_payload_when_all_details_fail(monkeypatch) -> None:
    calls: list[str] = []
    future_payload = copy.deepcopy(PAYLOAD)
    starts_at = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    for index, event in enumerate(future_payload["data"]):
        event["utcDate"] = (
            (starts_at + timedelta(minutes=index))
            .isoformat()
            .replace(
                "+00:00",
                "Z",
            )
        )

    class FakeResponse:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return future_payload

    async def fake_get(
        self: httpx.AsyncClient,
        url: str,
        **kwargs: object,
    ) -> FakeResponse:
        calls.append(url)
        if url == SUPERBET_EVENTS_BY_DATE_URL:
            return FakeResponse()
        raise httpx.HTTPError("detail failed")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    events = asyncio.run(SuperbetCollector().fetch())
    odds = [odd for event in events for odd in event.odds()]

    assert calls[0] == SUPERBET_EVENTS_BY_DATE_URL
    assert any(call.startswith(SUPERBET_EVENT_DETAIL_URL) for call in calls[1:])
    assert events
    assert {odd.market_key for odd in odds} == {"1x2"}
    assert {"home", "draw", "away"}.issubset({odd.outcome_key for odd in odds})


def test_parse_superbet_event_detail_extracts_core_markets() -> None:
    events = parse_superbet_payload(EVENT_DETAIL)

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
    assert all("2.5" in odd.raw_label or odd.market_key != "over_under_2_5" for odd in odds)
