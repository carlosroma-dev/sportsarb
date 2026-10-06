from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from odds_arb.collectors.kto import DEFAULT_HEADERS
from odds_arb.core.dedup import canonical_team_name

KTO_BASE_URL = "https://us.offering-api.kambicdn.com/offering/v2018/ktobr"
KTO_ALL_MATCHES_PATH = "listView/football/all/all/all/matches.json"
KTO_EVENT_DETAIL_PATH = "betoffer/event/{event_id}.json"
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_RETRY_ATTEMPTS = 2

JsonGetter = Callable[[str], Any]

_QUERY = {
    "lang": "pt_BR",
    "market": "BR",
    "client_id": "200",
    "channel_id": "1",
    "useCombined": "true",
}
_VIRTUAL_NOISE = re.compile(
    r"esport|esports|cla|\([^)]*\)|\b2x\d\b|laiking|uncle|wboy|gammi|kozak|mqr|space|r0ge|aloha",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KtoListEvent:
    event_id: str
    match_name: str
    home_team: str
    away_team: str


def discover_kto_events(payload: Mapping[str, Any]) -> list[KtoListEvent]:
    events: list[KtoListEvent] = []
    wrappers = payload.get("events")
    if not isinstance(wrappers, list):
        return events
    for wrapper in wrappers:
        if not isinstance(wrapper, Mapping):
            continue
        event = wrapper.get("event")
        if not isinstance(event, Mapping):
            continue
        event_id = _optional_string(event.get("id"))
        if event_id is None:
            continue
        match_name = _string(event.get("name"), default=event_id)
        group = _optional_string(event.get("group")) or ""
        home, away = _extract_teams(event, match_name)
        if not home or not away:
            continue
        if _is_virtual_or_noise(match_name, group, home, away):
            continue
        events.append(
            KtoListEvent(
                event_id=event_id,
                match_name=match_name,
                home_team=home,
                away_team=away,
            )
        )
    return events


def kto_team_set(event: KtoListEvent) -> frozenset[str]:
    return frozenset({canonical_team_name(event.home_team), canonical_team_name(event.away_team)})


def pair_kto_to_betano_universe(
    kto_events: Sequence[KtoListEvent],
    betano_team_sets: set[frozenset[str]],
) -> list[str]:
    return [event.event_id for event in kto_events if kto_team_set(event) in betano_team_sets]


def build_list_url() -> str:
    return _url(KTO_ALL_MATCHES_PATH)


def build_detail_url(event_id: str) -> str:
    return _url(KTO_EVENT_DETAIL_PATH.format(event_id=event_id))


def fetch_kto_events_deep(*, json_getter: JsonGetter | None = None) -> Mapping[str, Any]:
    json_getter = json_getter or _default_json_getter
    payload = _get_json_with_retries(json_getter, build_list_url())
    return payload if isinstance(payload, Mapping) else {}


def fetch_kto_detail(
    event_id: str,
    *,
    json_getter: JsonGetter | None = None,
) -> Mapping[str, Any] | None:
    json_getter = json_getter or _default_json_getter
    payload = _get_json_with_retries(json_getter, build_detail_url(event_id))
    return payload if isinstance(payload, Mapping) else None


def _url(path: str) -> str:
    return f"{KTO_BASE_URL}/{path}?{urlencode(_QUERY)}"


def _default_json_getter(url: str) -> Any:
    with httpx.Client(
        headers=DEFAULT_HEADERS,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.json()


def _get_json_with_retries(json_getter: JsonGetter, url: str) -> Any:
    last_error: Exception | None = None
    for _attempt in range(DEFAULT_RETRY_ATTEMPTS):
        try:
            return json_getter(url)
        except httpx.HTTPError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return json_getter(url)


def _is_virtual_or_noise(match_name: str, group: str, home: str, away: str) -> bool:
    haystack = " ".join((match_name, group, home, away))
    return bool(_VIRTUAL_NOISE.search(haystack))


def _extract_teams(event: Mapping[str, Any], match_name: str) -> tuple[str, str]:
    home = _optional_string(event.get("homeName"))
    away = _optional_string(event.get("awayName"))
    if home is not None and away is not None:
        return home, away
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in match_name:
            home_name, away_name = match_name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return match_name, ""


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
