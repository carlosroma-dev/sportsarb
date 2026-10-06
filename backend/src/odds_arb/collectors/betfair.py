from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin

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

BETFAIR_HOST = "https://www.betfair.bet.br"
BETFAIR_SPORT_PAGE = f"{BETFAIR_HOST}/apostas/futebol/s-1"
BETFAIR_APP_KEY = "K61C39rIC0WKzoQ7"
BETFAIR_BFF_URL = "https://apitbd.betfair.bet.br/api/tbd/bff-gql/v11/"
BETFAIR_CARD_DOCUMENT_FALLBACK = "Card#e790c877715333c5873badebe1846e23"
BETFAIR_COMPETITION_REGION_FALLBACK = "ppb:tbd:card:competitionRegion:ZxDgMhIAACEAf2RV/s/1"
# Betfair tags the 3-way result market with several names (live vs prematch / 2-Up).
BETFAIR_MATCH_RESULT_TYPES = ("MATCH_ODDS", "FULL_TIME_RESULT")
BETFAIR_OVER_UNDER_25_TYPE = "OVER_UNDER_25"
BETFAIR_BTTS_TYPE = "BOTH_TEAMS_TO_SCORE"
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_COUPON_CARD_LIMIT = 50
DEFAULT_MAX_COMPETITIONS = 100
DEFAULT_MAX_PAGES_PER_COMPETITION = 10
DEFAULT_REQUEST_DELAY_SECONDS = 0.1
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Origin": BETFAIR_HOST,
    "Referer": BETFAIR_SPORT_PAGE,
}
_COUPON_URN_RE = re.compile(r"ppb:tbd:cardgroup:coupon:[^\"<\s]+")
_COMPETITION_REGION_URN_RE = re.compile(r"ppb:tbd:card:competitionRegion:[^\"<\s]+")
_NAVIGATION_TAB_URN_RE = re.compile(r"ppb:tbd:view:navigationTab:[^\"<\s]+")
_CARD_DOCUMENT_RE = re.compile(r"(?<![A-Za-z])Card#[a-f0-9]{16,}")
_APP_KEY_RE = re.compile(r"_ak=([A-Za-z0-9]{8,})")
_STATE_APP_KEY_RE = re.compile(r'"appkey"\s*:\s*"([A-Za-z0-9]{8,})"')
_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+)")
JsonObject = Mapping[str, Any]

logger = structlog.get_logger(__name__)


class BetfairAdapter:
    """Betfair (.bet.br) sportsbook adapter.

    Betfair serves a card-based GraphQL BFF. ``fetch`` discovers the current app key,
    Card document id and football competition catalogue, then enumerates every
    competition coupon. The first implementation intentionally collects only the
    primary 1x2 market; opening the per-event "Gols" tab for totals/BTTS is deferred.
    """

    name = "betfair"

    def __init__(
        self,
        *,
        host: str = BETFAIR_HOST,
        bff_url: str = BETFAIR_BFF_URL,
        app_key: str = BETFAIR_APP_KEY,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        coupon_card_limit: int = DEFAULT_COUPON_CARD_LIMIT,
        max_competitions: int = DEFAULT_MAX_COMPETITIONS,
        max_pages_per_competition: int = DEFAULT_MAX_PAGES_PER_COMPETITION,
        request_delay_seconds: float = DEFAULT_REQUEST_DELAY_SECONDS,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.host = host
        self.bff_url = bff_url
        self.app_key = app_key
        self.timeout_seconds = timeout_seconds
        self.coupon_card_limit = coupon_card_limit
        self.max_competitions = max_competitions
        self.max_pages_per_competition = max_pages_per_competition
        self.request_delay_seconds = request_delay_seconds
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
                page = await self._get_text(client, f"{self.host}/apostas/futebol/s-1")
                app_key = _extract_app_key(page) or self.app_key
                document_id = await self._resolve_document_id(client, page)
                competition_region_urn = await self._resolve_competition_region_urn(
                    client,
                    page=page,
                    app_key=app_key,
                    document_id=document_id,
                )
                region = await self._post_card(
                    client,
                    app_key=app_key,
                    document_id=document_id,
                    urn=competition_region_urn,
                )
                competitions = _competition_links(region)[: max(0, self.max_competitions)]
                events = await self._fetch_competition_events(
                    client,
                    app_key=app_key,
                    document_id=document_id,
                    competitions=competitions,
                )
                # TODO: enrich each event through its "Gols" tab for OU 2.5 and BTTS.
                # Kept out of the first deep-catalogue pass because it multiplies requests.
                payload = {
                    "source": self.name,
                    "competition_region_urn": competition_region_urn,
                    "competition_count": len(competitions),
                    "events": events,
                }
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
        return parse_betfair_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    async def _resolve_document_id(self, client: httpx.AsyncClient, page: str) -> str:
        for script_url in _script_urls(page, self.host):
            try:
                script = await self._get_text(client, script_url)
            except httpx.HTTPError as exc:
                logger.warning("betfair.bootstrap.script_failed", url=script_url, error=str(exc))
                continue
            document_id = _extract_card_document_id(script)
            if document_id is not None:
                return document_id
        return BETFAIR_CARD_DOCUMENT_FALLBACK

    async def _resolve_competition_region_urn(
        self,
        client: httpx.AsyncClient,
        *,
        app_key: str,
        document_id: str,
        page: str,
    ) -> str:
        direct = _extract_competition_region_urn(page)
        if direct is not None:
            return direct
        for navigation_urn in _extract_navigation_tab_urns(page):
            try:
                navigation = await self._post_card(
                    client,
                    app_key=app_key,
                    document_id=document_id,
                    urn=navigation_urn,
                )
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "betfair.bootstrap.navigation_failed",
                    urn=navigation_urn,
                    error=str(exc),
                )
                continue
            region_urn = _extract_competition_region_urn(_to_text(navigation))
            if region_urn is not None:
                return region_urn
            await self._delay()
        return BETFAIR_COMPETITION_REGION_FALLBACK

    async def _fetch_competition_events(
        self,
        client: httpx.AsyncClient,
        *,
        app_key: str,
        document_id: str,
        competitions: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        events_by_id: dict[str, dict[str, Any]] = {}
        for competition in competitions:
            view_url = competition.get("view_url")
            if not view_url:
                continue
            competition_url = urljoin(f"{self.host}/apostas/", view_url)
            try:
                page = await self._get_text(client, competition_url)
                coupon_urn = _extract_coupon_urn(page)
                if coupon_urn is None:
                    continue
                competition_events = await self._fetch_coupon_pages(
                    client,
                    app_key=app_key,
                    document_id=document_id,
                    coupon_urn=coupon_urn,
                )
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(
                    "betfair.competition.failed",
                    competition=competition.get("name"),
                    error=str(exc),
                )
                continue
            for event in competition_events:
                event_id = event.get("eventId")
                if isinstance(event_id, str):
                    events_by_id[event_id] = event
            await self._delay()
        return list(events_by_id.values())

    async def _fetch_coupon_pages(
        self,
        client: httpx.AsyncClient,
        *,
        app_key: str,
        document_id: str,
        coupon_urn: str,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max(1, self.max_pages_per_competition)):
            coupon = await self._post_card(
                client,
                app_key=app_key,
                document_id=document_id,
                urn=coupon_urn,
                cursor=cursor,
            )
            events.extend(_build_events_from_coupon(coupon))
            has_next_page, end_cursor = _page_info(coupon)
            if not has_next_page or end_cursor is None or end_cursor == cursor:
                break
            cursor = end_cursor
            await self._delay()
        return events

    async def _post_card(
        self,
        client: httpx.AsyncClient,
        *,
        app_key: str,
        document_id: str,
        urn: str,
        cursor: str | None = None,
    ) -> JsonObject:
        variables: dict[str, Any] = {
            "urn": [urn],
            "numberOfFilledCardsInCardGroup": self.coupon_card_limit,
            "preferences": {
                "userProducts": ["SPORTSBOOK", "GAMES"],
                "favoriteSports": [],
            },
            "productExclusions": [],
            "experiments": [],
        }
        if cursor is not None:
            variables["cursor"] = cursor
        body = {
            "variables": variables,
            "documentId": document_id,
        }
        response = await client.post(
            self.bff_url,
            params={"_ak": app_key, "currentViewUrn": urn},
            json=body,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            return {}
        if payload.get("errors") and not _dig(payload, "data", "Cards"):
            msg = f"Betfair GraphQL Card failed for {urn}"
            raise ValueError(msg)
        return payload

    async def _get_text(self, client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url, headers={**DEFAULT_HEADERS, "Accept": "text/html"})
        response.raise_for_status()
        return response.text

    async def _delay(self) -> None:
        if self.request_delay_seconds > 0:
            await asyncio.sleep(self.request_delay_seconds)


class BetfairCollector(AdapterCollector):
    name = BetfairAdapter.name

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(BetfairAdapter(timeout_seconds=timeout_seconds))


def parse_betfair_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        return []
    parsed_events: list[RawEvent] = []
    for raw_event in raw_events:
        if not isinstance(raw_event, Mapping):
            continue
        event = _parse_event(raw_event, sport=sport, now=now)
        if event is not None:
            parsed_events.append(event)
    return parsed_events


def _parse_event(raw_event: JsonObject, *, sport: str, now: datetime | None) -> RawEvent | None:
    event_id = _optional_string(raw_event.get("eventId"))
    if event_id is None:
        return None
    starts_at = _parse_datetime(raw_event.get("openDate"))
    if not _is_prematch_event(raw_event, starts_at=starts_at, now=now):
        return None
    home_team = _optional_string(raw_event.get("home"))
    away_team = _optional_string(raw_event.get("away"))
    if home_team is None or away_team is None:
        return None
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(raw_event.get("competition")),
        country=_optional_string(raw_event.get("country")),
        raw_event_id=event_id,
    )
    match = temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})
    markets = _parse_markets(raw_event.get("markets"), match)
    if not markets:
        return None
    return RawEvent(event_id=event_id, bookmaker=BetfairAdapter.name, match=match, markets=markets)


def _is_prematch_event(
    raw_event: JsonObject,
    *,
    starts_at: datetime,
    now: datetime | None,
) -> bool:
    status = _optional_string(raw_event.get("status"))
    if status is not None and status != "PRE_MATCH":
        return False
    if raw_event.get("inplay") is True:
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
    market_id = _optional_string(raw_market.get("marketId"))
    if market_id is None:
        return None
    label = _string(raw_market.get("name"), default=market_id)
    raw_runners = raw_market.get("runners")
    if not isinstance(raw_runners, list):
        return None
    selections = [
        odd
        for raw_runner in raw_runners
        if (
            odd := _parse_runner(
                raw_runner=raw_runner,
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


def _parse_runner(
    *,
    raw_runner: object,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    if not isinstance(raw_runner, Mapping):
        return None
    price = _parse_price(raw_runner.get("price"))
    if price is None:
        return None
    outcome_key = _outcome_key(market_key, raw_runner, match)
    if outcome_key is None:
        return None
    selection_id = _optional_string(raw_runner.get("selectionId")) or f"{market_id}:{outcome_key}"
    label = _string(raw_runner.get("name"), default=outcome_key)
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=BetfairAdapter.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(raw_market: JsonObject) -> MarketKey | None:
    market_type = _optional_string(raw_market.get("marketType")) or ""
    upper = market_type.upper()
    if upper == BETFAIR_OVER_UNDER_25_TYPE:
        return "over_under_2_5"
    if upper == BETFAIR_BTTS_TYPE:
        return "both_teams_score"
    if any(upper.startswith(prefix) for prefix in BETFAIR_MATCH_RESULT_TYPES):
        return "1x2"
    return None


def _outcome_key(market_key: MarketKey, raw_runner: JsonObject, match: Match) -> str | None:
    if market_key == "1x2":
        role = _optional_string(raw_runner.get("role"))
        if role in {"home", "draw", "away"}:
            return role
        return _team_outcome_from_name(raw_runner, match)
    normalized = canonical_market_name(_string(raw_runner.get("name"), default=""))
    compact = normalized.replace(" ", "")
    if market_key == "over_under_2_5":
        if normalized.startswith("over_under_2_5:over") or normalized.startswith("mais"):
            return "over"
        if normalized.startswith("over_under_2_5:under") or normalized.startswith("menos"):
            return "under"
    if market_key == "both_teams_score":
        if normalized.startswith("both_teams_score:yes") or compact in {"sim", "yes"}:
            return "yes"
        if normalized.startswith("both_teams_score:no") or compact in {"nao", "no"}:
            return "no"
    return None


def _team_outcome_from_name(raw_runner: JsonObject, match: Match) -> str | None:
    normalized = canonical_market_name(_string(raw_runner.get("name"), default=""))
    if not normalized:
        return None
    if normalized in {"empate", "draw", "x"}:
        return "draw"
    if _team_label_matches(normalized, match.home_team):
        return "home"
    if _team_label_matches(normalized, match.away_team):
        return "away"
    return None


# --- fetch-side helpers: GraphQL payload -> normalized aggregate ---------------------


def _build_events_from_coupon(coupon: JsonObject) -> list[dict[str, Any]]:
    cards = _dig(coupon, "data", "Cards")
    if not isinstance(cards, list):
        return []
    events: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        edges = _dig(card, "full", "edges")
        if not isinstance(edges, list):
            continue
        competition = _optional_string(card.get("name"))
        for edge in edges:
            node = edge.get("node") if isinstance(edge, Mapping) else None
            if not isinstance(node, Mapping):
                continue
            typename = _optional_string(node.get("__typename"))
            if typename == "CouponHeaderCard":
                competition = _coupon_header_competition(node) or competition
            elif typename == "EventMarketCard":
                event = _event_from_market_card(node, competition)
                if event is not None:
                    events.append(event)
    return events


def _coupon_header_competition(node: JsonObject) -> str | None:
    competition = node.get("competition")
    if isinstance(competition, Mapping):
        return _optional_string(competition.get("name"))
    return None


def _event_from_market_card(node: JsonObject, competition: str | None) -> dict[str, Any] | None:
    sportevent = node.get("sportevent")
    if not isinstance(sportevent, Mapping):
        return None
    event_id = _optional_string(sportevent.get("eventId"))
    if event_id is None:
        return None
    raw_fixture = node.get("fixture")
    fixture: JsonObject = raw_fixture if isinstance(raw_fixture, Mapping) else {}
    home, away = _fixture_teams(fixture, sportevent)
    market = _dig(node, "displayRunners", "sportsbook", "market")
    event: dict[str, Any] = {
        "eventId": event_id,
        "name": _optional_string(sportevent.get("name")),
        "openDate": _optional_string(sportevent.get("openDate")),
        "competition": _event_competition(sportevent) or competition,
        "country": None,
        "sport": _event_sport(sportevent),
        "home": home,
        "away": away,
        "status": _fixture_status(fixture),
        "inplay": _fixture_inplay(market),
        "markets": [],
    }
    primary = _primary_market(market)
    if primary is not None:
        event["markets"].append(primary)
    return event


def _fixture_teams(fixture: JsonObject, sportevent: JsonObject) -> tuple[str | None, str | None]:
    runner_names = fixture.get("runnerNames")
    if isinstance(runner_names, Mapping):
        home = _optional_string(runner_names.get("home"))
        away = _optional_string(runner_names.get("away"))
        if home and away:
            return home, away
    home = _name_of(fixture.get("home"))
    away = _name_of(fixture.get("away"))
    if home and away:
        return home, away
    name = _optional_string(sportevent.get("name"))
    if name is not None:
        for separator in (" x ", " v ", " vs ", " - "):
            if separator in name:
                left, right = name.split(separator, 1)
                return left.strip(), right.strip()
    return None, None


def _fixture_inplay(market: object) -> bool:
    if isinstance(market, Mapping):
        live = market.get("liveData")
        if isinstance(live, Mapping) and live.get("inplay") is True:
            return True
    return False


def _fixture_status(fixture: JsonObject) -> str | None:
    duration = fixture.get("duration")
    if isinstance(duration, Mapping):
        return _optional_string(duration.get("status"))
    return None


def _primary_market(market: object) -> dict[str, Any] | None:
    if not isinstance(market, Mapping):
        return None
    market_id = _optional_string(market.get("urn"))
    if market_id is None:
        return None
    runners = _live_runners(market)
    if len(runners) != 3:
        return None
    roles = ("home", "draw", "away")
    out_runners: list[dict[str, Any]] = []
    for role, runner in zip(roles, runners, strict=True):
        price = _runner_decimal(runner)
        if price is None:
            return None
        out_runners.append(
            {
                "selectionId": _optional_string(runner.get("selectionId")),
                "name": None,
                "role": role,
                "price": price,
            }
        )
    return {
        "marketId": market_id,
        "marketType": _optional_string(market.get("marketType")) or "FULL_TIME_RESULT",
        "name": _optional_string(market.get("name")) or "Resultado da partida",
        "line": None,
        "runners": out_runners,
    }


def _live_runners(market: JsonObject) -> list[JsonObject]:
    live = market.get("liveData")
    if not isinstance(live, Mapping):
        return []
    runners = live.get("runners")
    if not isinstance(runners, list):
        return []
    return [runner for runner in runners if isinstance(runner, Mapping)]


def _runner_decimal(runner: JsonObject) -> float | None:
    odds = runner.get("odds")
    if not isinstance(odds, Mapping):
        return None
    decimal = odds.get("decimal")
    if isinstance(decimal, (int, float)) and not isinstance(decimal, bool):
        return float(decimal)
    return None


def _event_competition(sportevent: JsonObject) -> str | None:
    competition = sportevent.get("competition")
    if isinstance(competition, Mapping):
        return _optional_string(competition.get("name"))
    return None


def _event_sport(sportevent: JsonObject) -> str | None:
    competition = sportevent.get("competition")
    if isinstance(competition, Mapping):
        sport = competition.get("sport")
        if isinstance(sport, Mapping):
            return _optional_string(sport.get("name"))
    return None


def _extract_coupon_urn(page: str) -> str | None:
    match = _COUPON_URN_RE.search(page)
    return match.group(0) if match is not None else None


def _extract_app_key(page: str) -> str | None:
    match = _APP_KEY_RE.search(page) or _STATE_APP_KEY_RE.search(page)
    return match.group(1) if match is not None else None


def _script_urls(page: str, host: str) -> list[str]:
    urls = [urljoin(f"{host}/", source) for source in _SCRIPT_SRC_RE.findall(page)]
    app_bundles = [url for url in urls if "/app-" in url]
    return app_bundles or urls


def _extract_card_document_id(script: str) -> str | None:
    match = _CARD_DOCUMENT_RE.search(script)
    return match.group(0) if match is not None else None


def _extract_competition_region_urn(text: str) -> str | None:
    match = _COMPETITION_REGION_URN_RE.search(text)
    return match.group(0) if match is not None else None


def _extract_navigation_tab_urns(page: str) -> list[str]:
    return list(dict.fromkeys(_NAVIGATION_TAB_URN_RE.findall(page)))


def _competition_links(payload: JsonObject) -> list[dict[str, str]]:
    cards = _dig(payload, "data", "Cards")
    if not isinstance(cards, list):
        return []
    competitions: list[dict[str, str]] = []
    for card in cards:
        if not isinstance(card, Mapping):
            continue
        regions = card.get("competitionRegions")
        if not isinstance(regions, list):
            continue
        for region in regions:
            if not isinstance(region, Mapping):
                continue
            raw_links = region.get("competitionViewLinks")
            if not isinstance(raw_links, list):
                continue
            for raw_link in raw_links:
                if not isinstance(raw_link, Mapping):
                    continue
                view_link = raw_link.get("viewLink")
                competition = raw_link.get("competition")
                if not isinstance(view_link, Mapping) or not isinstance(competition, Mapping):
                    continue
                view_url = _optional_string(view_link.get("viewUrl"))
                view_urn = _optional_string(view_link.get("viewUrn"))
                name = _optional_string(competition.get("name"))
                sport_id = _optional_int(_dig(competition, "sport", "sportId"))
                if view_url is None or view_urn is None or name is None or sport_id != 1:
                    continue
                competitions.append(
                    {
                        "name": name,
                        "view_url": view_url,
                        "view_urn": view_urn,
                    }
                )
    return competitions


def _page_info(payload: JsonObject) -> tuple[bool, str | None]:
    found: tuple[bool, str | None] = (False, None)

    def walk(value: object) -> None:
        nonlocal found
        if found[0]:
            return
        if isinstance(value, Mapping):
            page_info = value.get("pageInfo")
            if isinstance(page_info, Mapping) and page_info.get("hasNextPage") is True:
                found = (True, _optional_string(page_info.get("endCursor")))
                return
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


def _name_of(value: object) -> str | None:
    if isinstance(value, Mapping):
        return _optional_string(value.get("name"))
    return None


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


def _dig(mapping: object, *keys: str) -> object:
    value: object = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _to_text(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
