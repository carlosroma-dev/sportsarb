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

KTO_FOOTBALL_URL = (
    "https://us.offering-api.kambicdn.com/offering/v2018/ktobr/listView/football.json"
    "?lang=pt_BR&market=BR&client_id=200&channel_id=1&useCombined=true"
)
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://www.kto.bet.br",
    "Referer": "https://www.kto.bet.br/",
}
JsonObject = Mapping[str, Any]


class KtoAdapter:
    name = "kto"

    def __init__(
        self,
        *,
        url: str = KTO_FOOTBALL_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds
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
                response = await client.get(self.url)
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
        return parse_kto_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()


class KtoCollector(AdapterCollector):
    name = KtoAdapter.name

    def __init__(
        self,
        *,
        url: str = KTO_FOOTBALL_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(KtoAdapter(url=url, timeout_seconds=timeout_seconds))


def parse_kto_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        return []

    parsed_events: list[RawEvent] = []
    for event_wrapper in raw_events:
        if not isinstance(event_wrapper, Mapping):
            continue
        event = event_wrapper.get("event")
        bet_offers = event_wrapper.get("betOffers")
        if not isinstance(event, Mapping) or not isinstance(bet_offers, list):
            continue
        event_id = str(event.get("id") or "")
        if not event_id:
            continue

        match = _parse_match(event_id, event, sport=sport)
        if not _is_prematch_event(event, starts_at=match.starts_at, now=now):
            continue
        markets = _parse_markets(bet_offers, match)
        if markets:
            parsed_events.append(
                RawEvent(
                    event_id=event_id,
                    bookmaker=KtoCollector.name,
                    match=match,
                    markets=markets,
                )
            )
    return parsed_events


def _parse_match(event_id: str, event: JsonObject, *, sport: str) -> Match:
    home_team = _optional_string(event.get("homeName"))
    away_team = _optional_string(event.get("awayName"))
    if home_team is None or away_team is None:
        home_team, away_team = _extract_teams(
            _string(event.get("name"), default="Unknown v Unknown")
        )
    starts_at = _parse_datetime(event.get("start"))
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(event.get("group")),
        raw_event_id=event_id,
    )
    return temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})


def _is_prematch_event(event: JsonObject, *, starts_at: datetime, now: datetime | None) -> bool:
    if now is None:
        return True
    state = _optional_string(event.get("state"))
    if state is not None and state.upper() in {
        "STARTED",
        "LIVE",
        "ENDED",
        "FINISHED",
        "CLOSED",
        "SETTLED",
        "CANCELLED",
    }:
        return False
    if event.get("started") is True:
        return False
    live_data = event.get("liveData")
    if live_data not in (None, "", [], {}):
        return False
    return starts_at > now


def _parse_markets(bet_offers: list[object], match: Match) -> list[RawMarket]:
    markets: list[RawMarket] = []
    for offer in bet_offers:
        if not isinstance(offer, Mapping):
            continue
        criterion = offer.get("criterion")
        if not isinstance(criterion, Mapping):
            continue
        label = _string(criterion.get("label"), default=str(offer.get("id") or "market"))
        market_key = _market_key(offer)
        if market_key is None:
            continue
        market_id = str(offer.get("id") or label)
        raw_outcomes = offer.get("outcomes")
        if not isinstance(raw_outcomes, list):
            continue
        odds = [
            odd
            for outcome in raw_outcomes
            if isinstance(outcome, Mapping)
            if (
                odd := _parse_outcome(
                    outcome=outcome,
                    market_id=market_id,
                    market_label=label,
                    market_key=market_key,
                    match=match,
                )
            )
            is not None
        ]
        if odds:
            markets.append(RawMarket(market_id=market_id, label=label, selections=odds))
    return markets


def _parse_outcome(
    *,
    outcome: JsonObject,
    market_id: str,
    market_label: str,
    market_key: MarketKey,
    match: Match,
) -> Odd | None:
    label = _string(outcome.get("label"), default=str(outcome.get("id") or "selection"))
    if _optional_string(outcome.get("status")) not in {None, "OPEN"}:
        return None
    if market_key == "over_under_2_5" and _parse_line(outcome.get("line")) != Decimal("2.5"):
        return None
    price = _parse_price(outcome.get("odds"))
    outcome_key = _outcome_key(
        market_key,
        label,
        match,
        outcome_type=_optional_string(outcome.get("type")),
        english_label=_optional_string(outcome.get("englishLabel")),
    )
    if price is None or outcome_key is None:
        return None
    selection_id = str(outcome.get("id") or f"{market_id}:{outcome_key}")
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=KtoCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(offer: JsonObject) -> MarketKey | None:
    criterion = offer.get("criterion")
    bet_offer_type = offer.get("betOfferType")
    if not isinstance(criterion, Mapping) or not isinstance(bet_offer_type, Mapping):
        return None

    criterion_english_label = _optional_string(criterion.get("englishLabel"))
    type_english_name = _optional_string(bet_offer_type.get("englishName"))
    if criterion_english_label == "Full Time" and type_english_name == "Match":
        return "1x2"
    if (
        criterion_english_label == "Total Goals"
        and type_english_name == "Over/Under"
        and _offer_has_line(offer, Decimal("2.5"))
    ):
        return "over_under_2_5"

    label = _string(criterion.get("label"), default=str(offer.get("id") or "market"))
    normalized = canonical_market_name(label)
    if normalized == "1x2" or "resultado" in normalized:
        return "1x2"
    if normalized.startswith("over_under_2_5") or "total" in normalized:
        return "over_under_2_5"
    if (
        normalized.startswith("both_teams_score")
        or "ambas" in normalized
        or "ambos" in normalized
        or "both teams" in normalized
    ):
        return "both_teams_score"
    if "dupla" in normalized or "chance dupla" in normalized:
        return "double_chance"
    return None


def _outcome_key(
    market_key: MarketKey,
    label: str,
    match: Match,
    *,
    outcome_type: str | None,
    english_label: str | None,
) -> str | None:
    if market_key == "1x2":
        if outcome_type == "OT_ONE" or english_label == "1":
            return "home"
        if outcome_type == "OT_CROSS" or english_label == "X":
            return "draw"
        if outcome_type == "OT_TWO" or english_label == "2":
            return "away"
    if market_key == "over_under_2_5":
        if outcome_type == "OT_OVER" or english_label == "Over":
            return "over"
        if outcome_type == "OT_UNDER" or english_label == "Under":
            return "under"

    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")
    if market_key == "1x2":
        if normalized in {"empate", "draw", "x"}:
            return "draw"
        if _team_label_matches(normalized, match.home_team) or compact == "1":
            return "home"
        if _team_label_matches(normalized, match.away_team) or compact == "2":
            return "away"
    if market_key == "over_under_2_5":
        if normalized.startswith("over_under_2_5:over"):
            return "over"
        if normalized.startswith("over_under_2_5:under"):
            return "under"
    if market_key == "both_teams_score":
        if normalized.startswith("both_teams_score:yes") or compact in {"sim", "yes", "gg"}:
            return "yes"
        if normalized.startswith("both_teams_score:no") or compact in {"nao", "no", "ng"}:
            return "no"
    if market_key == "double_chance":
        if compact in {"1x", "home_draw"}:
            return "home_draw"
        if compact in {"12", "home_away"}:
            return "home_away"
        if compact in {"x2", "draw_away"}:
            return "draw_away"
    return None


def _extract_teams(name: str) -> tuple[str, str]:
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in name:
            home, away = name.split(separator, 1)
            return home.strip(), away.strip()
    return name, "Unknown"


def _team_label_matches(label: str, team_name: str) -> bool:
    team = canonical_market_name(team_name)
    return label == team or label in team or team in label


def _parse_price(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    price = raw / Decimal("1000") if raw >= Decimal("100") else raw
    if not price.is_finite() or price <= Decimal("1"):
        return None
    return price


def _parse_line(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return raw / Decimal("1000") if raw >= Decimal("100") else raw


def _offer_has_line(offer: JsonObject, target: Decimal) -> bool:
    outcomes = offer.get("outcomes")
    if not isinstance(outcomes, list):
        return False
    return any(
        isinstance(outcome, Mapping) and _parse_line(outcome.get("line")) == target
        for outcome in outcomes
    )


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
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default


def _latency_ms(started_at: datetime) -> int:
    return int((datetime.now(UTC) - started_at).total_seconds() * 1000)
