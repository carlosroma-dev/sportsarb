from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
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

SUPERBET_OFFER_HOST = "https://production-superbet-offer-br.freetls.fastly.net"
SUPERBET_EVENTS_BY_DATE_URL = f"{SUPERBET_OFFER_HOST}/v2/pt-BR/events/by-date"
SUPERBET_EVENT_DETAIL_URL = f"{SUPERBET_OFFER_HOST}/v2/pt-BR/events"
SUPERBET_SPORT_ID_FOOTBALL = "5"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_DETAIL_CONCURRENCY = 8
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://superbet.bet.br",
    "Referer": "https://superbet.bet.br/",
}
JsonObject = Mapping[str, Any]

logger = structlog.get_logger(__name__)


class SuperbetAdapter:
    name = "superbet"

    def __init__(
        self,
        *,
        url: str = SUPERBET_EVENTS_BY_DATE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        window_hours: int = 72,
        fetch_event_details: bool = True,
        detail_concurrency: int = DEFAULT_DETAIL_CONCURRENCY,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.window_hours = window_hours
        self.fetch_event_details = fetch_event_details
        self.detail_concurrency = detail_concurrency
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
                response = await client.get(self.url, params=self._params())
                response.raise_for_status()
                payload = response.json()
                if self.fetch_event_details and isinstance(payload, Mapping):
                    payload = await self._fetch_detail_payload(client, payload)
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
        return parse_superbet_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    def _params(self) -> dict[str, str]:
        start = datetime.now().replace(microsecond=0)
        end = start + timedelta(hours=self.window_hours)
        return {
            "currentStatus": "active",
            "offerState": "prematch",
            "startDate": start.strftime("%Y-%m-%d %H:%M:%S"),
            "endDate": end.strftime("%Y-%m-%d %H:%M:%S"),
            "sportId": SUPERBET_SPORT_ID_FOOTBALL,
        }

    async def _fetch_detail_payload(
        self,
        client: httpx.AsyncClient,
        list_payload: JsonObject,
    ) -> JsonObject:
        event_ids = _event_ids(list_payload)
        if not event_ids:
            return list_payload

        semaphore = asyncio.Semaphore(max(1, self.detail_concurrency))

        async def fetch_one(event_id: str) -> JsonObject | None:
            async with semaphore:
                try:
                    response = await client.get(f"{SUPERBET_EVENT_DETAIL_URL}/{event_id}")
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPError as exc:
                    logger.warning(
                        "collector.fetch.detail_failed",
                        collector=self.name,
                        event_id=event_id,
                        error=str(exc),
                    )
                    return None
                return payload if isinstance(payload, Mapping) else None

        detail_payloads = await asyncio.gather(*(fetch_one(event_id) for event_id in event_ids))
        detail_events: list[JsonObject] = []
        for detail_payload in detail_payloads:
            if detail_payload is not None:
                detail_events.extend(_payload_events(detail_payload))
        if not detail_events:
            return list_payload
        return {"data": detail_events}


class SuperbetCollector(AdapterCollector):
    name = SuperbetAdapter.name

    def __init__(
        self,
        *,
        url: str = SUPERBET_EVENTS_BY_DATE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        window_hours: int = 72,
        fetch_event_details: bool = True,
        detail_concurrency: int = DEFAULT_DETAIL_CONCURRENCY,
    ) -> None:
        super().__init__(
            SuperbetAdapter(
                url=url,
                timeout_seconds=timeout_seconds,
                window_hours=window_hours,
                fetch_event_details=fetch_event_details,
                detail_concurrency=detail_concurrency,
            )
        )


def parse_superbet_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    raw_events = _payload_events(payload)
    if not raw_events:
        return []

    parsed_events: list[RawEvent] = []
    for event in raw_events:
        if not isinstance(event, Mapping):
            continue
        event_id = str(event.get("eventId") or event.get("uuid") or "")
        if not event_id:
            continue
        match = _parse_match(event_id, event, sport=sport)
        if now is not None and match.starts_at <= now:
            continue
        markets = _parse_markets(event, match)
        if markets:
            parsed_events.append(
                RawEvent(
                    event_id=event_id,
                    bookmaker=SuperbetCollector.name,
                    match=match,
                    markets=markets,
                )
            )
    return parsed_events


def _parse_match(event_id: str, event: JsonObject, *, sport: str) -> Match:
    home_team, away_team = _extract_teams(
        _string(event.get("matchName"), default="Unknown v Unknown")
    )
    starts_at = _parse_datetime(event.get("utcDate") or event.get("matchDate"))
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(event.get("tournamentName")),
        raw_event_id=event_id,
    )
    return temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})


def _parse_markets(event: JsonObject, match: Match) -> list[RawMarket]:
    raw_odds = event.get("odds")
    if not isinstance(raw_odds, list):
        return []

    grouped: dict[tuple[str, MarketKey, str], list[Odd]] = defaultdict(list)
    for raw_odd in raw_odds:
        if not isinstance(raw_odd, Mapping):
            continue
        market_label = _string(raw_odd.get("marketName"), default=str(raw_odd.get("marketId")))
        market_key = _market_key(market_label)
        if market_key is None:
            continue
        market_id = str(raw_odd.get("marketUuid") or raw_odd.get("marketId") or market_label)
        odd = _parse_odd(
            raw_odd=raw_odd,
            market_id=market_id,
            market_label=market_label,
            market_key=market_key,
            match=match,
        )
        if odd is not None:
            grouped[(market_id, market_key, market_label)].append(odd)

    return [
        RawMarket(market_id=market_id, label=market_label, selections=odds)
        for (market_id, _market_key_value, market_label), odds in grouped.items()
        if odds
    ]


def _parse_odd(
    *,
    raw_odd: JsonObject,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    if _optional_string(raw_odd.get("status")) not in {None, "active"}:
        return None
    label = _string(raw_odd.get("name"), default=str(raw_odd.get("outcomeId") or "selection"))
    outcome_key = _outcome_key(market_key, label, _optional_string(raw_odd.get("code")))
    price = _parse_price(raw_odd.get("price"))
    if outcome_key is None or price is None:
        return None
    selection_id = str(raw_odd.get("uuid") or raw_odd.get("outcomeId") or f"{market_id}:{label}")
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=SuperbetCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(label: str) -> MarketKey | None:
    normalized = canonical_market_name(label)
    if normalized == "1x2":
        return "1x2"
    if normalized == "total de gols":
        return "over_under_2_5"
    if normalized == "ambas as equipes marcam":
        return "both_teams_score"
    if normalized == "dupla chance":
        return "double_chance"
    return None


def _outcome_key(market_key: MarketKey, label: str, code: str | None) -> str | None:
    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")
    if market_key == "1x2":
        if compact == "1" or code == "1":
            return "home"
        if compact == "x" or code == "0":
            return "draw"
        if compact == "2" or code == "2":
            return "away"
    if market_key == "over_under_2_5":
        if normalized.startswith("over_under_2_5:over"):
            return "over"
        if normalized.startswith("over_under_2_5:under"):
            return "under"
    if market_key == "both_teams_score":
        if normalized.startswith("both_teams_score:yes") or compact == "sim" or code == "1":
            return "yes"
        if normalized.startswith("both_teams_score:no") or compact == "nao" or code == "2":
            return "no"
    if market_key == "double_chance":
        if compact in {"1ouempate", "1x"} or code == "10":
            return "home_draw"
        if compact in {"1ou2", "12"} or code == "12":
            return "home_away"
        if compact in {"empateou2", "x2"} or code == "02":
            return "draw_away"
    return None


def _payload_events(payload: JsonObject) -> list[JsonObject]:
    raw_events = payload.get("data")
    if not isinstance(raw_events, list):
        return []
    return [event for event in raw_events if isinstance(event, Mapping)]


def _event_ids(payload: JsonObject) -> list[str]:
    event_ids: list[str] = []
    for event in _payload_events(payload):
        event_id = event.get("eventId")
        if isinstance(event_id, int) and not isinstance(event_id, bool):
            event_ids.append(str(event_id))
        elif isinstance(event_id, str) and event_id.strip():
            event_ids.append(event_id.strip())
    return event_ids


def _extract_teams(name: str) -> tuple[str, str]:
    for separator in ("·", " - ", " vs ", " x ", " v "):
        if separator in name:
            home, away = name.split(separator, 1)
            return home.strip(), away.strip()
    return name, "Unknown"


def _parse_price(value: object) -> Decimal | None:
    if value is None:
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


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default


def _latency_ms(started_at: datetime) -> int:
    return int((datetime.now(UTC) - started_at).total_seconds() * 1000)
