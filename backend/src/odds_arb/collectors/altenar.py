from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from odds_arb.collectors.base import (
    AdapterCollector,
    RawEvent,
    RawMarket,
    fetch_bytes_with_retries,
    json_from_bytes,
    response_bytes,
)
from odds_arb.core.dedup import canonical_market_name, canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

ALTENAR_BASE_URL = "https://sb2frontend-altenar2.biahosted.com/api/widget"
ALTENAR_CULTURE = "pt-BR"
ALTENAR_FOOTBALL_ICON = "soccer"
ALTENAR_FOOTBALL_SPORT_ID = 66
ALTENAR_MARKET_1X2 = 1
ALTENAR_MARKET_DOUBLE_CHANCE = 10
ALTENAR_MARKET_OVER_UNDER = 18
ALTENAR_MARKET_BTTS = 29
ALTENAR_ODD_HOME = 1
ALTENAR_ODD_DRAW = 2
ALTENAR_ODD_AWAY = 3
ALTENAR_ODD_DC_HOME_DRAW = 9
ALTENAR_ODD_DC_HOME_AWAY = 10
ALTENAR_ODD_DC_DRAW_AWAY = 11
ALTENAR_ODD_OVER = 12
ALTENAR_ODD_UNDER = 13
ALTENAR_ODD_BTTS_YES = 74
ALTENAR_ODD_BTTS_NO = 76
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_CHAMP_LIMIT = 3
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}
ALTENAR_PREFERRED_CHAMP_TERMS = (
    "brasileirao a",
    "brasileirao serie a",
    "brasileirao b",
    "brasileirao serie b",
    "copa do mundo",
)
JsonObject = Mapping[str, Any]


class AltenarAdapter:
    def __init__(
        self,
        *,
        name: str,
        integration: str,
        base_url: str = ALTENAR_BASE_URL,
        culture: str = ALTENAR_CULTURE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        champ_limit: int | None = DEFAULT_CHAMP_LIMIT,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.name = name
        self.integration = integration
        self.base_url = base_url
        self.culture = culture
        self.timeout_seconds = timeout_seconds
        self.champ_limit = champ_limit
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    async def fetch(self) -> bytes:
        async def fetch_once() -> bytes:
            async with httpx.AsyncClient(
                headers={
                    **DEFAULT_HEADERS,
                    "Referer": self._referer(),
                    "Origin": self._origin(),
                },
                timeout=self.timeout_seconds,
                follow_redirects=True,
                http2=True,
            ) as client:
                menu = await self._get_menu(client)
                sport_id = _soccer_sport_id(menu)
                champ_ids = _prioritized_champ_ids(menu, limit=self.champ_limit)
                response = await client.get(
                    f"{self.base_url}/GetEvents",
                    params={
                        "culture": self.culture,
                        "timezoneOffset": 180,
                        "integration": self.integration,
                        "deviceType": 1,
                        "numFormat": "en-GB",
                        "countryCode": "BR",
                        "sportId": sport_id,
                        "champIds": ",".join(champ_ids),
                    },
                )
                response.raise_for_status()
                return response_bytes(response)

        return await fetch_bytes_with_retries(
            fetch_once,
            attempts=self.retry_attempts,
            backoff_seconds=self.retry_backoff_seconds,
        )

    def parse(
        self,
        raw: bytes,
        *,
        sport: str = "soccer",
        now: datetime | None = None,
    ) -> list[RawEvent]:
        payload = json_from_bytes(raw)
        if not isinstance(payload, Mapping):
            return []
        return parse_altenar_payload(payload, bookmaker=self.name, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    async def _get_menu(self, client: httpx.AsyncClient) -> JsonObject:
        response = await client.get(
            f"{self.base_url}/GetClickableSportMenu",
            params={
                "culture": self.culture,
                "timezoneOffset": 180,
                "integration": self.integration,
                "deviceType": 1,
                "numFormat": "en-GB",
                "countryCode": "BR",
                "period": 0,
            },
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, Mapping) else {}

    def _origin(self) -> str:
        if self.integration == "br4bet":
            return "https://www.br4.bet.br"
        if self.integration == "bateu":
            return "https://bateu.bet.br"
        return "https://www.estrelabet.bet.br"

    def _referer(self) -> str:
        return f"{self._origin()}/"


class AltenarCollector(AdapterCollector):
    def __init__(
        self,
        *,
        name: str,
        integration: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        champ_limit: int | None = DEFAULT_CHAMP_LIMIT,
    ) -> None:
        super().__init__(
            AltenarAdapter(
                name=name,
                integration=integration,
                timeout_seconds=timeout_seconds,
                champ_limit=champ_limit,
            )
        )


def parse_altenar_payload(
    payload: JsonObject,
    *,
    bookmaker: str,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    events = _object_list(payload.get("events"))
    markets_by_id = _objects_by_id(payload.get("markets"))
    odds_by_id = _objects_by_id(payload.get("odds"))
    competitors_by_id = _objects_by_id(payload.get("competitors"))
    categories_by_id = _objects_by_id(payload.get("categories"))
    champs_by_id = _objects_by_id(payload.get("champs"))

    parsed_events: list[RawEvent] = []
    for raw_event in events:
        event = _parse_event(
            raw_event,
            markets_by_id=markets_by_id,
            odds_by_id=odds_by_id,
            competitors_by_id=competitors_by_id,
            categories_by_id=categories_by_id,
            champs_by_id=champs_by_id,
            bookmaker=bookmaker,
            sport=sport,
            now=now,
        )
        if event is not None:
            parsed_events.append(event)
    return parsed_events


def _parse_event(
    raw_event: JsonObject,
    *,
    markets_by_id: Mapping[str, JsonObject],
    odds_by_id: Mapping[str, JsonObject],
    competitors_by_id: Mapping[str, JsonObject],
    categories_by_id: Mapping[str, JsonObject],
    champs_by_id: Mapping[str, JsonObject],
    bookmaker: str,
    sport: str,
    now: datetime | None,
) -> RawEvent | None:
    raw_sport_id = _optional_int(raw_event.get("sportId"))
    if sport == "soccer" and raw_sport_id not in {None, ALTENAR_FOOTBALL_SPORT_ID}:
        return None
    event_id = _optional_string(raw_event.get("id"))
    if event_id is None:
        return None
    starts_at = _parse_datetime(raw_event.get("startDate"))
    if not _is_prematch_event(raw_event, starts_at=starts_at, now=now):
        return None
    home_team, away_team = _extract_teams(raw_event, competitors_by_id)
    if home_team is None or away_team is None:
        return None
    category = categories_by_id.get(_string(raw_event.get("catId"), default=""))
    champ = champs_by_id.get(_string(raw_event.get("champId"), default=""))
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(champ.get("name")) if champ is not None else None,
        country=_optional_string(category.get("name")) if category is not None else None,
        raw_event_id=event_id,
    )
    match = temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})
    markets = _parse_markets(
        raw_event.get("marketIds"),
        markets_by_id=markets_by_id,
        odds_by_id=odds_by_id,
        match=match,
        bookmaker=bookmaker,
    )
    if not markets:
        return None
    return RawEvent(event_id=event_id, bookmaker=bookmaker, match=match, markets=markets)


def _is_prematch_event(
    raw_event: JsonObject,
    *,
    starts_at: datetime,
    now: datetime | None,
) -> bool:
    status = _optional_int(raw_event.get("status"))
    if status not in {None, 0}:
        return False
    event_type = _optional_int(raw_event.get("et"))
    if event_type not in {None, 0}:
        return False
    if raw_event.get("rc") is True:
        return False
    return now is None or starts_at > now


def _parse_markets(
    raw_market_ids: object,
    *,
    markets_by_id: Mapping[str, JsonObject],
    odds_by_id: Mapping[str, JsonObject],
    match: Match,
    bookmaker: str,
) -> list[RawMarket]:
    if not isinstance(raw_market_ids, list):
        return []
    markets: list[RawMarket] = []
    for raw_market_id in raw_market_ids:
        market = markets_by_id.get(_string(raw_market_id, default=""))
        if market is None:
            continue
        parsed_market = _parse_market(
            market,
            odds_by_id=odds_by_id,
            match=match,
            bookmaker=bookmaker,
        )
        if parsed_market is not None:
            markets.append(parsed_market)
    return markets


def _parse_market(
    raw_market: JsonObject,
    *,
    odds_by_id: Mapping[str, JsonObject],
    match: Match,
    bookmaker: str,
) -> RawMarket | None:
    market_key = _market_key(raw_market)
    if market_key is None:
        return None
    market_id = _optional_string(raw_market.get("id"))
    if market_id is None:
        return None
    label = _string(raw_market.get("name"), default=market_id)
    raw_odd_ids = raw_market.get("oddIds")
    if not isinstance(raw_odd_ids, list):
        return None
    selections = [
        odd
        for raw_odd_id in raw_odd_ids
        if (
            odd := _parse_odd(
                odds_by_id.get(_string(raw_odd_id, default="")),
                market_id=market_id,
                market_label=label,
                market_key=market_key,
                match=match,
                bookmaker=bookmaker,
            )
        )
        is not None
    ]
    if not selections:
        return None
    return RawMarket(market_id=market_id, label=label, selections=selections)


def _parse_odd(
    raw_odd: JsonObject | None,
    *,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
    bookmaker: str,
) -> Odd | None:
    if raw_odd is None:
        return None
    if _optional_int(raw_odd.get("oddStatus")) not in {None, 0}:
        return None
    selection_id = _optional_string(raw_odd.get("id"))
    if selection_id is None:
        return None
    price = _parse_price(raw_odd.get("price"))
    label = _string(raw_odd.get("name"), default=selection_id)
    outcome_key = _outcome_key(market_key, raw_odd, label, match)
    if price is None or outcome_key is None:
        return None
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=bookmaker,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(raw_market: JsonObject) -> MarketKey | None:
    type_id = _optional_int(raw_market.get("typeId"))
    if type_id == ALTENAR_MARKET_1X2:
        return "1x2"
    if type_id == ALTENAR_MARKET_OVER_UNDER and _parse_line(raw_market.get("sv")) == Decimal("2.5"):
        return "over_under_2_5"
    if type_id == ALTENAR_MARKET_BTTS:
        return "both_teams_score"
    if type_id == ALTENAR_MARKET_DOUBLE_CHANCE:
        return "double_chance"
    return None


def _outcome_key(
    market_key: MarketKey,
    raw_odd: JsonObject,
    label: str,
    match: Match,
) -> str | None:
    type_id = _optional_int(raw_odd.get("typeId"))
    if market_key == "1x2":
        if type_id == ALTENAR_ODD_HOME:
            return "home"
        if type_id == ALTENAR_ODD_DRAW:
            return "draw"
        if type_id == ALTENAR_ODD_AWAY:
            return "away"
    if market_key == "over_under_2_5":
        if type_id == ALTENAR_ODD_OVER:
            return "over"
        if type_id == ALTENAR_ODD_UNDER:
            return "under"
    if market_key == "both_teams_score":
        if type_id == ALTENAR_ODD_BTTS_YES:
            return "yes"
        if type_id == ALTENAR_ODD_BTTS_NO:
            return "no"
    if market_key == "double_chance":
        if type_id == ALTENAR_ODD_DC_HOME_DRAW:
            return "home_draw"
        if type_id == ALTENAR_ODD_DC_HOME_AWAY:
            return "home_away"
        if type_id == ALTENAR_ODD_DC_DRAW_AWAY:
            return "draw_away"
    return _outcome_key_from_label(market_key, label, match)


def _outcome_key_from_label(market_key: MarketKey, label: str, match: Match) -> str | None:
    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")
    if market_key == "1x2":
        if compact in {"empate", "draw", "x"}:
            return "draw"
        if _team_label_matches(normalized, match.home_team):
            return "home"
        if _team_label_matches(normalized, match.away_team):
            return "away"
    if market_key == "over_under_2_5":
        if normalized == "over_under_2_5:over":
            return "over"
        if normalized == "over_under_2_5:under":
            return "under"
    if market_key == "both_teams_score":
        if compact in {"sim", "yes"}:
            return "yes"
        if compact in {"nao", "no"}:
            return "no"
    if market_key == "double_chance":
        has_draw = "empate" in normalized or "draw" in normalized
        home = _team_label_matches(normalized, match.home_team)
        away = _team_label_matches(normalized, match.away_team)
        if home and has_draw:
            return "home_draw"
        if home and away:
            return "home_away"
        if has_draw and away:
            return "draw_away"
    return None


def _extract_teams(
    raw_event: JsonObject,
    competitors_by_id: Mapping[str, JsonObject],
) -> tuple[str | None, str | None]:
    raw_competitor_ids = raw_event.get("competitorIds")
    if isinstance(raw_competitor_ids, list) and len(raw_competitor_ids) >= 2:
        home = competitors_by_id.get(_string(raw_competitor_ids[0], default=""))
        away = competitors_by_id.get(_string(raw_competitor_ids[1], default=""))
        home_name = _optional_string(home.get("name")) if home is not None else None
        away_name = _optional_string(away.get("name")) if away is not None else None
        if home_name is not None and away_name is not None:
            return home_name, away_name
    name = _optional_string(raw_event.get("name"))
    if name is not None:
        return _extract_teams_from_name(name)
    return None, None


def _extract_teams_from_name(name: str) -> tuple[str, str]:
    for separator in (" vs. ", " vs ", " - ", " x ", " v "):
        if separator in name:
            home, away = name.split(separator, 1)
            return home.strip(), away.strip()
    return name, "Unknown"


def _soccer_sport_id(payload: JsonObject) -> str:
    for sport in _object_list(payload.get("sports")):
        icon_name = _optional_string(sport.get("iconName"))
        name = canonical_market_name(_optional_string(sport.get("name")) or "")
        if icon_name == ALTENAR_FOOTBALL_ICON or name == "futebol":
            sport_id = _optional_string(sport.get("id"))
            if sport_id is not None:
                return sport_id
    msg = "Altenar football sport was not found in menu"
    raise ValueError(msg)


def _prioritized_champ_ids(payload: JsonObject, *, limit: int | None) -> list[str]:
    soccer = next(
        (
            sport
            for sport in _object_list(payload.get("sports"))
            if _optional_string(sport.get("iconName")) == ALTENAR_FOOTBALL_ICON
            or canonical_market_name(_optional_string(sport.get("name")) or "") == "futebol"
        ),
        None,
    )
    soccer_cat_ids: set[int] = set()
    if soccer is not None:
        for raw_cat_id in _object_values(soccer.get("catIds")):
            parsed_cat_id = _optional_int(raw_cat_id)
            if parsed_cat_id is not None:
                soccer_cat_ids.add(parsed_cat_id)
    category_champ_ids: set[int] = set()
    for category in _object_list(payload.get("categories")):
        category_id = _optional_int(category.get("id"))
        if soccer_cat_ids and category_id not in soccer_cat_ids:
            continue
        for champ_id in _object_values(category.get("champIds")):
            parsed_champ_id = _optional_int(champ_id)
            if parsed_champ_id is not None:
                category_champ_ids.add(parsed_champ_id)

    candidates = [
        champ
        for champ in _object_list(payload.get("champs"))
        if (
            (champ_id := _optional_int(champ.get("id"))) is not None
            and (limit is None or not category_champ_ids or champ_id in category_champ_ids)
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


def _is_preferred_champ(champ: JsonObject) -> bool:
    name = canonical_market_name(_optional_string(champ.get("name")) or "")
    return any(term in name for term in ALTENAR_PREFERRED_CHAMP_TERMS)


def _object_list(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _object_values(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return value


def _objects_by_id(value: object) -> dict[str, JsonObject]:
    return {
        object_id: item
        for item in _object_list(value)
        if (object_id := _optional_string(item.get("id"))) is not None
    }


def _team_label_matches(label: str, team_name: str) -> bool:
    team = canonical_market_name(team_name)
    return label == team or label in team or team in label


def _parse_price(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not price.is_finite() or price <= Decimal("1"):
        return None
    return price


def _parse_line(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return datetime.now(UTC)


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
