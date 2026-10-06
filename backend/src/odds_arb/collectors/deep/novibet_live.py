from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from odds_arb.collectors.base import json_from_bytes
from odds_arb.collectors.novibet import NovibetAdapter
from odds_arb.core.dedup import canonical_team_name

NOVIBET_DETAIL_ALIASES = ("CORNERS", "STATS")


@dataclass(frozen=True)
class NovibetListEvent:
    event_id: str
    home_team: str
    away_team: str
    payload: Mapping[str, Any]


def fetch_novibet_list(
    *,
    adapter: NovibetAdapter | None = None,
) -> object:
    adapter = adapter or NovibetAdapter()
    return json_from_bytes(adapter._fetch_once_bytes())


def fetch_novibet_event_detail(
    event_id: str,
    filter_alias: str,
    *,
    adapter: NovibetAdapter | None = None,
) -> Mapping[str, Any] | None:
    adapter = adapter or NovibetAdapter()
    payload = adapter.fetch_event_marketview(event_id, filter_alias=filter_alias)
    return payload if isinstance(payload, Mapping) else None


def discover_novibet_events(payload: object) -> list[NovibetListEvent]:
    events: list[NovibetListEvent] = []
    for event in _event_payloads(payload):
        if event.get("isLive") is True:
            continue
        event_id = _optional_string(event.get("eventBetContextId") or event.get("betContextId"))
        home, away = _extract_teams(event)
        if event_id is None or home is None or away is None:
            continue
        events.append(
            NovibetListEvent(
                event_id=event_id,
                home_team=home,
                away_team=away,
                payload=event,
            )
        )
    return events


def novibet_team_set(event: NovibetListEvent) -> frozenset[str]:
    return frozenset({canonical_team_name(event.home_team), canonical_team_name(event.away_team)})


def pair_novibet_to_betano_universe(
    novibet_events: Sequence[NovibetListEvent],
    betano_team_sets: set[frozenset[str]],
) -> list[Mapping[str, Any]]:
    return [
        event.payload for event in novibet_events if novibet_team_set(event) in betano_team_sets
    ]


def _event_payloads(payload: object) -> list[Mapping[str, Any]]:
    groups = payload if isinstance(payload, list) else [payload]
    events: list[Mapping[str, Any]] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        for bet_view in _object_list(group.get("betViews")):
            events.extend(_object_list(bet_view.get("items")))
            for competition in _object_list(bet_view.get("competitions")):
                events.extend(_object_list(competition.get("events")))
    return events


def _extract_teams(event: Mapping[str, Any]) -> tuple[str | None, str | None]:
    captions = event.get("additionalCaptions")
    if not isinstance(captions, Mapping):
        return None, None
    return _optional_string(captions.get("competitor1")), _optional_string(
        captions.get("competitor2")
    )


def _object_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None
