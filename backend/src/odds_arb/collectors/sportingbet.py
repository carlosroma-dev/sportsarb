from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import structlog

from odds_arb.collectors.base import (
    AdapterCollector,
    RawEvent,
    RawMarket,
    fetch_bytes_with_retries,
    json_from_bytes,
    json_payload_bytes,
)
from odds_arb.core.dedup import canonical_market_name, canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

SPORTINGBET_HOST = "https://www.sportingbet.bet.br"
SPORTINGBET_CLIENT_CONFIG_URL = f"{SPORTINGBET_HOST}/pt-br/api/clientconfig"
SPORTINGBET_FIXTURES_URL = f"{SPORTINGBET_HOST}/cds-api/bettingoffer/fixtures"
SPORTINGBET_PUBLIC_ACCESS_ID = "YTRhMjczYjctNTBlNy00MWZlLTliMGMtMWNkOWQxMThmZTI2"
SPORTINGBET_FOOTBALL_SPORT_ID = "4"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_PAGES = 20
DEFAULT_PAGE_DELAY_SECONDS = 0.1
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": SPORTINGBET_HOST,
    "Referer": f"{SPORTINGBET_HOST}/pt-br/sports/futebol-4",
}
CLIENT_CONFIG_HEADERS = {
    **DEFAULT_HEADERS,
    "x-bwin-browser-url": f"{SPORTINGBET_HOST}/pt-br/sports",
    "x-bwin-sports-api": "prod",
    "x-from-product": "host-app",
}
JsonObject = Mapping[str, Any]

logger = structlog.get_logger(__name__)


class SportingbetAdapter:
    name = "sportingbet"

    def __init__(
        self,
        *,
        url: str = SPORTINGBET_FIXTURES_URL,
        client_config_url: str = SPORTINGBET_CLIENT_CONFIG_URL,
        access_id: str = SPORTINGBET_PUBLIC_ACCESS_ID,
        sport_id: str = SPORTINGBET_FOOTBALL_SPORT_ID,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.url = url
        self.client_config_url = client_config_url
        self.access_id = access_id
        self.sport_id = sport_id
        self.timeout_seconds = timeout_seconds
        self.page_size = page_size
        self.max_pages = max_pages
        self.page_delay_seconds = page_delay_seconds
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    async def fetch(self) -> bytes:
        async def fetch_once() -> bytes:
            async with httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                http2=True,
            ) as client:
                access_id = await self._resolve_access_id(client)
                payload = await self._fetch_all_pages(client, access_id)
            return json_payload_bytes(payload)

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
        return parse_sportingbet_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    async def _resolve_access_id(self, client: httpx.AsyncClient) -> str:
        params = {
            "browserUrl": f"{SPORTINGBET_HOST}/pt-br/sports",
            "x-from-product": "host-app",
        }
        try:
            response = await client.get(
                self.client_config_url,
                params=params,
                headers=CLIENT_CONFIG_HEADERS,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("collector.fetch.config_failed", collector=self.name, error=str(exc))
            return self.access_id
        if not isinstance(payload, Mapping):
            return self.access_id
        return _extract_access_id(payload) or self.access_id

    async def _get_json(self, client: httpx.AsyncClient, params: Mapping[str, str]) -> object:
        response = await client.get(self.url, params=params)
        response.raise_for_status()
        return response.json()

    async def _fetch_all_pages(
        self,
        client: httpx.AsyncClient,
        access_id: str,
    ) -> JsonObject:
        fixtures: list[JsonObject] = []
        envelope: dict[str, Any] = {}
        total_count: int | None = None
        page_size = max(1, self.page_size)
        for page_index in range(max(1, self.max_pages)):
            skip = page_index * page_size
            payload = await self._get_json(
                client,
                self._params(access_id=access_id, skip=skip, take=page_size),
            )
            if not isinstance(payload, Mapping):
                break
            if not envelope:
                envelope.update(payload)
            page_fixtures = _payload_fixtures(payload)
            if not page_fixtures:
                break
            fixtures.extend(page_fixtures)
            raw_total_count = payload.get("totalCount")
            if isinstance(raw_total_count, int) and not isinstance(raw_total_count, bool):
                total_count = raw_total_count
            if total_count is not None and skip + page_size >= total_count:
                break
            if self.page_delay_seconds > 0:
                await asyncio.sleep(self.page_delay_seconds)
        envelope["fixtures"] = _deduplicate_fixtures(fixtures)
        if total_count is not None:
            envelope["totalCount"] = total_count
        return envelope

    def _params(
        self,
        *,
        access_id: str,
        skip: int,
        take: int,
    ) -> dict[str, str]:
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
            "sportIds": self.sport_id,
            "isPriceBoost": "false",
            "statisticsModes": "None",
            "skip": str(skip),
            "take": str(take),
            "sortBy": "StartDate",
        }


class SportingbetCollector(AdapterCollector):
    name = SportingbetAdapter.name

    def __init__(
        self,
        *,
        url: str = SPORTINGBET_FIXTURES_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        super().__init__(
            SportingbetAdapter(
                url=url,
                timeout_seconds=timeout_seconds,
                page_size=page_size,
                max_pages=max_pages,
            )
        )


def parse_sportingbet_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    parsed_events: list[RawEvent] = []
    for fixture in _payload_fixtures(payload):
        event = _parse_event(fixture, sport=sport, now=now)
        if event is not None:
            parsed_events.append(event)
    return parsed_events


def _parse_event(fixture: JsonObject, *, sport: str, now: datetime | None) -> RawEvent | None:
    event_id = _optional_string(fixture.get("id") or fixture.get("sourceId"))
    if event_id is None:
        return None
    match = _parse_match(event_id, fixture, sport=sport)
    if not _is_prematch_event(fixture, starts_at=match.starts_at, now=now):
        return None
    markets = _parse_markets(fixture, match)
    if not markets:
        return None
    return RawEvent(
        event_id=event_id,
        bookmaker=SportingbetCollector.name,
        match=match,
        markets=markets,
    )


def _parse_match(event_id: str, fixture: JsonObject, *, sport: str) -> Match:
    home_team, away_team = _extract_teams(fixture)
    starts_at = _parse_datetime(fixture.get("startDate"))
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_name_value(fixture.get("competition")),
        country=_name_value(fixture.get("region")),
        raw_event_id=event_id,
    )
    return temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})


def _is_prematch_event(
    fixture: JsonObject,
    *,
    starts_at: datetime,
    now: datetime | None,
) -> bool:
    if fixture.get("isOpenForBetting") is False:
        return False
    stage = _optional_string(fixture.get("stage"))
    if stage is not None and stage.lower() not in {"prematch", "notstarted", "not_started"}:
        return False
    addons = fixture.get("addons")
    if isinstance(addons, Mapping) and addons.get("isResulted") is True:
        return False
    scoreboard = fixture.get("scoreboard")
    if isinstance(scoreboard, Mapping):
        if scoreboard.get("started") is True:
            return False
        period_id = scoreboard.get("periodId")
        if isinstance(period_id, int) and not isinstance(period_id, bool) and period_id != 0:
            return False
        timer = scoreboard.get("timer")
        if isinstance(timer, Mapping) and timer.get("running") is True:
            return False
    return now is None or starts_at > now


def _parse_markets(fixture: JsonObject, match: Match) -> list[RawMarket]:
    markets: list[RawMarket] = []
    for raw_market in _market_payloads(fixture):
        market = _parse_market(raw_market, match)
        if market is not None:
            markets.append(market)
    return markets


def _parse_market(raw_market: object, match: Match) -> RawMarket | None:
    if not isinstance(raw_market, Mapping):
        return None
    market_id = _optional_string(raw_market.get("id"))
    if market_id is None:
        return None
    label = _string(_name_value(raw_market), default=market_id)
    market_key = _market_key(raw_market, label)
    if market_key is None:
        return None
    raw_options = raw_market.get("options")
    if not isinstance(raw_options, list):
        raw_options = raw_market.get("results")
    if not isinstance(raw_options, list):
        return None
    selections = [
        odd
        for raw_option in raw_options
        if (
            odd := _parse_option(
                raw_option=raw_option,
                market_id=market_id,
                market_label=label,
                market_key=market_key,
                match=match,
            )
        )
        is not None
    ]
    if not selections:
        return None
    return RawMarket(market_id=market_id, label=label, selections=selections)


def _parse_option(
    *,
    raw_option: object,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    if not isinstance(raw_option, Mapping):
        return None
    if _optional_string(raw_option.get("status")) not in {None, "Visible"}:
        return None
    label = _string(_name_value(raw_option), default=str(raw_option.get("id") or "selection"))
    price = _parse_price(raw_option.get("price"))
    outcome_key = _outcome_key(market_key, label, raw_option, match)
    if price is None or outcome_key is None:
        return None
    selection_id = _optional_string(raw_option.get("id")) or f"{market_id}:{outcome_key}"
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=SportingbetCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(raw_market: JsonObject, label: str) -> MarketKey | None:
    parameters = _parameters(raw_market)
    market_type = _optional_string(parameters.get("MarketType"))
    happening = _optional_string(parameters.get("Happening"))
    period = _optional_string(parameters.get("Period"))
    if happening not in {None, "Goal"}:
        return None
    if period not in {None, "RegularTime"}:
        return None
    if _optional_string(parameters.get("RangeValue")) is not None:
        return None
    if market_type == "3way" and _optional_string(parameters.get("MarketSubType")) in {None, "2Up"}:
        return "1x2"
    if (
        market_type == "Over/Under"
        and _parse_line(parameters.get("DecimalValue")) == Decimal("2.5")
        and _optional_string(parameters.get("FixtureParticipant")) is None
    ):
        return "over_under_2_5"
    if market_type == "BTTS":
        return "both_teams_score"
    if market_type == "DoubleChance":
        return "double_chance"

    normalized = canonical_market_name(label)
    if normalized in {"resultado da partida", "resultado final", "1x2"}:
        return "1x2"
    if normalized == "total de gols" and _parse_line(parameters.get("DecimalValue")) == Decimal(
        "2.5"
    ):
        return "over_under_2_5"
    if normalized == "chance dupla":
        return "double_chance"
    return None


def _outcome_key(
    market_key: MarketKey,
    label: str,
    raw_option: JsonObject,
    match: Match,
) -> str | None:
    option_types = _option_types(raw_option)
    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")

    if market_key == "1x2":
        if "Draw" in option_types or compact in {"x", "empate", "draw"}:
            return "draw"
        if _team_label_matches(normalized, match.home_team):
            return "home"
        if _team_label_matches(normalized, match.away_team):
            return "away"
    if market_key == "over_under_2_5":
        if "Over" in option_types or normalized.startswith("over_under_2_5:over"):
            return "over"
        if "Under" in option_types or normalized.startswith("over_under_2_5:under"):
            return "under"
    if market_key == "both_teams_score":
        if "ToHappen" in option_types or compact in {"sim", "yes"}:
            return "yes"
        if "NotToHappen" in option_types or compact in {"nao", "no"}:
            return "no"
    if market_key == "double_chance":
        has_draw = "Draw" in option_types or "empate" in normalized or " x " in f" {label.lower()} "
        home = _team_label_matches(normalized, match.home_team)
        away = _team_label_matches(normalized, match.away_team)
        if has_draw and home:
            return "home_draw"
        if has_draw and away:
            return "draw_away"
        if home and away:
            return "home_away"
        if "ou" in normalized and _team_label_matches(normalized, match.home_team):
            if _team_label_matches(normalized, match.away_team):
                return "home_away"
            return "home_draw"
        if "ou" in normalized and _team_label_matches(normalized, match.away_team):
            return "draw_away"
    return None


def _extract_access_id(payload: JsonObject) -> str | None:
    for section_key in ("msConnection", "msApp"):
        section = payload.get(section_key)
        if isinstance(section, Mapping):
            access_id = _optional_string(section.get("publicAccessId"))
            if access_id is not None:
                return access_id
    return None


def _payload_fixtures(payload: JsonObject) -> list[JsonObject]:
    raw_fixtures = payload.get("fixtures")
    if not isinstance(raw_fixtures, list):
        return []
    return [fixture for fixture in raw_fixtures if isinstance(fixture, Mapping)]


def _deduplicate_fixtures(fixtures: list[JsonObject]) -> list[JsonObject]:
    deduplicated: list[JsonObject] = []
    seen_ids: set[str] = set()
    for fixture in fixtures:
        fixture_id = _optional_string(fixture.get("id"))
        if fixture_id is not None:
            if fixture_id in seen_ids:
                continue
            seen_ids.add(fixture_id)
        deduplicated.append(fixture)
    return deduplicated


def _market_payloads(fixture: JsonObject) -> list[object]:
    markets: list[object] = []
    option_markets = fixture.get("optionMarkets")
    if isinstance(option_markets, list):
        markets.extend(option_markets)
    games = fixture.get("games")
    if isinstance(games, list):
        markets.extend(games)
    return markets


def _parameters(raw: JsonObject) -> dict[str, object]:
    raw_parameters = raw.get("parameters")
    if isinstance(raw_parameters, Mapping):
        return {str(key): value for key, value in raw_parameters.items()}
    if not isinstance(raw_parameters, list):
        return {}
    parameters: dict[str, object] = {}
    for item in raw_parameters:
        if not isinstance(item, Mapping):
            continue
        key = _optional_string(item.get("key"))
        if key is not None:
            parameters[key] = item.get("value")
    return parameters


def _option_types(raw_option: JsonObject) -> set[str]:
    parameters = _parameters(raw_option)
    raw_option_types = parameters.get("optionTypes")
    if not isinstance(raw_option_types, list):
        return set()
    return {
        option_type
        for item in raw_option_types
        if (option_type := _optional_string(item)) is not None
    }


def _extract_teams(fixture: JsonObject) -> tuple[str, str]:
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

    name = _string(_name_value(fixture), default="Unknown v Unknown")
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in name:
            home_name, away_name = name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return name, "Unknown"


def _team_label_matches(label: str, team_name: str) -> bool:
    team = canonical_market_name(team_name)
    return label == team or label in team or team in label


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


def _parse_price(value: object) -> Decimal | None:
    if isinstance(value, Mapping):
        value = value.get("odds")
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
    try:
        return Decimal(str(value)).quantize(Decimal("0.1"))
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


def _dig(mapping: JsonObject, *keys: str) -> object:
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
