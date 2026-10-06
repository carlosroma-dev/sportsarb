from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import structlog
from curl_cffi import requests

from odds_arb.collectors.base import (
    AdapterCollector,
    CollectorError,
    RawEvent,
    RawMarket,
    fetch_bytes_with_retries_sync,
    json_from_bytes,
    json_payload_bytes,
)
from odds_arb.core.dedup import canonical_market_name, canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

# Novibet runs an in-house sportsbook. Plain httpx hits the Cloudflare "Just a moment"
# challenge on every /spt/ call, so the collector impersonates a real Chrome TLS
# fingerprint via curl_cffi. No auth, no cookies.
NOVIBET_HOST = "https://www.novibet.bet.br"
NOVIBET_CONTENT_GROUP_ID = 4324  # pre-match content group (8824 is live)
NOVIBET_SOCCER_GROUP_ID = 4372606  # marketViewGroupId for Futebol
NOVIBET_MATCH_RESULT_SYSNAME = "SOCCER_MATCH_RESULT"  # 1X2
NOVIBET_UNDER_OVER_SYSNAME = "SOCCER_UNDER_OVER"
NOVIBET_BOTH_TEAMS_SCORE_SYSNAME = "SOCCER_BOTH_TEAMS_TO_SCORE"
NOVIBET_DOUBLE_CHANCE_SYSNAME = "SOCCER_DOUBLE_CHANCE"
NOVIBET_DEFAULT_IMPERSONATE = "chrome131"
DEFAULT_TIMEOUT_SECONDS = 12.0
DEFAULT_PARAMS = {
    "lang": "pt-BR",
    "timeZ": "E. South America Standard Time",
    "oddsR": "1",  # decimal odds
    "usrGrp": "BR",
}
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "pt-BR",
    "Referer": f"{NOVIBET_HOST}/apostas-esportivas",
}
JsonObject = Mapping[str, Any]

logger = structlog.get_logger(__name__)


class NovibetAdapter:
    name = "novibet"

    def __init__(
        self,
        *,
        host: str = NOVIBET_HOST,
        content_group_id: int = NOVIBET_CONTENT_GROUP_ID,
        soccer_group_id: int = NOVIBET_SOCCER_GROUP_ID,
        location_id: int | None = None,
        impersonate: str = NOVIBET_DEFAULT_IMPERSONATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.host = host
        self.content_group_id = content_group_id
        self.soccer_group_id = soccer_group_id
        # When None, the daily-coupon aggregate location is discovered from the coupon tree.
        self.location_id = location_id
        self.impersonate = impersonate
        self.timeout_seconds = timeout_seconds
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    async def fetch(self) -> bytes:
        return await asyncio.to_thread(
            fetch_bytes_with_retries_sync,
            self._fetch_once_bytes,
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
        return parse_novibet_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    def _fetch_once_bytes(self) -> bytes:
        """Blocking curl_cffi calls; run off the event loop via asyncio.to_thread."""
        # impersonate is typed as a Literal of browser ids upstream; keep the field a plain
        # configurable str and hand it over as Any so a future browser id needs no code change.
        with requests.Session(impersonate=cast(Any, self.impersonate)) as session:
            location_id = self.location_id
            if location_id is None:
                coupon = self._get_json(
                    session,
                    f"/spt/feed/navigation/coupon/v2/{self.content_group_id}/{self.soccer_group_id}/",
                )
                location_id = _extract_location_id(coupon)
            if location_id is None:
                msg = "could not resolve Novibet daily-coupon locationId"
                raise CollectorError(msg)
            # TODO(decisão): add a bounded fan-out over coupon tree isPopular leagues only if
            # the daily aggregate misses material coverage. It trades more events for extra
            # requests, dedup work, and rate-limit risk; today the aggregate is broad.
            payload = self._get_json(
                session,
                f"/spt/feed/marketviews/location/v2/{self.content_group_id}/{location_id}/",
            )
            return json_payload_bytes(payload)

    def fetch_event_marketview(self, event_id: str, *, filter_alias: str = "") -> object:
        with requests.Session(impersonate=cast(Any, self.impersonate)) as session:
            return self._get_json(
                session,
                f"/spt/feed/marketviews/event/{self.content_group_id}/{event_id}",
                params={
                    **DEFAULT_PARAMS,
                    "timestamp": "0",
                    "filterAlias": filter_alias,
                },
            )

    def _get_json(
        self,
        session: Any,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> object:
        response = session.get(
            f"{self.host}{path}",
            params=params or DEFAULT_PARAMS,
            headers=DEFAULT_HEADERS,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload: object = response.json()
        return payload


class NovibetCollector(AdapterCollector):
    name = NovibetAdapter.name

    def __init__(
        self,
        *,
        host: str = NOVIBET_HOST,
        content_group_id: int = NOVIBET_CONTENT_GROUP_ID,
        soccer_group_id: int = NOVIBET_SOCCER_GROUP_ID,
        location_id: int | None = None,
        impersonate: str = NOVIBET_DEFAULT_IMPERSONATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            NovibetAdapter(
                host=host,
                content_group_id=content_group_id,
                soccer_group_id=soccer_group_id,
                location_id=location_id,
                impersonate=impersonate,
                timeout_seconds=timeout_seconds,
            )
        )


def parse_novibet_payload(
    payload: object,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    groups = payload if isinstance(payload, list) else [payload]
    parsed_events: list[RawEvent] = []
    for group in groups:
        if not isinstance(group, Mapping):
            continue
        bet_views = group.get("betViews")
        if not isinstance(bet_views, list):
            continue
        for bet_view in bet_views:
            if not isinstance(bet_view, Mapping):
                continue
            parsed_events.extend(_parse_bet_view(bet_view, sport=sport, now=now))
    return parsed_events


def _parse_bet_view(
    bet_view: JsonObject,
    *,
    sport: str,
    now: datetime | None,
) -> list[RawEvent]:
    events: list[RawEvent] = []
    # Shape A (UPCOMING_EVENTS): flat list of events under "items".
    for item in _as_list(bet_view.get("items")):
        event = _parse_event(item, league=None, sport=sport, now=now)
        if event is not None:
            events.append(event)
    # Shape B (in-play / league view): events nested under competitions.
    for competition in _as_list(bet_view.get("competitions")):
        if not isinstance(competition, Mapping):
            continue
        league = _optional_string(competition.get("caption"))
        for item in _as_list(competition.get("events")):
            event = _parse_event(item, league=league, sport=sport, now=now)
            if event is not None:
                events.append(event)
    return events


def _parse_event(
    item: object,
    *,
    league: str | None,
    sport: str,
    now: datetime | None,
) -> RawEvent | None:
    if not isinstance(item, Mapping):
        return None
    event_id = _optional_string(item.get("eventBetContextId") or item.get("betContextId"))
    if event_id is None:
        return None
    home_team, away_team = _extract_teams(item.get("additionalCaptions"))
    if home_team is None or away_team is None:
        return None

    starts_at = _parse_datetime(item.get("startDateTime"))
    if now is not None and starts_at <= now:
        return None
    league_name = league or _optional_string(item.get("competitionCaption"))
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=league_name,
        raw_event_id=event_id,
    )
    match = temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})
    markets = _parse_markets(item.get("markets"), match)
    if not markets:
        return None
    return RawEvent(
        event_id=event_id,
        bookmaker=NovibetCollector.name,
        match=match,
        markets=markets,
    )


def _parse_markets(raw_markets: object, match: Match) -> list[RawMarket]:
    if not isinstance(raw_markets, list):
        return []
    markets: list[RawMarket] = []
    for raw_market in raw_markets:
        if not isinstance(raw_market, Mapping):
            continue
        market_key = _market_key(raw_market)
        if market_key is None:
            continue
        market_id = _optional_string(raw_market.get("marketId"))
        if market_id is None:
            continue
        label = _market_label(raw_market, market_key)
        selections = [
            odd
            for raw_item in _as_list(raw_market.get("betItems"))
            if (
                odd := _parse_selection(
                    raw_item=raw_item,
                    market_id=market_id,
                    market_label=label,
                    market_key=market_key,
                    match=match,
                )
            )
            is not None
        ]
        if selections:
            markets.append(RawMarket(market_id=market_id, label=label, selections=selections))
    return markets


def _parse_selection(
    *,
    raw_item: object,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    if not isinstance(raw_item, Mapping):
        return None
    if raw_item.get("isAvailable") is False:
        return None
    caption = _string(raw_item.get("caption"), default="")
    if market_key == "over_under_2_5" and _parse_line(caption) != Decimal("2.5"):
        return None
    outcome_key = _outcome_key(
        market_key,
        _optional_string(raw_item.get("code")),
        caption,
    )
    price = _parse_price(raw_item.get("price"))
    if outcome_key is None or price is None:
        return None
    selection_id = _optional_string(raw_item.get("id")) or f"{market_id}:{outcome_key}"
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=NovibetCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {_string(raw_item.get('caption'), default=outcome_key)}",
    )


def _market_key(raw_market: JsonObject) -> MarketKey | None:
    sysname = _optional_string(raw_market.get("betTypeSysname"))
    if sysname == NOVIBET_MATCH_RESULT_SYSNAME:
        return "1x2"
    if sysname == NOVIBET_UNDER_OVER_SYSNAME and _market_has_line(raw_market, Decimal("2.5")):
        return "over_under_2_5"
    if sysname == NOVIBET_BOTH_TEAMS_SCORE_SYSNAME:
        return "both_teams_score"
    if sysname == NOVIBET_DOUBLE_CHANCE_SYSNAME:
        return "double_chance"
    return None


def _market_label(raw_market: JsonObject, market_key: MarketKey) -> str:
    default_by_key: dict[MarketKey, str] = {
        "1x2": "Resultado Final",
        "over_under_2_5": "Total de Gols 2.5",
        "both_teams_score": "Ambas equipes Marcam",
        "double_chance": "Chance dupla",
    }
    return _string(raw_market.get("marketCaption"), default=default_by_key[market_key])


def _outcome_key(market_key: MarketKey, code: str | None, caption: str) -> str | None:
    normalized_code = code.strip().upper() if code is not None else ""
    normalized_caption = canonical_market_name(caption)
    compact_caption = normalized_caption.replace(" ", "")
    if market_key == "1x2":
        if normalized_code == "1":
            return "home"
        if normalized_code == "X":
            return "draw"
        if normalized_code == "2":
            return "away"
    if market_key == "over_under_2_5":
        if normalized_code == "O" or normalized_caption.startswith("over_under_2_5:over"):
            return "over"
        if normalized_code == "U" or normalized_caption.startswith("over_under_2_5:under"):
            return "under"
    if market_key == "both_teams_score":
        if normalized_code == "Y" or compact_caption in {"gg", "sim", "yes"}:
            return "yes"
        if normalized_code == "N" or compact_caption in {"ng", "nao", "no"}:
            return "no"
    if market_key == "double_chance":
        if normalized_code == "1X" or compact_caption == "1x":
            return "home_draw"
        if normalized_code == "12" or compact_caption == "12":
            return "home_away"
        if normalized_code == "X2" or compact_caption == "x2":
            return "draw_away"
    return None


def _market_has_line(raw_market: JsonObject, target: Decimal) -> bool:
    return any(
        isinstance(raw_item, Mapping)
        and _parse_line(_string(raw_item.get("caption"), default="")) == target
        for raw_item in _as_list(raw_market.get("betItems"))
    )


def _parse_line(value: str) -> Decimal | None:
    match = re.search(r"\b(\d+(?:[,.]\d+)?)\b", value)
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def _extract_teams(captions: object) -> tuple[str | None, str | None]:
    if not isinstance(captions, Mapping):
        return None, None
    return _optional_string(captions.get("competitor1")), _optional_string(
        captions.get("competitor2")
    )


def _extract_location_id(coupon: object) -> int | None:
    if isinstance(coupon, Mapping):
        info = coupon.get("dailyCouponInfo")
        if isinstance(info, Mapping):
            location_id = info.get("locationId")
            if isinstance(location_id, int) and not isinstance(location_id, bool):
                return location_id
    return None


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


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default


def _latency_ms(started_at: datetime) -> int:
    return int((datetime.now(UTC) - started_at).total_seconds() * 1000)
