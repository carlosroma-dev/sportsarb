from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

import structlog
from curl_cffi import requests

from odds_arb.collectors.base import (
    AdapterCollector,
    RawEvent,
    RawMarket,
    fetch_bytes_with_retries_sync,
    json_from_bytes,
    json_payload_bytes,
)
from odds_arb.core.dedup import canonical_market_name, canonical_match_id
from odds_arb.core.models import MarketKey, Match, Odd

BETANO_TOP_EVENTS_URL = "https://www.betano.bet.br/api/home/top-events-v2/"
BETANO_DEFAULT_IMPERSONATE = "chrome131"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.betano.bet.br/",
}
JsonObject = Mapping[str, Any]

logger = structlog.get_logger(__name__)


class BetanoAdapter:
    name = "betano"

    def __init__(
        self,
        *,
        url: str = BETANO_TOP_EVENTS_URL,
        impersonate: str = BETANO_DEFAULT_IMPERSONATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.url = url
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
        if not isinstance(payload, Mapping):
            return []
        return parse_betano_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()

    def _fetch_once_bytes(self) -> bytes:
        """Blocking curl_cffi call; run off the event loop via asyncio.to_thread."""
        with requests.Session(impersonate=cast(Any, self.impersonate)) as session:
            response = session.get(
                self.url,
                headers=DEFAULT_HEADERS,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return json_payload_bytes(payload if isinstance(payload, Mapping) else {})


class BetanoCollector(AdapterCollector):
    name = BetanoAdapter.name

    def __init__(
        self,
        *,
        url: str = BETANO_TOP_EVENTS_URL,
        impersonate: str = BETANO_DEFAULT_IMPERSONATE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__(
            BetanoAdapter(url=url, impersonate=impersonate, timeout_seconds=timeout_seconds)
        )


def parse_betano_payload(
    payload: JsonObject,
    *,
    sport: str = "soccer",
    now: datetime | None = None,
) -> list[RawEvent]:
    top_events = _dig(payload, "data", "topEventsV2")
    if not isinstance(top_events, Mapping):
        return []

    raw_events = _as_id_map(top_events.get("events"))
    raw_markets = _as_id_map(top_events.get("markets"))
    raw_selections = _as_id_map(top_events.get("selections"))
    parsed_events: list[RawEvent] = []

    for event_id, event in raw_events.items():
        match = _parse_match(event_id, event, sport=sport)
        if now is not None and match.starts_at <= now:
            continue
        markets = _parse_event_markets(
            event=event,
            raw_markets=raw_markets,
            raw_selections=raw_selections,
            match=match,
        )
        if markets:
            parsed_events.append(
                RawEvent(
                    event_id=event_id,
                    bookmaker=BetanoCollector.name,
                    match=match,
                    markets=markets,
                )
            )
    return parsed_events


def _parse_match(event_id: str, event: JsonObject, *, sport: str) -> Match:
    starts_at = _parse_datetime(
        _first_value(event, ("startTime", "startDate", "eventStartTime", "date", "startsAt"))
    )
    home_team, away_team = _extract_teams(event)
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(
            _first_value(event, ("leagueName", "competitionName", "regionName"))
        ),
        raw_event_id=event_id,
    )
    return temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})


def _parse_event_markets(
    *,
    event: JsonObject,
    raw_markets: Mapping[str, JsonObject],
    raw_selections: Mapping[str, JsonObject],
    match: Match,
) -> list[RawMarket]:
    markets: list[RawMarket] = []
    for market_id in _extract_ids(event, ("marketIdList", "marketIds", "markets")):
        market = raw_markets.get(market_id)
        if market is None:
            continue
        label = _string(_first_value(market, ("name", "label", "title")), default=market_id)
        selection_ids = _extract_ids(market, ("selectionIdList", "selectionIds", "selections"))
        market_key = _market_key(
            label,
            (raw_selections[id_] for id_ in selection_ids if id_ in raw_selections),
        )
        if market_key is None:
            continue

        selections = [
            odd
            for selection_id in selection_ids
            if (selection := raw_selections.get(selection_id)) is not None
            if (
                odd := _parse_selection(
                    selection_id=selection_id,
                    selection=selection,
                    market_key=market_key,
                    market_label=label,
                    market_id=market_id,
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
    selection_id: str,
    selection: JsonObject,
    market_key: MarketKey,
    market_label: str,
    market_id: str,
    match: Match,
) -> Odd | None:
    price = _parse_price(selection.get("price"))
    if price is None:
        return None
    label = _string(_first_value(selection, ("name", "label", "title")), default=selection_id)
    outcome_key = _outcome_key(market_key, label, match)
    if outcome_key is None:
        return None
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=BetanoCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {label}",
    )


def _market_key(label: str, selections: Iterable[JsonObject]) -> MarketKey | None:
    selection_list = tuple(selections)
    canonical = canonical_market_name(label)
    if canonical.startswith("over_under_2_5"):
        return "over_under_2_5"
    if canonical.startswith("both_teams_score") and _has_main_btts_outcomes(selection_list):
        return "both_teams_score"
    if canonical == "1x2":
        return "1x2"

    normalized = canonical_market_name(
        " ".join(_selection_name(selection) for selection in selection_list)
    )
    if normalized.startswith("over_under_2_5"):
        return "over_under_2_5"
    if normalized.startswith("both_teams_score") and _has_main_btts_outcomes(selection_list):
        return "both_teams_score"

    compact_label = label.lower()
    if "dupla" in compact_label or "double chance" in compact_label:
        return "double_chance"
    if _is_main_btts_label(label) and _has_main_btts_outcomes(selection_list):
        return "both_teams_score"
    return None


def _outcome_key(market_key: MarketKey, label: str, match: Match) -> str | None:
    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")
    if market_key == "1x2":
        if normalized in {"empate", "draw", "x"}:
            return "draw"
        if _team_label_matches(normalized, match.home_team) or compact in {"1", "casa", "home"}:
            return "home"
        if _team_label_matches(normalized, match.away_team) or compact in {"2", "fora", "away"}:
            return "away"
    if market_key == "over_under_2_5":
        if normalized.startswith("over_under_2_5:over"):
            return "over"
        if normalized.startswith("over_under_2_5:under"):
            return "under"
    if market_key == "both_teams_score":
        if _btts_caption_outcome(normalized) == "yes":
            return "yes"
        if _btts_caption_outcome(normalized) == "no":
            return "no"
    if market_key == "double_chance":
        if compact in {"1x", "home_draw"} or ("empate" in normalized and "casa" in normalized):
            return "home_draw"
        if compact in {"12", "home_away"}:
            return "home_away"
        if compact in {"x2", "draw_away"} or ("empate" in normalized and "fora" in normalized):
            return "draw_away"
    return None


def _extract_teams(event: JsonObject) -> tuple[str, str]:
    home = _optional_string(_first_value(event, ("homeTeam", "home", "team1", "participant1Name")))
    away = _optional_string(_first_value(event, ("awayTeam", "away", "team2", "participant2Name")))
    if home and away:
        return home, away

    participants = event.get("participants")
    if isinstance(participants, list):
        names = [
            _string(participant.get("name"), default="")
            for participant in participants
            if isinstance(participant, Mapping)
        ]
        if len(names) >= 2 and names[0] and names[1]:
            return names[0], names[1]

    name = (
        _optional_string(_first_value(event, ("name", "title", "eventName"))) or "Unknown v Unknown"
    )
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in name:
            home_name, away_name = name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return name, "Unknown"


def _team_label_matches(label: str, team_name: str) -> bool:
    team = canonical_market_name(team_name)
    return label == team or label in team or team in label


def _is_main_btts_label(label: str) -> bool:
    normalized = canonical_market_name(label)
    compact = normalized.replace(" ", "")
    if any(
        blocked in normalized
        for blocked in ("tempo", "half", "period", "periodo", "mais", "menos", "over", "under")
    ):
        return False
    return (
        ("ambas" in normalized and "marcam" in normalized)
        or ("ambos" in normalized and "marcam" in normalized)
        or "bothteam" in compact
        or "btts" in compact
    )


def _has_main_btts_outcomes(selections: Iterable[JsonObject]) -> bool:
    outcomes = [
        outcome
        for selection in selections
        if (outcome := _btts_caption_outcome(canonical_market_name(_selection_name(selection))))
        is not None
    ]
    return len(outcomes) == 2 and set(outcomes) == {"yes", "no"}


def _btts_caption_outcome(normalized: str) -> str | None:
    compact = normalized.replace(" ", "")
    if normalized.startswith("both_teams_score:yes") or compact in {"sim", "yes", "gg"}:
        return "yes"
    if normalized.startswith("both_teams_score:no") or compact in {"nao", "no", "ng"}:
        return "no"
    return None


def _parse_price(value: object) -> Decimal | None:
    if isinstance(value, Mapping):
        value = _first_value(value, ("decimal", "decimalPrice", "price", "odds"))
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
    if isinstance(value, int | float) and not isinstance(value, bool):
        timestamp = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str) and value:
        normalized = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.now(UTC)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed
    return datetime.now(UTC)


def _as_id_map(value: object) -> dict[str, JsonObject]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items() if isinstance(item, Mapping)}
    if isinstance(value, list):
        return {
            str(item.get("id")): item
            for item in value
            if isinstance(item, Mapping) and item.get("id") is not None
        }
    return {}


def _extract_ids(mapping: JsonObject, keys: tuple[str, ...]) -> list[str]:
    value = _first_value(mapping, keys)
    if isinstance(value, list):
        ids: list[str] = []
        for item in value:
            if isinstance(item, Mapping) and item.get("id") is not None:
                ids.append(str(item["id"]))
            elif item is not None:
                ids.append(str(item))
        return ids
    if isinstance(value, Mapping):
        return [str(key) for key in value]
    return []


def _dig(mapping: JsonObject, *keys: str) -> object:
    value: object = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _first_value(mapping: JsonObject, keys: tuple[str, ...]) -> object:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default


def _selection_name(selection: JsonObject) -> str:
    return _string(_first_value(selection, ("name", "label", "title")), default="")


def _latency_ms(started_at: datetime) -> int:
    return int((datetime.now(UTC) - started_at).total_seconds() * 1000)
