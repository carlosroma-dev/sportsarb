from __future__ import annotations

from collections.abc import Mapping, Sequence
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

# Pixbet is a white-label of the First Sports SB (FSSB) sportsbook; the whole offer
# lives in an iframe on prod20383.fssb.io. A plain GET on the iframe URL hands back two
# anonymous JWT cookies (session + authorization) that the JSON API requires.
PIXBET_HOST = "https://prod20383.fssb.io"
PIXBET_IFRAME_URL = (
    f"{PIXBET_HOST}/br-pt/spbk/Futebol"
    "?operatorToken=logout"
    "&api=https%3A%2F%2Fpix.bet.br%2Fscripts%2Fwhl%2Fproduction%2Fpixbet%2Fwhl.js"
)
PIXBET_MARKETTYPES_URL = f"{PIXBET_HOST}/api/eventlist/eu/events/v2/marketTypes"
# FSSB SportId for Futebol (confirmed against the live navigation tree; 234 is E-Futebol).
PIXBET_SPORT_ID_FOOTBALL = "1"
# ML0 == "Resultado Final" (1X2), the main match-result market type.
PIXBET_MARKET_TYPE_1X2 = "ML0"
# OU0 == "Total de Gols Mais/Menos"; the 2.5 line lives on selection index 13.
PIXBET_MARKET_TYPE_OVER_UNDER = "OU0"
# QA158 == "Ambas equipes Marcam".
PIXBET_MARKET_TYPE_BTTS = "QA158"
# QA61 == "Chance Dupla"; Q6/Q7/Q8 selections map to 1X/X2/12 respectively.
PIXBET_MARKET_TYPE_DOUBLE_CHANCE = "QA61"
PIXBET_MARKET_TYPE_IDS = ",".join(
    (
        PIXBET_MARKET_TYPE_1X2,
        PIXBET_MARKET_TYPE_OVER_UNDER,
        PIXBET_MARKET_TYPE_BTTS,
        PIXBET_MARKET_TYPE_DOUBLE_CHANCE,
    )
)
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_EVENT_LIMIT = 1000
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "time-area": "",
    "Referer": PIXBET_IFRAME_URL,
}
JsonObject = Mapping[str, Any]

# The marketTypes feed ships every event as a POSITIONAL array (fields by index, not name).
# These constants pin the layout confirmed against the live API; everything that reads the
# arrays guards length/type first so a layout shift degrades to "skip", never a crash.
_EVENT_ID = 0
_EVENT_LEAGUE = 2  # league_name, e.g. "Copa do Mundo 2026"
_EVENT_PARTICIPANTS = 8  # [[team_id, {"BR-PT": name}, "Home"|"Away"], ...]
_EVENT_NAME = 10  # "Home vs Away" display string
_EVENT_START = 11  # ISO-8601 UTC, e.g. "2026-06-17T23:00:00.000Z"
_EVENT_MARKETS = 19  # list of market arrays
_EVENT_MIN_LEN = _EVENT_MARKETS + 1

_PARTICIPANT_NAME = 1  # {"BR-PT": "Gana"}
_PARTICIPANT_SIDE = 2  # "Home" | "Away"
_PARTICIPANT_MIN_LEN = 3

_MARKET_ID = 0
_MARKET_LABEL = 1  # "Resultado Final"
_MARKET_TYPE_TAG = 3  # ["ML0", "Resultado Final", 1, 1]
_MARKET_SELECTIONS = 7
_MARKET_MIN_LEN = 8

_SEL_ID = 0  # "0ML...D" / "...H" / "...A" (suffix encodes the outcome)
_SEL_PRICE = 4  # decimal odds already, e.g. 2.13
_SEL_DISPLAY = 9  # {"BR-PT": "Casa"|"Empate"|"Fora"}
_SEL_LINE = 13  # OU0 point line, e.g. 2.5; only this line is emitted.
_SEL_MIN_LEN = 10


class PixbetAdapter:
    name = "pixbet"

    def __init__(
        self,
        *,
        host: str = PIXBET_HOST,
        iframe_url: str = PIXBET_IFRAME_URL,
        sport_id: str = PIXBET_SPORT_ID_FOOTBALL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        limit: int = DEFAULT_EVENT_LIMIT,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self.host = host
        self.iframe_url = iframe_url
        self.sport_id = sport_id
        self.timeout_seconds = timeout_seconds
        self.limit = limit
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
                # Bootstrap: the iframe GET sets the anonymous JWT cookies on the client jar.
                bootstrap = await client.get(self.iframe_url)
                bootstrap.raise_for_status()
                response = await client.get(
                    f"{self.host}/api/eventlist/eu/events/v2/marketTypes",
                    params={
                        "marketTypeIds": PIXBET_MARKET_TYPE_IDS,
                        "SportId": self.sport_id,
                        "limit": str(self.limit),
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
        return parse_pixbet_payload(payload, sport=sport, now=now)

    def normalize(self, event: RawEvent) -> list[Odd]:
        return event.odds()


class PixbetCollector(AdapterCollector):
    name = PixbetAdapter.name

    def __init__(
        self,
        *,
        host: str = PIXBET_HOST,
        iframe_url: str = PIXBET_IFRAME_URL,
        sport_id: str = PIXBET_SPORT_ID_FOOTBALL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        limit: int = DEFAULT_EVENT_LIMIT,
    ) -> None:
        super().__init__(
            PixbetAdapter(
                host=host,
                iframe_url=iframe_url,
                sport_id=sport_id,
                timeout_seconds=timeout_seconds,
                limit=limit,
            )
        )


def parse_pixbet_payload(
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
        event = _parse_event(raw_event, sport=sport, now=now)
        if event is not None:
            parsed_events.append(event)
    return parsed_events


def _parse_event(raw_event: object, *, sport: str, now: datetime | None) -> RawEvent | None:
    if not isinstance(raw_event, Sequence) or isinstance(raw_event, str | bytes):
        return None
    if len(raw_event) < _EVENT_MIN_LEN:
        return None
    event_id = _optional_string(raw_event[_EVENT_ID])
    if event_id is None:
        return None

    match = _parse_match(event_id, raw_event, sport=sport)
    if now is not None and match.starts_at <= now:
        return None
    markets = _parse_markets(raw_event[_EVENT_MARKETS], match)
    if not markets:
        return None
    return RawEvent(
        event_id=event_id,
        bookmaker=PixbetCollector.name,
        match=match,
        markets=markets,
    )


def _parse_match(event_id: str, raw_event: Sequence[Any], *, sport: str) -> Match:
    home_team, away_team = _extract_teams(raw_event[_EVENT_PARTICIPANTS])
    if home_team is None or away_team is None:
        home_team, away_team = _extract_teams_from_name(
            _string(raw_event[_EVENT_NAME], default="Unknown v Unknown")
        )
    starts_at = _parse_datetime(raw_event[_EVENT_START])
    temporary_match = Match(
        match_id=event_id,
        sport=sport,
        home_team=home_team,
        away_team=away_team,
        starts_at=starts_at,
        league=_optional_string(raw_event[_EVENT_LEAGUE]),
        raw_event_id=event_id,
    )
    return temporary_match.model_copy(update={"match_id": canonical_match_id(temporary_match)})


def _parse_markets(raw_markets: object, match: Match) -> list[RawMarket]:
    if not isinstance(raw_markets, list):
        return []
    markets: list[RawMarket] = []
    for raw_market in raw_markets:
        market = _parse_market(raw_market, match)
        if market is not None:
            markets.append(market)
    return markets


def _parse_market(raw_market: object, match: Match) -> RawMarket | None:
    if not isinstance(raw_market, Sequence) or isinstance(raw_market, str | bytes):
        return None
    if len(raw_market) < _MARKET_MIN_LEN:
        return None
    market_key = _market_key(raw_market)
    if market_key is None:
        return None
    market_id = _optional_string(raw_market[_MARKET_ID])
    label = _string(raw_market[_MARKET_LABEL], default=market_id or "Resultado Final")
    if market_id is None:
        return None

    raw_selections = raw_market[_MARKET_SELECTIONS]
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
    if not isinstance(raw_selection, Sequence) or isinstance(raw_selection, str | bytes):
        return None
    if len(raw_selection) < _SEL_MIN_LEN:
        return None
    selection_id = _optional_string(raw_selection[_SEL_ID])
    if selection_id is None:
        return None
    if market_key == "over_under_2_5" and _parse_line(raw_selection) != Decimal("2.5"):
        return None
    price = _parse_price(raw_selection[_SEL_PRICE])
    outcome_key = _outcome_key(
        market_key,
        _localized(raw_selection[1]),
        _localized(raw_selection[_SEL_DISPLAY]),
        selection_id,
    )
    if price is None or outcome_key is None:
        return None
    return Odd(
        match_id=match.match_id,
        market_key=market_key,
        outcome_key=outcome_key,
        price=price,
        bookmaker=PixbetCollector.name,
        event_id=match.raw_event_id,
        market_id=market_id,
        selection_id=selection_id,
        raw_label=f"{market_label} | {selection_id}",
    )


def _market_key(raw_market: Sequence[Any]) -> MarketKey | None:
    type_tag = raw_market[_MARKET_TYPE_TAG]
    if isinstance(type_tag, Sequence) and not isinstance(type_tag, str | bytes) and type_tag:
        type_code = _optional_string(type_tag[0])
        if type_code == PIXBET_MARKET_TYPE_1X2:
            return "1x2"
        if type_code == PIXBET_MARKET_TYPE_OVER_UNDER:
            return "over_under_2_5"
        if type_code == PIXBET_MARKET_TYPE_BTTS:
            return "both_teams_score"
        if type_code == PIXBET_MARKET_TYPE_DOUBLE_CHANCE:
            return "double_chance"

    normalized = canonical_market_name(_string(raw_market[_MARKET_LABEL], default=""))
    if normalized == "1x2":
        return "1x2"
    return None


def _outcome_key(
    market_key: MarketKey,
    label: str | None,
    display: str | None,
    selection_id: str,
) -> str | None:
    normalized_label = canonical_market_name(label or "")
    compact_label = normalized_label.replace(" ", "")
    normalized_display = canonical_market_name(display or "")
    compact_display = normalized_display.replace(" ", "")
    if market_key == "1x2":
        if normalized_display in {"casa", "home", "1"}:
            return "home"
        if normalized_display in {"empate", "draw", "x"}:
            return "draw"
        if normalized_display in {"fora", "away", "2"}:
            return "away"
        # Fallback: the selection id suffix encodes the outcome (...H / ...D / ...A).
        suffix = selection_id[-1:].upper()
        if suffix == "H":
            return "home"
        if suffix == "D":
            return "draw"
        if suffix == "A":
            return "away"
    if market_key == "over_under_2_5":
        if normalized_label.startswith("over_under_2_5:over") or normalized_display == "acima":
            return "over"
        if normalized_label.startswith("over_under_2_5:under") or normalized_display == "abaixo":
            return "under"
    if market_key == "both_teams_score":
        if normalized_label.startswith("both_teams_score:yes") or compact_label == "sim":
            return "yes"
        if normalized_label.startswith("both_teams_score:no") or compact_label == "nao":
            return "no"
    if market_key == "double_chance":
        if "Q6Q" in selection_id or compact_display == "casa":
            return "home_draw"
        if "Q8Q" in selection_id or compact_display == "fora":
            return "home_away"
        if "Q7Q" in selection_id or compact_display == "empate":
            return "draw_away"
        if "empate" in normalized_label:
            return "home_draw" if compact_label.startswith("casa") else "draw_away"
        if "ou" in normalized_label:
            return "home_away"
    return None


def _parse_line(raw_selection: Sequence[Any]) -> Decimal | None:
    if len(raw_selection) <= _SEL_LINE:
        return None
    value = raw_selection[_SEL_LINE]
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _extract_teams(participants: object) -> tuple[str | None, str | None]:
    if not isinstance(participants, list):
        return None, None
    home: str | None = None
    away: str | None = None
    for participant in participants:
        if not isinstance(participant, Sequence) or isinstance(participant, str | bytes):
            continue
        if len(participant) < _PARTICIPANT_MIN_LEN:
            continue
        name = _localized(participant[_PARTICIPANT_NAME])
        side = _optional_string(participant[_PARTICIPANT_SIDE])
        if side == "Home":
            home = name
        elif side == "Away":
            away = name
    return home, away


def _extract_teams_from_name(name: str) -> tuple[str, str]:
    for separator in (" vs ", " - ", " x ", " v "):
        if separator in name:
            home, away = name.split(separator, 1)
            return home.strip(), away.strip()
    return name, "Unknown"


def _localized(value: object) -> str | None:
    if isinstance(value, Mapping):
        localized = value.get("BR-PT")
        if isinstance(localized, str) and localized.strip():
            return localized.strip()
        for candidate in value.values():
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return None
    return _optional_string(value)


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
