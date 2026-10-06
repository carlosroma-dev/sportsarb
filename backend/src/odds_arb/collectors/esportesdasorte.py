from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Mapping
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
    json_payload_bytes,
)
from odds_arb.core.dedup import canonical_market_name, canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

ESPORTESDASORTE_HOST = "https://esportesdasorte.bet.br"
ESPORTESDASORTE_TRADER_DEFAULTS_URL = (
    f"{ESPORTESDASORTE_HOST}/api/generic/getTraderDefaults/esportesdasorte.bet.br/w"
)
ESPORTESDASORTE_LEFT_MENU_BASE_URL = f"{ESPORTESDASORTE_HOST}/api-v2/left-menu"
ESPORTESDASORTE_CATEGORY_DETAILS_BASE_URL = (
    f"{ESPORTESDASORTE_HOST}/api-v2/fixture/category-details"
)
ESPORTESDASORTE_BRAGI_URL = "https://bragi.sportingtech.com/"
ESPORTESDASORTE_TRADER = "esportesdasortevip"
ESPORTESDASORTE_LANGUAGE_ID = "23"
ESPORTESDASORTE_DEVICE = "d"
ESPORTESDASORTE_FOOTBALL_SLUG = "soccer"
ESPORTESDASORTE_DEFAULT_BET_TYPE_GROUP_LIMIT = 20
ESPORTESDASORTE_MARKET_1X2 = 7988
ESPORTESDASORTE_MARKET_OVER_UNDER = 7689
ESPORTESDASORTE_MARKET_BTTS = 8038
ESPORTESDASORTE_MARKET_DOUBLE_CHANCE = 7982
ESPORTESDASORTE_PREFERRED_LEAGUES = (
    ("brazil", "brasileiro-serie-a-2026"),
    ("brazil", "brasileiro-serie-b-2026"),
    ("international", "world-cup-2026"),
    ("international", "fifa-world-cup-group-a"),
)
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_LEAGUE_LIMIT = 3
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": ESPORTESDASORTE_HOST,
    "Referer": f"{ESPORTESDASORTE_HOST}/ptb/bet/main",
    "customorigin": ESPORTESDASORTE_HOST,
    "languageid": ESPORTESDASORTE_LANGUAGE_ID,
    "device": ESPORTESDASORTE_DEVICE,
}
JsonObject = Mapping[str, Any]


class EsportesdasorteAdapter:
    name = "esportesdasorte"

    def __init__(
        self,
        *,
        trader_defaults_url: str = ESPORTESDASORTE_TRADER_DEFAULTS_URL,
        left_menu_base_url: str = ESPORTESDASORTE_LEFT_MENU_BASE_URL,
        category_details_base_url: str = ESPORTESDASORTE_CATEGORY_DETAILS_BASE_URL,
        trader: str = ESPORTESDASORTE_TRADER,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        league_limit: int = DEFAULT_LEAGUE_LIMIT,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.trader_defaults_url = trader_defaults_url
        self.left_menu_base_url = left_menu_base_url
        self.category_details_base_url = category_details_base_url
        self.trader = trader
        self.timeout_seconds = timeout_seconds
        self.league_limit = league_limit
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
                defaults = await self._trader_defaults(client)
                bragi_url = (
                    _optional_string(defaults.get("tBU"))
                    or _optional_string(defaults.get("bragiUrl"))
                    or ESPORTESDASORTE_BRAGI_URL
                )
                group_limit = _optional_int(defaults.get("tBTGL"))
                if group_limit is None:
                    group_limit = ESPORTESDASORTE_DEFAULT_BET_TYPE_GROUP_LIMIT

                leagues = await self._discover_leagues(client, bragi_url)
                payloads: list[JsonObject] = []
                source_urls: list[str] = []
                for country_slug, season_slug in leagues[: max(1, self.league_limit)]:
                    url = self._category_details_url(
                        country_slug=country_slug,
                        season_slug=season_slug,
                        group_limit=group_limit,
                    )
                    response = await client.get(
                        url,
                        headers=_headers_with_body(
                            bragi_url,
                            _category_details_body(
                                bragi_url=bragi_url,
                                group_limit=group_limit,
                            ),
                        ),
                    )
                    response.raise_for_status()
                    payload = response.json()
                    if isinstance(payload, Mapping):
                        payloads.append(payload)
                        source_urls.append(url)

            return json_payload_bytes(_combine_payloads(payloads, source_urls))

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
        return parse_esportesdasorte_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    async def _trader_defaults(self, client: httpx.AsyncClient) -> JsonObject:
        response = await client.get(self.trader_defaults_url)
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, Mapping) else None
        return data if isinstance(data, Mapping) else {}

    async def _discover_leagues(
        self,
        client: httpx.AsyncClient,
        bragi_url: str,
    ) -> list[tuple[str, str]]:
        body: JsonObject = {"requestBody": {}}
        encoded_body = _encoded_body(body)
        response = await client.get(
            f"{self.left_menu_base_url}/{ESPORTESDASORTE_DEVICE}/"
            f"{ESPORTESDASORTE_LANGUAGE_ID}/{self.trader}/{encoded_body}",
            headers=_headers_with_body(bragi_url, body),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return list(ESPORTESDASORTE_PREFERRED_LEAGUES)
        discovered = _discover_soccer_leagues(payload)
        if not discovered:
            return list(ESPORTESDASORTE_PREFERRED_LEAGUES)
        return _prioritize_leagues(discovered)

    def _category_details_url(
        self,
        *,
        country_slug: str,
        season_slug: str,
        group_limit: int,
    ) -> str:
        return (
            f"{self.category_details_base_url}/{ESPORTESDASORTE_DEVICE}/"
            f"{ESPORTESDASORTE_LANGUAGE_ID}/{self.trader}/null/false/no-ante/"
            f"{group_limit}/{ESPORTESDASORTE_FOOTBALL_SLUG}/{country_slug}/{season_slug}"
        )


class EsportesdasorteCollector(AdapterCollector):
    name = EsportesdasorteAdapter.name

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        league_limit: int = DEFAULT_LEAGUE_LIMIT,
    ) -> None:
        super().__init__(
            EsportesdasorteAdapter(
                timeout_seconds=timeout_seconds,
                league_limit=league_limit,
            )
        )


def parse_esportesdasorte_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    parsed_events: list[RawEvent] = []
    for raw_fixture, context in _iter_fixture_contexts(payload):
        event = _parse_event(raw_fixture, context, sport=sport, now=now)
        if event is not None:
            parsed_events.append(event)
    return parsed_events


def _parse_event(
    raw_fixture: JsonObject,
    context: JsonObject,
    *,
    sport: str,
    now: datetime | None,
) -> RawEvent | None:
    event_id = _optional_string(raw_fixture.get("fId"))
    if event_id is None:
        return None
    home_team = _optional_string(raw_fixture.get("hcN"))
    away_team = _optional_string(raw_fixture.get("acN"))
    if home_team is None or away_team is None:
        return None
    starts_at = _parse_datetime(raw_fixture.get("fsd"))
    if not _is_prematch_event(raw_fixture, starts_at=starts_at, now=now):
        return None
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(context.get("lName") or context.get("seaN")),
        country=_optional_string(context.get("cN")),
        raw_event_id=event_id,
    )
    match = temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})
    markets = _parse_markets(raw_fixture.get("btgs"), match)
    if not markets:
        return None
    return RawEvent(
        event_id=event_id,
        bookmaker=EsportesdasorteCollector.name,
        match=match,
        markets=markets,
    )


def _is_prematch_event(
    raw_fixture: JsonObject,
    *,
    starts_at: datetime,
    now: datetime | None,
) -> bool:
    if raw_fixture.get("vld") is False:
        return False
    if raw_fixture.get("frz") is True or raw_fixture.get("ante") is True:
        return False
    live_state = raw_fixture.get("lSt")
    if live_state is True:
        return False
    match_data = raw_fixture.get("mDat")
    if isinstance(match_data, Mapping):
        status = canonical_market_name(_optional_string(match_data.get("st")) or "")
        if status and status not in {"por iniciar", "not started", "nao iniciado"}:
            return False
        seconds = match_data.get("sud")
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
            return False
    return now is None or starts_at > now


def _parse_markets(raw_markets: object, match: Match) -> list[RawMarket]:
    if not isinstance(raw_markets, list):
        return []
    markets: list[RawMarket] = []
    for raw_market in raw_markets:
        if not isinstance(raw_market, Mapping):
            continue
        market = _parse_market(raw_market, match)
        if market is not None:
            markets.append(market)
    return markets


def _parse_market(raw_market: JsonObject, match: Match) -> RawMarket | None:
    market_key = _market_key(raw_market)
    if market_key is None:
        return None
    market_id = _optional_string(raw_market.get("btgId")) or _optional_string(
        raw_market.get("btgN")
    )
    if market_id is None:
        return None
    label = _string(raw_market.get("btgN"), default=market_id)
    raw_selections = raw_market.get("fos")
    if not isinstance(raw_selections, list):
        return None
    selections = [
        odd
        for raw_selection in raw_selections
        if (
            odd := _parse_selection(
                raw_selection=raw_selection,
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


def _parse_selection(
    *,
    raw_selection: object,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    if not isinstance(raw_selection, Mapping):
        return None
    if raw_selection.get("vld") is False or raw_selection.get("frz") is True:
        return None
    if market_key == "over_under_2_5" and _parse_line(raw_selection.get("sv")) != Decimal("2.5"):
        return None
    price = _parse_price(raw_selection.get("hO"))
    label = _selection_label(raw_selection)
    outcome_key = _outcome_key(market_key, label, raw_selection, match)
    if price is None or outcome_key is None:
        return None
    selection_id = _optional_string(raw_selection.get("foId")) or f"{market_id}:{outcome_key}"
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=EsportesdasorteCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(raw_market: JsonObject) -> MarketKey | None:
    market_id = _optional_int(raw_market.get("btgId"))
    if market_id == ESPORTESDASORTE_MARKET_1X2:
        return "1x2"
    if market_id == ESPORTESDASORTE_MARKET_OVER_UNDER:
        return "over_under_2_5"
    if market_id == ESPORTESDASORTE_MARKET_BTTS:
        return "both_teams_score"
    if market_id == ESPORTESDASORTE_MARKET_DOUBLE_CHANCE:
        return "double_chance"
    return None


def _outcome_key(
    market_key: MarketKey,
    label: str,
    raw_selection: JsonObject,
    match: Match,
) -> str | None:
    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")
    provider_short = canonical_market_name(_optional_string(raw_selection.get("pSh")) or "")
    if market_key == "1x2":
        if provider_short == "home" or compact in {"casa", "home", "1"}:
            return "home"
        if provider_short == "away" or compact in {"fora", "away", "2"}:
            return "away"
        if provider_short == "empate" or compact in {"empate", "draw", "x"}:
            return "draw"
    if market_key == "over_under_2_5":
        if (
            provider_short == "over_under_2_5:under"
            or normalized == "over_under_2_5:under"
            or provider_short.startswith("under")
        ):
            return "under"
        if (
            provider_short == "over_under_2_5:over"
            or normalized == "over_under_2_5:over"
            or provider_short.startswith("over")
        ):
            return "over"
    if market_key == "both_teams_score":
        if compact in {"sim", "yes"} or provider_short == "yes":
            return "yes"
        if compact in {"nao", "no"} or provider_short == "no":
            return "no"
    if market_key == "double_chance":
        return _double_chance_outcome(normalized, provider_short, match)
    return None


def _double_chance_outcome(
    normalized: str,
    provider_short: str,
    match: Match,
) -> str | None:
    text = f"{normalized} {provider_short}"
    has_draw = "empate" in text or "draw" in text
    has_home = "casa" in text or "home" in text or _team_label_matches(text, match.home_team)
    has_away = "fora" in text or "away" in text or _team_label_matches(text, match.away_team)
    if has_home and has_draw:
        return "home_draw"
    if has_home and has_away:
        return "home_away"
    if has_draw and has_away:
        return "draw_away"
    return None


def _iter_fixture_contexts(payload: JsonObject) -> Iterable[tuple[JsonObject, JsonObject]]:
    raw_data = payload.get("data")
    if not isinstance(raw_data, list):
        return
    for sport_node in raw_data:
        if not isinstance(sport_node, Mapping):
            continue
        for country_node in _children(sport_node, "cs"):
            country_name = country_node.get("cN")
            for season_node in _iter_seasons(country_node):
                context = {
                    "cN": country_name,
                    "seaN": season_node.get("seaN"),
                    "lName": season_node.get("lName"),
                }
                for raw_fixture in _children(season_node, "fs"):
                    yield raw_fixture, context


def _iter_seasons(node: JsonObject) -> Iterable[JsonObject]:
    for season_node in _children(node, "sns"):
        yield season_node
        yield from _iter_seasons(season_node)


def _children(node: JsonObject, key: str) -> list[JsonObject]:
    raw_children = node.get(key)
    if not isinstance(raw_children, list):
        return []
    return [child for child in raw_children if isinstance(child, Mapping)]


def _discover_soccer_leagues(payload: JsonObject) -> list[tuple[str, str]]:
    leagues: list[tuple[str, str]] = []
    raw_sports = payload.get("data")
    if not isinstance(raw_sports, list):
        return leagues
    for sport_node in raw_sports:
        if not isinstance(sport_node, Mapping):
            continue
        if _optional_string(sport_node.get("stSURL")) != ESPORTESDASORTE_FOOTBALL_SLUG:
            continue
        for country_node in _children(sport_node, "cs"):
            country_slug = _optional_string(country_node.get("cSURL"))
            if country_slug is None:
                continue
            for season_node in _iter_seasons(country_node):
                if _optional_int(season_node.get("fCnt")) in {None, 0}:
                    continue
                season_slug = _optional_string(season_node.get("seaSURL"))
                if season_slug is not None:
                    leagues.append((country_slug, season_slug))
    return leagues


def _prioritize_leagues(discovered: list[tuple[str, str]]) -> list[tuple[str, str]]:
    discovered_set = set(discovered)
    ordered: list[tuple[str, str]] = [
        preferred for preferred in ESPORTESDASORTE_PREFERRED_LEAGUES if preferred in discovered_set
    ]
    for league in discovered:
        if league not in ordered:
            ordered.append(league)
    return ordered


def _combine_payloads(payloads: list[JsonObject], source_urls: list[str]) -> JsonObject:
    combined_data: list[object] = []
    for payload in payloads:
        raw_data = payload.get("data")
        if isinstance(raw_data, list):
            combined_data.extend(raw_data)
    return {
        "success": bool(combined_data),
        "responseCodes": [],
        "data": combined_data,
        "type": "aggregated",
        "sourceUrls": source_urls,
    }


def _category_details_body(*, bragi_url: str, group_limit: int) -> JsonObject:
    return {
        "requestBody": {
            "betTypeGroupLimit": group_limit,
            "bragiUrl": bragi_url,
        }
    }


def _headers_with_body(bragi_url: str, body: JsonObject) -> dict[str, str]:
    encoded_body = _encoded_body(body)
    return {
        **DEFAULT_HEADERS,
        "bragiurl": bragi_url,
        "encodedbody": encoded_body,
    }


def _encoded_body(body: JsonObject) -> str:
    return base64.b64encode(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _selection_label(raw_selection: JsonObject) -> str:
    for key in ("hSh", "pSh", "btN", "oc"):
        value = _optional_string(raw_selection.get(key))
        if value is not None:
            return value
    return "selection"


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
    try:
        return Decimal(str(value)).quantize(Decimal("0.1"))
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
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
