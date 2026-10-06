from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from curl_cffi import requests

from odds_arb.collectors.base import (
    AdapterCollector,
    RawEvent,
    RawMarket,
    fetch_bytes_with_retries_sync,
    json_from_bytes,
    json_payload_bytes,
)
from odds_arb.core.dedup import canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

BETNACIONAL_HOST = "https://prod-global-bff-events.bet6.com.br"
BETNACIONAL_ODDS_URL = f"{BETNACIONAL_HOST}/api/odds/1/events-by-seasons"
BETNACIONAL_DEFAULT_IMPERSONATE = "chrome131"
BETNACIONAL_SPORT_ID = 1
BETNACIONAL_MARKET_1X2 = 1
BETNACIONAL_MARKET_TOTAL = 18
BETNACIONAL_MARKET_BTTS = 29
BETNACIONAL_FULL_CATALOG_FILTER = 4
BETNACIONAL_TARGET_MARKETS = (
    BETNACIONAL_MARKET_1X2,
    BETNACIONAL_MARKET_TOTAL,
    BETNACIONAL_MARKET_BTTS,
)
BETNACIONAL_TOTAL_2_5_SPECIFIER = "total=2.5"
# date_start has no offset; betnacional serves Brazil local time (UTC-3).
BETNACIONAL_TZ = timezone(timedelta(hours=-3))
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://betnacional.bet.br",
    "Referer": "https://betnacional.bet.br/",
}
JsonObject = Mapping[str, Any]


class BetnacionalAdapter:
    name = "betnacional"

    def __init__(
        self,
        *,
        url: str = BETNACIONAL_ODDS_URL,
        impersonate: str = BETNACIONAL_DEFAULT_IMPERSONATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        markets: tuple[int, ...] = BETNACIONAL_TARGET_MARKETS,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.url = url
        self.impersonate = impersonate
        self.timeout_seconds = timeout_seconds
        self.markets = markets
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
        if not isinstance(payload, Mapping):
            return []
        return parse_betnacional_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    def _fetch_once_bytes(self) -> bytes:
        """Blocking curl_cffi calls (one per market id); aggregate into one payload."""
        rows: list[JsonObject] = []
        with requests.Session(impersonate=cast(Any, self.impersonate)) as session:
            for market_id in self.markets:
                response = session.get(
                    self.url,
                    params=_request_params(market_id),
                    headers=DEFAULT_HEADERS,
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, Mapping):
                    raw_odds = payload.get("odds")
                    if isinstance(raw_odds, list):
                        rows.extend(row for row in raw_odds if isinstance(row, Mapping))
        return json_payload_bytes({"odds": rows})


class BetnacionalCollector(AdapterCollector):
    name = BetnacionalAdapter.name

    def __init__(
        self,
        *,
        url: str = BETNACIONAL_ODDS_URL,
        impersonate: str = BETNACIONAL_DEFAULT_IMPERSONATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            BetnacionalAdapter(url=url, impersonate=impersonate, timeout_seconds=timeout_seconds)
        )


def parse_betnacional_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    raw_odds = payload.get("odds")
    if not isinstance(raw_odds, list):
        return []
    grouped = _group_rows_by_event(raw_odds)
    parsed_events: list[RawEvent] = []
    for event_id, rows in grouped.items():
        event = _parse_event(event_id, rows, sport=sport, now=now)
        if event is not None:
            parsed_events.append(event)
    return parsed_events


def _group_rows_by_event(raw_odds: list[Any]) -> dict[str, list[JsonObject]]:
    grouped: dict[str, list[JsonObject]] = {}
    for row in raw_odds:
        if not isinstance(row, Mapping):
            continue
        event_id = _optional_string(row.get("event_id"))
        if event_id is None:
            continue
        grouped.setdefault(event_id, []).append(row)
    return grouped


def _parse_event(
    event_id: str,
    rows: list[JsonObject],
    *,
    sport: str,
    now: datetime | None,
) -> RawEvent | None:
    first = rows[0]
    home_team = _optional_string(first.get("home"))
    away_team = _optional_string(first.get("away"))
    if home_team is None or away_team is None:
        return None
    starts_at = _parse_datetime(first.get("date_start"))
    if not _is_prematch_event(rows, starts_at=starts_at, now=now):
        return None
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(first.get("tournament_name")),
        country=_optional_string(first.get("category_name")),
        raw_event_id=event_id,
    )
    match = temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})
    markets = _build_markets(rows, match)
    if not markets:
        return None
    return RawEvent(
        event_id=event_id,
        bookmaker=BetnacionalAdapter.name,
        match=match,
        markets=markets,
    )


def _is_prematch_event(
    rows: list[JsonObject],
    *,
    starts_at: datetime,
    now: datetime | None,
) -> bool:
    for row in rows:
        if _optional_int(row.get("is_live")) == 1:
            return False
        if _optional_int(row.get("event_status_id")) not in {None, 0}:
            return False
    return now is None or starts_at > now


def _build_markets(rows: list[JsonObject], match: Match) -> list[RawMarket]:
    buckets: dict[tuple[MarketKey, str], list[Odd]] = {}
    labels: dict[tuple[MarketKey, str], str] = {}
    for row in rows:
        market_key = _market_key(row)
        if market_key is None:
            continue
        outcome_key = _outcome_key(market_key, row)
        if outcome_key is None:
            continue
        price = _parse_price(row.get("odd"))
        if price is None:
            continue
        market_id = _optional_string(row.get("market_id")) or "market"
        bucket_key = (market_key, market_id)
        label = _string(row.get("market_name"), default=market_id)
        outcome_label = _string(row.get("outcome_name"), default=outcome_key)
        odd = Odd(
            match_id=match.match_id,
            market_key=market_key,
            outcome_key=outcome_key,
            price=price,
            bookmaker=BetnacionalAdapter.name,
            event_id=match.raw_event_id,
            market_id=market_id,
            selection_id=_optional_string(row.get("id")),
            raw_label=f"{label} | {outcome_label}",
        )
        bucket = buckets.setdefault(bucket_key, [])
        if any(existing.outcome_key == outcome_key for existing in bucket):
            continue
        bucket.append(odd)
        labels.setdefault(bucket_key, label)

    markets: list[RawMarket] = []
    for (_, market_id), selections in buckets.items():
        if selections:
            markets.append(
                RawMarket(
                    market_id=market_id,
                    label=labels[(_, market_id)],
                    selections=selections,
                )
            )
    return markets


def _market_key(row: JsonObject) -> MarketKey | None:
    market_id = _optional_int(row.get("market_id"))
    if market_id == BETNACIONAL_MARKET_1X2:
        return "1x2"
    if market_id == BETNACIONAL_MARKET_TOTAL and _is_total_2_5(row):
        return "over_under_2_5"
    if market_id == BETNACIONAL_MARKET_BTTS:
        return "both_teams_score"
    return None


def _outcome_key(market_key: MarketKey, row: JsonObject) -> str | None:
    code = _optional_string(row.get("outcome_code")) or ""
    outcome_id = _optional_int(row.get("outcome_id"))
    if market_key == "1x2":
        if code == "{$competitor1}" or outcome_id == 1:
            return "home"
        if code == "draw" or outcome_id == 2:
            return "draw"
        if code == "{$competitor2}" or outcome_id == 3:
            return "away"
    if market_key == "over_under_2_5":
        if code.startswith("over") or outcome_id == 12:
            return "over"
        if code.startswith("under") or outcome_id == 13:
            return "under"
    if market_key == "both_teams_score":
        if code == "yes" or outcome_id == 74:
            return "yes"
        if code == "no" or outcome_id == 76:
            return "no"
    return None


def _is_total_2_5(row: JsonObject) -> bool:
    if _optional_string(row.get("specifier")) == BETNACIONAL_TOTAL_2_5_SPECIFIER:
        return True
    return _parse_line(row.get("specifier_value")) == Decimal("2.5")


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
    if isinstance(value, str) and value.strip():
        text = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            return parsed.replace(tzinfo=BETNACIONAL_TZ).astimezone(UTC)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=BETNACIONAL_TZ).astimezone(UTC)
        return parsed.astimezone(UTC)
    return datetime.now(UTC)


def _request_params(market_id: int) -> dict[str, int | str]:
    return {
        "sport_id": BETNACIONAL_SPORT_ID,
        "category_id": 0,
        "tournament_id": "",
        "markets": market_id,
        "filter_time_event": BETNACIONAL_FULL_CATALOG_FILTER,
    }


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
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
