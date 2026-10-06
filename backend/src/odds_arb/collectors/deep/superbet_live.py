from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from odds_arb.collectors.superbet import DEFAULT_HEADERS, SUPERBET_OFFER_HOST
from odds_arb.core.dedup import canonical_team_name

SUPERBET_BY_DATE_URL = f"{SUPERBET_OFFER_HOST}/v2/pt-BR/events/by-date"
SUPERBET_DETAIL_URL = f"{SUPERBET_OFFER_HOST}/v2/pt-BR/events"
SUPERBET_SPORT_ID_FOOTBALL = "5"
DEFAULT_TIMEOUT_SECONDS = 8.0

JsonGetter = Callable[[str, Mapping[str, str] | None], Any]


@dataclass(frozen=True)
class SuperbetListEvent:
    event_id: str
    match_name: str


def _teams_from_match_name(match_name: str) -> tuple[str, str]:
    for sep in ("·", " - ", " vs ", " x ", " v "):
        if sep in match_name:
            home, away = match_name.split(sep, 1)
            return home.strip(), away.strip()
    return match_name.strip(), ""


def superbet_team_set(match_name: str) -> frozenset[str]:
    home, away = _teams_from_match_name(match_name)
    teams = {canonical_team_name(home)}
    if away:
        teams.add(canonical_team_name(away))
    return frozenset(teams)


def discover_superbet_events(list_payload: Mapping[str, Any]) -> list[SuperbetListEvent]:
    data = list_payload.get("data")
    if not isinstance(data, list):
        return []
    events: list[SuperbetListEvent] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        event_id = str(item.get("eventId") or "")
        match_name = str(item.get("matchName") or "")
        if event_id and match_name:
            events.append(SuperbetListEvent(event_id=event_id, match_name=match_name))
    return events


def pair_to_betano_universe(
    superbet_events: Sequence[SuperbetListEvent],
    betano_team_sets: set[frozenset[str]],
) -> list[str]:
    return [
        event.event_id
        for event in superbet_events
        if superbet_team_set(event.match_name) in betano_team_sets
    ]


def extract_superbet_event(detail_payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    data = detail_payload.get("data")
    if isinstance(data, list) and data and isinstance(data[0], Mapping):
        return data[0]
    return None


def build_by_date_params(start: datetime, end: datetime) -> dict[str, str]:
    return {
        "currentStatus": "active",
        "offerState": "prematch",
        "startDate": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endDate": end.strftime("%Y-%m-%d %H:%M:%S"),
        "sportId": SUPERBET_SPORT_ID_FOOTBALL,
    }


def _default_json_getter(url: str, params: Mapping[str, str] | None) -> Any:
    with httpx.Client(
        headers=DEFAULT_HEADERS,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(url, params=dict(params) if params else None)
        response.raise_for_status()
        return response.json()


def fetch_superbet_list(
    start: datetime,
    end: datetime,
    *,
    json_getter: JsonGetter = _default_json_getter,
) -> Mapping[str, Any]:
    payload = json_getter(SUPERBET_BY_DATE_URL, build_by_date_params(start, end))
    return payload if isinstance(payload, Mapping) else {}


def fetch_superbet_detail(
    event_id: str,
    *,
    json_getter: JsonGetter = _default_json_getter,
) -> Mapping[str, Any] | None:
    payload = json_getter(f"{SUPERBET_DETAIL_URL}/{event_id}", None)
    if isinstance(payload, Mapping):
        return extract_superbet_event(payload)
    return None
