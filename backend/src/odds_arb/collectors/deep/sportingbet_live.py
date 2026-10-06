from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from odds_arb.collectors.sportingbet import (
    CLIENT_CONFIG_HEADERS,
    DEFAULT_HEADERS,
    DEFAULT_PAGE_SIZE,
    SPORTINGBET_CLIENT_CONFIG_URL,
    SPORTINGBET_FIXTURES_URL,
    SPORTINGBET_FOOTBALL_SPORT_ID,
    SPORTINGBET_HOST,
    SPORTINGBET_PUBLIC_ACCESS_ID,
)
from odds_arb.core.dedup import canonical_team_name

DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_MAX_PAGES = 8

JsonGetter = Callable[[str, Mapping[str, str] | None], Any]


@dataclass(frozen=True)
class SportingbetListEvent:
    fixture_id: str
    match_name: str
    home_team: str
    away_team: str


def discover_sportingbet_events(payload: Mapping[str, Any]) -> list[SportingbetListEvent]:
    events: list[SportingbetListEvent] = []
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list):
        return events
    for fixture in fixtures:
        if not isinstance(fixture, Mapping):
            continue
        fixture_id = _optional_string(fixture.get("id"))
        if fixture_id is None:
            continue
        match_name = _string(_name_value(fixture), default=fixture_id)
        home, away = _extract_teams(fixture)
        if home and away:
            events.append(
                SportingbetListEvent(
                    fixture_id=fixture_id,
                    match_name=match_name,
                    home_team=home,
                    away_team=away,
                )
            )
    return events


def sportingbet_team_set(event: SportingbetListEvent) -> frozenset[str]:
    return frozenset({canonical_team_name(event.home_team), canonical_team_name(event.away_team)})


def pair_sportingbet_to_betano_universe(
    sportingbet_events: Sequence[SportingbetListEvent],
    betano_team_sets: set[frozenset[str]],
) -> list[str]:
    return [
        event.fixture_id
        for event in sportingbet_events
        if sportingbet_team_set(event) in betano_team_sets
    ]


def build_list_params(*, access_id: str, skip: int, take: int) -> dict[str, str]:
    return {
        "x-bwin-accessid": access_id,
        "lang": "pt-br",
        "country": "BR",
        "userCountry": "BR",
        "fixtureTypes": "Standard",
        "state": "Latest",
        "offerMapping": "Filtered",
        "offerCategories": "Gridable",
        "fixtureCategories": "Gridable,NonGridable,Other",
        "sportIds": SPORTINGBET_FOOTBALL_SPORT_ID,
        "isPriceBoost": "false",
        "statisticsModes": "None",
        "skip": str(skip),
        "take": str(take),
        "sortBy": "StartDate",
    }


def build_detail_params(*, access_id: str, fixture_id: str) -> dict[str, str]:
    params = build_list_params(access_id=access_id, skip=0, take=DEFAULT_PAGE_SIZE)
    params.pop("skip", None)
    params.pop("take", None)
    params["offerMapping"] = "All"
    params["fixtureIds"] = fixture_id
    return params


def fetch_sportingbet_list(
    *,
    json_getter: JsonGetter | None = None,
    access_id: str | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> Mapping[str, Any]:
    json_getter = json_getter or _default_json_getter
    resolved_access_id = access_id or _resolve_access_id(json_getter)
    fixtures: list[Mapping[str, Any]] = []
    envelope: dict[str, Any] = {}
    total_count: int | None = None
    for page_index in range(max(1, max_pages)):
        skip = page_index * page_size
        payload = json_getter(
            SPORTINGBET_FIXTURES_URL,
            build_list_params(access_id=resolved_access_id, skip=skip, take=page_size),
        )
        if not isinstance(payload, Mapping):
            break
        if not envelope:
            envelope.update(payload)
        page_fixtures = _payload_fixtures(payload)
        if not page_fixtures:
            break
        fixtures.extend(page_fixtures)
        raw_total = payload.get("totalCount")
        if isinstance(raw_total, int) and not isinstance(raw_total, bool):
            total_count = raw_total
        if total_count is not None and skip + page_size >= total_count:
            break
    envelope["fixtures"] = _deduplicate_fixtures(fixtures)
    if total_count is not None:
        envelope["totalCount"] = total_count
    return envelope


def fetch_sportingbet_detail(
    fixture_id: str,
    *,
    json_getter: JsonGetter | None = None,
    access_id: str | None = None,
) -> Mapping[str, Any] | None:
    json_getter = json_getter or _default_json_getter
    resolved_access_id = access_id or _resolve_access_id(json_getter)
    payload = json_getter(
        SPORTINGBET_FIXTURES_URL,
        build_detail_params(access_id=resolved_access_id, fixture_id=fixture_id),
    )
    if not isinstance(payload, Mapping):
        return None
    fixtures = _payload_fixtures(payload)
    return fixtures[0] if fixtures else None


def _resolve_access_id(json_getter: JsonGetter) -> str:
    params = {
        "browserUrl": f"{SPORTINGBET_HOST}/pt-br/sports",
        "x-from-product": "host-app",
    }
    try:
        payload = json_getter(SPORTINGBET_CLIENT_CONFIG_URL, params)
    except httpx.HTTPError:
        return SPORTINGBET_PUBLIC_ACCESS_ID
    if not isinstance(payload, Mapping):
        return SPORTINGBET_PUBLIC_ACCESS_ID
    for section_key in ("msConnection", "msApp"):
        section = payload.get(section_key)
        if isinstance(section, Mapping):
            access_id = _optional_string(section.get("publicAccessId"))
            if access_id is not None:
                return access_id
    return SPORTINGBET_PUBLIC_ACCESS_ID


def _default_json_getter(url: str, params: Mapping[str, str] | None) -> Any:
    headers = CLIENT_CONFIG_HEADERS if url == SPORTINGBET_CLIENT_CONFIG_URL else DEFAULT_HEADERS
    with httpx.Client(
        headers=headers,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(url, params=dict(params) if params else None)
        response.raise_for_status()
        return response.json()


def _payload_fixtures(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_fixtures = payload.get("fixtures")
    if not isinstance(raw_fixtures, list):
        return []
    return [fixture for fixture in raw_fixtures if isinstance(fixture, Mapping)]


def _deduplicate_fixtures(fixtures: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    deduplicated: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    for fixture in fixtures:
        fixture_id = _optional_string(fixture.get("id"))
        if fixture_id is not None:
            if fixture_id in seen_ids:
                continue
            seen_ids.add(fixture_id)
        deduplicated.append(fixture)
    return deduplicated


def _extract_teams(fixture: Mapping[str, Any]) -> tuple[str, str]:
    participants = fixture.get("participants")
    if isinstance(participants, list):
        home: str | None = None
        away: str | None = None
        for participant in participants:
            if not isinstance(participant, Mapping):
                continue
            participant_type = _dig(participant, "properties", "type")
            participant_name = _name_value(participant)
            if participant_type == "HomeTeam":
                home = participant_name
            elif participant_type == "AwayTeam":
                away = participant_name
        if home and away:
            return home, away

    name = _string(_name_value(fixture), default="")
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in name:
            home_name, away_name = name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return name, ""


def _name_value(raw: object) -> str | None:
    if isinstance(raw, Mapping):
        name = raw.get("name")
        if isinstance(name, Mapping):
            value = name.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = raw.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _dig(mapping: Mapping[str, Any], *keys: str) -> object:
    value: object = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
