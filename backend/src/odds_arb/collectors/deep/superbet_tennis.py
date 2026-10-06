"""Coleta de tenis pre-jogo da Superbet, pareada contra o universo da Betano.

Mesma API de oferta usada no futebol (``events/by-date`` + detalhe por evento),
trocando ``sportId`` para tenis. Mercados aceitos por ``marketId`` exato:
521 = "Vencedor da Partida" (selecoes "1"/"2"), 1002 = "Total de Games" da
partida (linha em ``specifiers.total``/``specialBetValue``). Set totals (524),
totais por jogador (996/999), combos (238832 etc.) e handicaps (520) ficam de
fora por construcao.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from odds_arb.collectors.deep.superbet_live import (
    SUPERBET_BY_DATE_URL,
    JsonGetter,
    _default_json_getter,
)
from odds_arb.collectors.deep.superbet_live import (
    fetch_superbet_detail as _fetch_superbet_detail,
)
from odds_arb.core.deep_markets import Side
from odds_arb.core.tennis_markets import (
    TennisMarket,
    TennisMarketOdd,
    canonical_player_key,
)

SUPERBET_SPORT_ID_TENNIS = "2"
SUPERBET_WINNER_MARKET_ID = "521"
SUPERBET_TOTAL_GAMES_MARKET_ID = "1002"


@dataclass(frozen=True)
class SuperbetTennisListEvent:
    event_id: str
    match_name: str


def _split_players(match_name: str) -> tuple[str, str] | None:
    for sep in ("·", " - ", " vs ", " v "):
        if sep in match_name:
            a, b = match_name.split(sep, 1)
            if a.strip() and b.strip():
                return a.strip(), b.strip()
    return None


def discover_superbet_tennis_events(
    list_payload: Mapping[str, Any],
) -> list[SuperbetTennisListEvent]:
    data = list_payload.get("data")
    if not isinstance(data, list):
        return []
    events: list[SuperbetTennisListEvent] = []
    for item in data:
        if not isinstance(item, Mapping):
            continue
        event_id = str(item.get("eventId") or "")
        match_name = str(item.get("matchName") or "")
        if not event_id or not match_name:
            continue
        if "/" in match_name:  # duplas fora do MVP
            continue
        if _split_players(match_name) is None:
            continue
        events.append(SuperbetTennisListEvent(event_id=event_id, match_name=match_name))
    return events


def superbet_tennis_player_set(match_name: str) -> frozenset[str]:
    players = _split_players(match_name)
    if players is None:
        return frozenset({canonical_player_key(match_name)})
    return frozenset({canonical_player_key(players[0]), canonical_player_key(players[1])})


def pair_tennis_to_betano_universe(
    superbet_events: Sequence[SuperbetTennisListEvent],
    betano_player_sets: set[frozenset[str]],
) -> list[str]:
    return [
        event.event_id
        for event in superbet_events
        if superbet_tennis_player_set(event.match_name) in betano_player_sets
    ]


def build_tennis_by_date_params(start: datetime, end: datetime) -> dict[str, str]:
    return {
        "currentStatus": "active",
        "offerState": "prematch",
        "startDate": start.strftime("%Y-%m-%d %H:%M:%S"),
        "endDate": end.strftime("%Y-%m-%d %H:%M:%S"),
        "sportId": SUPERBET_SPORT_ID_TENNIS,
    }


def fetch_superbet_tennis_list(
    start: datetime,
    end: datetime,
    *,
    json_getter: JsonGetter = _default_json_getter,
) -> Mapping[str, Any]:
    payload = json_getter(SUPERBET_BY_DATE_URL, build_tennis_by_date_params(start, end))
    return payload if isinstance(payload, Mapping) else {}


def fetch_superbet_tennis_detail(
    event_id: str,
    *,
    json_getter: JsonGetter = _default_json_getter,
) -> Mapping[str, Any] | None:
    return _fetch_superbet_detail(event_id, json_getter=json_getter)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _total_line(raw: Mapping[str, Any]) -> Decimal | None:
    specifiers = raw.get("specifiers")
    if isinstance(specifiers, Mapping) and specifiers.get("total") is not None:
        return _decimal(specifiers.get("total"))
    if raw.get("specialBetValue") is not None:
        return _decimal(raw.get("specialBetValue"))
    return None


def _total_side(raw: Mapping[str, Any]) -> Side | None:
    code = str(raw.get("code") or "")
    if code == "+":
        return Side.OVER
    if code == "-":
        return Side.UNDER
    name = str(raw.get("name") or "").strip().lower()
    if name.startswith(("mais de", "over")):
        return Side.OVER
    if name.startswith(("menos de", "under")):
        return Side.UNDER
    return None


def _build_odd(**kwargs: Any) -> TennisMarketOdd | None:
    try:
        return TennisMarketOdd(**kwargs)
    except ValueError:
        return None


def parse_superbet_tennis_event(event: Mapping[str, Any]) -> list[TennisMarketOdd]:
    match_name = str(event.get("matchName") or "")
    if "/" in match_name:  # duplas fora do MVP
        return []
    players = _split_players(match_name)
    if players is None:
        return []
    player_a, player_b = players

    event_id = str(event.get("eventId") or "")
    start_time = _parse_datetime(event.get("utcDate") or event.get("matchDate"))
    tournament_name = event.get("tournamentName")
    source_url = f"https://superbet.bet.br/eventos/{event_id}" if event_id else None

    common: dict[str, Any] = {
        "bookmaker": "superbet",
        "raw_event_id": event_id or "unknown",
        "event_name": match_name,
        "player_a": player_a,
        "player_b": player_b,
        "start_time": start_time,
        "is_live": False,
        "competition_name": str(tournament_name) if tournament_name else None,
        "source_event_url": source_url,
    }

    odds: list[TennisMarketOdd] = []
    raw_odds = event.get("odds")
    if not isinstance(raw_odds, list):
        return odds
    for raw in raw_odds:
        if not isinstance(raw, Mapping):
            continue
        market_id = str(raw.get("marketId") or "")
        if market_id not in (SUPERBET_WINNER_MARKET_ID, SUPERBET_TOTAL_GAMES_MARKET_ID):
            continue
        if str(raw.get("status") or "active") != "active":
            continue
        price = _decimal(raw.get("price"))
        selection_name = str(raw.get("name") or "")
        if price is None or not selection_name:
            continue
        base: dict[str, Any] = {
            **common,
            "raw_market_id": market_id,
            "raw_selection_id": str(
                raw.get("uuid") or raw.get("outcomeId") or f"{market_id}:{selection_name}"
            ),
            "raw_market_name": str(raw.get("marketName") or ""),
            "raw_selection_name": selection_name,
            "odd": price,
        }
        if market_id == SUPERBET_WINNER_MARKET_ID:
            code = str(raw.get("code") or selection_name)
            if code == "1":
                winner = player_a
            elif code == "2":
                winner = player_b
            else:
                continue
            built = _build_odd(
                market=TennisMarket.MATCH_WINNER,
                winner_player=winner,
                **base,
            )
        else:
            side = _total_side(raw)
            line = _total_line(raw)
            if side is None or line is None:
                continue
            built = _build_odd(
                market=TennisMarket.MATCH_TOTAL_GAMES,
                side=side,
                line=line,
                **base,
            )
        if built is not None:
            odds.append(built)
    return odds
