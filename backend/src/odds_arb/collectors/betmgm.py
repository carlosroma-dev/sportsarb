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
    json_payload_bytes,
)
from odds_arb.core.dedup import canonical_market_name, canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

BETMGM_EVENTS_URL = "https://br-program-api.goldrush.llc/program/v1/api/events"
BETMGM_GROUP_ID_FOOTBALL = "11"
BETMGM_BRAND = "betmgm"
BETMGM_LOCATION = "BR"
BETMGM_LANG = "pt"
BETMGM_MARKET_1X2 = "standard-3-way"
BETMGM_MARKET_OVER_UNDER = "total-points"
BETMGM_MARKET_BTTS = "both-competitors-to-score"
BETMGM_MARKET_DOUBLE_CHANCE = "double-chance"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_LIMIT = 100
DEFAULT_MAX_PAGES = 1
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.betmgm.bet.br",
    "Referer": "https://www.betmgm.bet.br/",
}
JsonObject = Mapping[str, Any]


class BetmgmAdapter:
    name = "betmgm"

    def __init__(
        self,
        *,
        url: str = BETMGM_EVENTS_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        limit: int = DEFAULT_LIMIT,
        max_pages: int = DEFAULT_MAX_PAGES,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.limit = limit
        self.max_pages = max_pages
        self.retry_attempts = retry_attempts
        self.retry_backoff_seconds = retry_backoff_seconds

    async def fetch(self) -> bytes:
        async def fetch_once() -> bytes:
            payloads: list[JsonObject] = []
            cursor: str | None = None
            async with httpx.AsyncClient(
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds,
                follow_redirects=True,
                http2=True,
            ) as client:
                for _ in range(max(1, self.max_pages)):
                    params = self._params(cursor=cursor)
                    response = await client.get(self.url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, Mapping):
                        break
                    payloads.append(payload)
                    cursor = _optional_string(payload.get("nextCursor"))
                    if cursor is None:
                        break
            return json_payload_bytes(_combine_payloads(payloads))

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
        return parse_betmgm_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    def _params(self, *, cursor: str | None) -> dict[str, str]:
        params = {
            "groupIds": BETMGM_GROUP_ID_FOOTBALL,
            "matchState": "PREMATCH",
            "startTimeOffsetFrom": "0",
            "lang": BETMGM_LANG,
            "brand": BETMGM_BRAND,
            "location": BETMGM_LOCATION,
            "limit": str(self.limit),
            "fields": "GROUPS,BETMARKETS",
        }
        if cursor is not None:
            params["cursor"] = cursor
        return params


class BetmgmCollector(AdapterCollector):
    name = BetmgmAdapter.name

    def __init__(
        self,
        *,
        url: str = BETMGM_EVENTS_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        limit: int = DEFAULT_LIMIT,
    ) -> None:
        super().__init__(
            BetmgmAdapter(
                url=url,
                timeout_seconds=timeout_seconds,
                limit=limit,
            )
        )


def parse_betmgm_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    raw_events = payload.get("data")
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
    event_id = _optional_string(raw_event.get("id"))
    if event_id is None:
        return None
    starts_at = _parse_datetime(raw_event.get("startTime"))
    if not _is_prematch_event(raw_event, starts_at=starts_at, now=now):
        return None
    home_team, away_team = _extract_teams(raw_event.get("participants"))
    if home_team is None or away_team is None:
        return None
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(raw_event.get("leagueName")) or _group_name(raw_event),
        country=_group_parent_name(raw_event),
        raw_event_id=event_id,
    )
    match = temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})
    markets = _parse_markets(raw_event.get("markets"), match)
    if not markets:
        return None
    return RawEvent(
        event_id=event_id,
        bookmaker=BetmgmCollector.name,
        match=match,
        markets=markets,
    )


def _is_prematch_event(
    raw_event: JsonObject,
    *,
    starts_at: datetime,
    now: datetime | None,
) -> bool:
    if _optional_string(raw_event.get("eventType")) != "MATCH":
        return False
    if _optional_string(raw_event.get("matchState")) != "PREMATCH":
        return False
    if _optional_string(raw_event.get("sportType")) != "FOOTBALL":
        return False
    game_clock = raw_event.get("gameClock")
    if isinstance(game_clock, Mapping):
        event_state = _optional_string(game_clock.get("eventState"))
        if event_state != "PREMATCH":
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
    if _optional_string(raw_market.get("betMarketStatus")) != "OPEN":
        return None
    market_key = _market_key(raw_market)
    if market_key is None:
        return None
    market_id = _optional_string(raw_market.get("id"))
    if market_id is None:
        return None
    label = _string(raw_market.get("name"), default=market_id)
    raw_outcomes = raw_market.get("outcomes")
    if not isinstance(raw_outcomes, list):
        return None
    selections = [
        odd
        for raw_outcome in raw_outcomes
        if (
            odd := _parse_outcome(
                raw_outcome=raw_outcome,
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


def _parse_outcome(
    *,
    raw_outcome: object,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    if not isinstance(raw_outcome, Mapping):
        return None
    if _optional_string(raw_outcome.get("offerState")) not in {None, "TRADED"}:
        return None
    if raw_outcome.get("isTraded") is False:
        return None
    selection_id = _optional_string(raw_outcome.get("id"))
    if selection_id is None:
        return None
    label = _string(raw_outcome.get("name"), default=selection_id)
    price = _parse_price(raw_outcome.get("formatDecimal") or raw_outcome.get("odds"))
    outcome_key = _outcome_key(market_key, label, match)
    if price is None or outcome_key is None:
        return None
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=BetmgmCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(raw_market: JsonObject) -> MarketKey | None:
    market_type = _optional_string(raw_market.get("type"))
    if market_type == BETMGM_MARKET_1X2:
        return "1x2"
    if market_type == BETMGM_MARKET_OVER_UNDER and _is_match_total_2_5(raw_market):
        return "over_under_2_5"
    if market_type == BETMGM_MARKET_BTTS and not _has_specifier(raw_market, "SECTION"):
        return "both_teams_score"
    if market_type == BETMGM_MARKET_DOUBLE_CHANCE:
        return "double_chance"
    return None


def _outcome_key(market_key: MarketKey, label: str, match: Match) -> str | None:
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
        if normalized == "over_under_2_5:over" or normalized.startswith("mais"):
            return "over"
        if normalized == "over_under_2_5:under" or normalized.startswith("menos"):
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


def _is_match_total_2_5(raw_market: JsonObject) -> bool:
    if _has_specifier(raw_market, "TEAM") or _has_specifier(raw_market, "SECTION"):
        return False
    return _specifier_line(raw_market) == Decimal("2.5")


def _has_specifier(raw_market: JsonObject, specifier_type: str) -> bool:
    return any(
        _optional_string(specifier.get("type")) == specifier_type
        for specifier in _specifier_list(raw_market)
    )


def _specifier_line(raw_market: JsonObject) -> Decimal | None:
    for specifier in _specifier_list(raw_market):
        if _optional_string(specifier.get("type")) == "OVER_UNDER":
            return _parse_line(specifier.get("value"))
    return None


def _specifier_list(raw_market: JsonObject) -> list[JsonObject]:
    specifiers = raw_market.get("specifiers")
    if not isinstance(specifiers, list):
        return []
    return [specifier for specifier in specifiers if isinstance(specifier, Mapping)]


def _extract_teams(raw_participants: object) -> tuple[str | None, str | None]:
    if not isinstance(raw_participants, list):
        return None, None
    home: str | None = None
    away: str | None = None
    for participant in raw_participants:
        if not isinstance(participant, Mapping):
            continue
        name = _optional_string(participant.get("name"))
        position = _optional_string(participant.get("position"))
        if name is None:
            continue
        if position == "HOME":
            home = name
        elif position == "AWAY":
            away = name
    return home, away


def _group_name(raw_event: JsonObject) -> str | None:
    group = raw_event.get("group")
    return _optional_string(group.get("name")) if isinstance(group, Mapping) else None


def _group_parent_name(raw_event: JsonObject) -> str | None:
    group = raw_event.get("group")
    if not isinstance(group, Mapping):
        return None
    parent = group.get("parentGroup")
    return _optional_string(parent.get("name")) if isinstance(parent, Mapping) else None


def _combine_payloads(payloads: list[JsonObject]) -> JsonObject:
    data: list[object] = []
    next_cursor: str | None = None
    for payload in payloads:
        raw_data = payload.get("data")
        if isinstance(raw_data, list):
            data.extend(raw_data)
        next_cursor = _optional_string(payload.get("nextCursor"))
    return {
        "data": data,
        "limit": len(data),
        "nextCursor": next_cursor,
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


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
