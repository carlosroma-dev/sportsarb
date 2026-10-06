from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from odds_arb.collectors.altenar import (
    ALTENAR_CULTURE,
    ALTENAR_FOOTBALL_ICON,
    ALTENAR_FOOTBALL_SPORT_ID,
    DEFAULT_HEADERS,
)
from odds_arb.core.dedup import canonical_market_name, canonical_team_name

ALTENAR_WIDGET_BASE_URL = "https://sb2frontend-altenar2.biahosted.com/api/widget"
ESTRELABET_INTEGRATION = "estrelabet"
ESTRELABET_REFERER = "https://www.estrelabet.bet.br/"
DEFAULT_TIMEOUT_SECONDS = 18.0
DEFAULT_CHAMP_LIMIT = 8
DEFAULT_RETRY_ATTEMPTS = 2
PREFERRED_CHAMP_TERMS = (
    "copa do mundo",
    "brasileirao",
    "libertadores",
    "sul americana",
    "sul americana",
)

JsonGetter = Callable[[str, Mapping[str, str]], Any]


@dataclass(frozen=True)
class AltenarListEvent:
    event_id: str
    match_name: str
    home_team: str
    away_team: str


def discover_altenar_events(payload: Mapping[str, Any]) -> list[AltenarListEvent]:
    competitors_by_id = _objects_by_id(payload.get("competitors"))
    events: list[AltenarListEvent] = []
    for raw_event in _object_list(payload.get("events")):
        if _optional_int(raw_event.get("sportId")) not in {None, ALTENAR_FOOTBALL_SPORT_ID}:
            continue
        if _optional_int(raw_event.get("status")) not in {None, 0}:
            continue
        if _optional_int(raw_event.get("et")) not in {None, 0}:
            continue
        if raw_event.get("rc") is True:
            continue
        event_id = _optional_string(raw_event.get("id"))
        if event_id is None:
            continue
        match_name = _string(raw_event.get("name"), default=event_id)
        home, away = _extract_teams(raw_event, competitors_by_id, match_name)
        if home is None or away is None:
            continue
        events.append(
            AltenarListEvent(
                event_id=event_id,
                match_name=match_name,
                home_team=home,
                away_team=away,
            )
        )
    return events


def altenar_team_set(event: AltenarListEvent) -> frozenset[str]:
    return frozenset({canonical_team_name(event.home_team), canonical_team_name(event.away_team)})


def pair_altenar_to_betano_universe(
    altenar_events: Sequence[AltenarListEvent],
    betano_team_sets: set[frozenset[str]],
) -> list[str]:
    return [
        event.event_id for event in altenar_events if altenar_team_set(event) in betano_team_sets
    ]


def build_menu_params(*, integration: str = ESTRELABET_INTEGRATION) -> dict[str, str]:
    return {
        "culture": ALTENAR_CULTURE,
        "timezoneOffset": "180",
        "integration": integration,
        "deviceType": "1",
        "numFormat": "en-GB",
        "countryCode": "BR",
        "period": "0",
    }


def build_events_params(
    *,
    integration: str = ESTRELABET_INTEGRATION,
    champ_ids: Sequence[str],
    sport_id: int | str = ALTENAR_FOOTBALL_SPORT_ID,
) -> dict[str, str]:
    params = _common_params(integration=integration)
    params["sportId"] = str(sport_id)
    params["champIds"] = ",".join(champ_ids)
    return params


def build_detail_params(
    *,
    event_id: str,
    integration: str = ESTRELABET_INTEGRATION,
) -> dict[str, str]:
    params = _common_params(integration=integration)
    params["eventId"] = event_id
    return params


def fetch_estrelabet_list(
    *,
    json_getter: JsonGetter | None = None,
    champ_limit: int | None = DEFAULT_CHAMP_LIMIT,
) -> Mapping[str, Any]:
    return fetch_altenar_list(
        integration=ESTRELABET_INTEGRATION,
        json_getter=json_getter,
        champ_limit=champ_limit,
    )


def fetch_estrelabet_detail(
    event_id: str,
    *,
    json_getter: JsonGetter | None = None,
) -> Mapping[str, Any] | None:
    payload = _get_json_with_retries(
        json_getter or _default_json_getter,
        f"{ALTENAR_WIDGET_BASE_URL}/GetEventDetails",
        build_detail_params(event_id=event_id),
    )
    return payload if isinstance(payload, Mapping) else None


def fetch_altenar_list(
    *,
    integration: str,
    json_getter: JsonGetter | None = None,
    champ_limit: int | None = DEFAULT_CHAMP_LIMIT,
) -> Mapping[str, Any]:
    json_getter = json_getter or _default_json_getter
    menu = _get_json_with_retries(
        json_getter,
        f"{ALTENAR_WIDGET_BASE_URL}/GetClickableSportMenu",
        build_menu_params(integration=integration),
    )
    if not isinstance(menu, Mapping):
        return {}
    sport_id = _soccer_sport_id(menu)
    champ_ids = _preferred_champ_ids(menu, limit=champ_limit)
    if not champ_ids:
        return {}
    payload = _get_json_with_retries(
        json_getter,
        f"{ALTENAR_WIDGET_BASE_URL}/GetEvents",
        build_events_params(integration=integration, champ_ids=champ_ids, sport_id=sport_id),
    )
    return payload if isinstance(payload, Mapping) else {}


def _common_params(*, integration: str) -> dict[str, str]:
    return {
        "culture": ALTENAR_CULTURE,
        "timezoneOffset": "180",
        "integration": integration,
        "deviceType": "1",
        "numFormat": "en-GB",
        "countryCode": "BR",
    }


def _default_json_getter(url: str, params: Mapping[str, str]) -> Any:
    with httpx.Client(
        headers={
            **DEFAULT_HEADERS,
            "Referer": ESTRELABET_REFERER,
            "Origin": "https://www.estrelabet.bet.br",
        },
        timeout=DEFAULT_TIMEOUT_SECONDS,
        follow_redirects=True,
        http2=True,
    ) as client:
        response = client.get(url, params=dict(params))
        response.raise_for_status()
        return response.json()


def _get_json_with_retries(json_getter: JsonGetter, url: str, params: Mapping[str, str]) -> Any:
    last_error: Exception | None = None
    for _attempt in range(DEFAULT_RETRY_ATTEMPTS):
        try:
            return json_getter(url, params)
        except httpx.HTTPError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return json_getter(url, params)


def _soccer_sport_id(payload: Mapping[str, Any]) -> str:
    for sport in _object_list(payload.get("sports")):
        icon_name = _optional_string(sport.get("iconName"))
        name = canonical_market_name(_optional_string(sport.get("name")) or "")
        if icon_name == ALTENAR_FOOTBALL_ICON or name == "futebol":
            sport_id = _optional_string(sport.get("id"))
            if sport_id is not None:
                return sport_id
    return str(ALTENAR_FOOTBALL_SPORT_ID)


def _preferred_champ_ids(payload: Mapping[str, Any], *, limit: int | None) -> list[str]:
    soccer_cat_ids = _soccer_category_ids(payload)
    category_champ_ids: set[int] = set()
    for category in _object_list(payload.get("categories")):
        category_id = _optional_int(category.get("id"))
        if soccer_cat_ids and category_id not in soccer_cat_ids:
            continue
        for champ_id in _object_values(category.get("champIds")):
            parsed = _optional_int(champ_id)
            if parsed is not None:
                category_champ_ids.add(parsed)
    candidates = [
        champ
        for champ in _object_list(payload.get("champs"))
        if (
            (champ_id := _optional_int(champ.get("id"))) is not None
            and (not category_champ_ids or champ_id in category_champ_ids)
            and (_optional_int(champ.get("eventsCount")) or 0) > 0
        )
    ]
    preferred = [champ for champ in candidates if _is_preferred_champ(champ)]
    ordered = preferred + [champ for champ in candidates if champ not in preferred]
    champ_ids = [
        champ_id for champ in ordered if (champ_id := _optional_string(champ.get("id"))) is not None
    ]
    if limit is None:
        return champ_ids
    return champ_ids[: max(1, limit)]


def _soccer_category_ids(payload: Mapping[str, Any]) -> set[int]:
    for sport in _object_list(payload.get("sports")):
        icon_name = _optional_string(sport.get("iconName"))
        name = canonical_market_name(_optional_string(sport.get("name")) or "")
        if icon_name != ALTENAR_FOOTBALL_ICON and name != "futebol":
            continue
        return {
            parsed
            for value in _object_values(sport.get("catIds"))
            if (parsed := _optional_int(value)) is not None
        }
    return set()


def _is_preferred_champ(champ: Mapping[str, Any]) -> bool:
    name = canonical_market_name(_optional_string(champ.get("name")) or "")
    return any(term in name for term in PREFERRED_CHAMP_TERMS)


def _extract_teams(
    event: Mapping[str, Any],
    competitors_by_id: Mapping[str, Mapping[str, Any]],
    match_name: str,
) -> tuple[str | None, str | None]:
    competitor_ids = event.get("competitorIds")
    if isinstance(competitor_ids, list) and len(competitor_ids) >= 2:
        home = competitors_by_id.get(_string(competitor_ids[0], default=""))
        away = competitors_by_id.get(_string(competitor_ids[1], default=""))
        home_name = _optional_string(home.get("name")) if home is not None else None
        away_name = _optional_string(away.get("name")) if away is not None else None
        if home_name is not None and away_name is not None:
            return home_name, away_name
    for separator in (" vs. ", " vs ", " - ", " x ", " v "):
        if separator in match_name:
            home_name, away_name = match_name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return None, None


def _object_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _object_values(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _objects_by_id(value: object) -> dict[str, Mapping[str, Any]]:
    return {
        object_id: item
        for item in _object_list(value)
        if (object_id := _optional_string(item.get("id"))) is not None
    }


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
