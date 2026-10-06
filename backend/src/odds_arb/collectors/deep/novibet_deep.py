from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from odds_arb.collectors.novibet import NOVIBET_HOST
from odds_arb.core.deep_markets import (
    DeepMarketNormalizationResult,
    LineSource,
    normalize_deep_market,
)

NOVIBET_CORNERS_TOTAL = "SOCCER_CORNERS_UNDER_OVER"
NOVIBET_CORNERS_FIRST_HALF_TOTAL = "SOCCER_CORNERS_FIRST_HALF_UNDER_OVER"
NOVIBET_CORNERS_SECOND_HALF_TOTAL = "SOCCER_CORNERS_SECOND_HALF_UNDER_OVER"

_MATCH_TOTAL_MARKETS = {
    NOVIBET_CORNERS_TOTAL: "Total de Escanteios",
    NOVIBET_CORNERS_FIRST_HALF_TOTAL: "1o Tempo - Total de Escanteios",
    NOVIBET_CORNERS_SECOND_HALF_TOTAL: "2o Tempo - Total de Escanteios",
    "SOCCER_GOALKICKS_UNDER_OVER": "Total de Tiros de Meta",
    "SOCCER_FOULS_UNDER_OVER": "Total de Faltas",
    "SOCCER_SHOTS_UNDER_OVER": "Total de Chutes",
    "SOCCER_SHOTSONTARGET_UNDER_OVER": "Total de Chutes no Gol",
    "SOCCER_OFFSIDES_UNDER_OVER": "Total de Impedimentos",
    "SOCCER_TACKLES_UNDER_OVER": "Total de Desarmes",
    "SOCCER_THROWINS_UNDER_OVER": "Total de Laterais",
}

_TEAM_TOTAL_MARKETS = {
    "SOCCER_CORNERS_HOME_UNDER_OVER": "{home} Escanteios",
    "SOCCER_CORNERS_AWAY_UNDER_OVER": "{away} Escanteios",
    "SOCCER_CORNERS_FIRST_HALF_HOME_UNDER_OVER": "1o Tempo - {home} Escanteios",
    "SOCCER_CORNERS_FIRST_HALF_AWAY_UNDER_OVER": "1o Tempo - {away} Escanteios",
    "SOCCER_CORNERS_SECOND_HALF_HOME_UNDER_OVER": "2o Tempo - {home} Escanteios",
    "SOCCER_CORNERS_SECOND_HALF_AWAY_UNDER_OVER": "2o Tempo - {away} Escanteios",
    "SOCCER_GOALKICKS_HOME_UNDER_OVER": "{home} Tiros de Meta",
    "SOCCER_GOALKICKS_AWAY_UNDER_OVER": "{away} Tiros de Meta",
    "SOCCER_FOULS_HOME_UNDER_OVER": "{home} Faltas",
    "SOCCER_FOULS_AWAY_UNDER_OVER": "{away} Faltas",
    "SOCCER_SHOTS_HOME_UNDER_OVER": "{home} Chutes",
    "SOCCER_SHOTS_AWAY_UNDER_OVER": "{away} Chutes",
    "SOCCER_SHOTSONTARGET_HOME_UNDER_OVER": "{home} Chutes no Gol",
    "SOCCER_SHOTSONTARGET_AWAY_UNDER_OVER": "{away} Chutes no Gol",
    "SOCCER_OFFSIDES_HOME_UNDER_OVER": "{home} Impedimentos",
    "SOCCER_OFFSIDES_AWAY_UNDER_OVER": "{away} Impedimentos",
    "SOCCER_TACKLES_HOME_UNDER_OVER": "{home} Desarmes",
    "SOCCER_TACKLES_AWAY_UNDER_OVER": "{away} Desarmes",
    "SOCCER_THROWINS_HOME_UNDER_OVER": "{home} Laterais",
    "SOCCER_THROWINS_AWAY_UNDER_OVER": "{away} Laterais",
}


def parse_novibet_deep_event(
    event: Mapping[str, Any],
) -> list[DeepMarketNormalizationResult]:
    event_id = _string(
        event.get("eventBetContextId") or event.get("betContextId"), default="unknown"
    )
    home, away = _extract_teams(event)
    event_name = f"{home} - {away}"
    start_time = _parse_datetime(
        event.get("startDateTime") or event.get("startTimeUTC") or event.get("startTime")
    )
    is_live = event.get("isLive") is True
    source_url = _source_url(event)

    results: list[DeepMarketNormalizationResult] = []
    for market in _market_payloads(event):
        market_id = _string(market.get("marketId"), default=f"market:{len(results)}")
        market_name = _market_name(market, home=home, away=away)
        if market_name is None:
            continue
        for item in _object_list(market.get("betItems")):
            odd = _parse_decimal(item.get("price"))
            if odd is None:
                continue
            line = _parse_line(_optional_string(item.get("caption")) or "")
            selection_id = _string(item.get("id"), default=f"{market_id}:{len(results)}")
            results.append(
                normalize_deep_market(
                    bookmaker="novibet",
                    raw_event_id=event_id,
                    raw_market_id=market_id,
                    raw_selection_id=selection_id,
                    event_name=event_name,
                    home_team=home,
                    away_team=away,
                    start_time=start_time,
                    raw_market_name=market_name,
                    raw_selection_name=_string(item.get("caption"), default=selection_id),
                    line=line,
                    raw_line_value=_optional_string(item.get("caption")),
                    line_source=LineSource.SELECTION_NAME_REGEX,
                    odd=odd,
                    is_live=is_live,
                    is_available=None
                    if item.get("isAvailable") is None
                    else item.get("isAvailable") is True,
                    competition_name=_optional_string(event.get("competitionCaption")),
                    competition_id=_optional_string(event.get("competitionId")),
                    country=_optional_string(event.get("regionCaption")),
                    source_event_url=source_url,
                )
            )
    return results


def _legacy_market_name(market: Mapping[str, Any]) -> str | None:
    sysname = _optional_string(market.get("betTypeSysname"))
    if sysname == NOVIBET_CORNERS_TOTAL:
        return "Total de Escanteios"
    if sysname == NOVIBET_CORNERS_FIRST_HALF_TOTAL:
        return "1º Tempo - Total de Escanteios"
    return None


def _market_payloads(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    markets: list[Mapping[str, Any]] = []
    markets.extend(_object_list(event.get("markets")))
    _collect_nested_markets(event.get("marketCategories"), markets)
    seen: set[tuple[str | None, str | None, str | None]] = set()
    deduped: list[Mapping[str, Any]] = []
    for market in markets:
        key = (
            _optional_string(market.get("marketId")),
            _market_sysname(market),
            _optional_string(market.get("caption") or market.get("displayCaption")),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(market)
    return deduped


def _collect_nested_markets(value: object, markets: list[Mapping[str, Any]]) -> None:
    if isinstance(value, Mapping):
        if _market_sysname(value) is not None and isinstance(value.get("betItems"), list):
            markets.append(value)
            return
        for child in value.values():
            _collect_nested_markets(child, markets)
    elif isinstance(value, list):
        for child in value:
            _collect_nested_markets(child, markets)


def _market_name(market: Mapping[str, Any], *, home: str, away: str) -> str | None:
    sysname = _market_sysname(market)
    if sysname is None:
        return None
    if sysname in _MATCH_TOTAL_MARKETS:
        return _MATCH_TOTAL_MARKETS[sysname]
    template = _TEAM_TOTAL_MARKETS.get(sysname)
    if template is not None:
        return template.format(home=home, away=away)
    return None


def _market_sysname(market: Mapping[str, Any]) -> str | None:
    return _optional_string(market.get("betTypeSysname") or market.get("marketSysname"))


def _extract_teams(event: Mapping[str, Any]) -> tuple[str, str]:
    captions = event.get("additionalCaptions")
    if isinstance(captions, Mapping):
        home = _optional_string(captions.get("competitor1"))
        away = _optional_string(captions.get("competitor2"))
        if home and away:
            return home, away
    return "Unknown", "Unknown"


def _source_url(event: Mapping[str, Any]) -> str:
    path = _optional_string(event.get("path"))
    if path is None:
        return f"{NOVIBET_HOST}/apostas-esportivas"
    return f"{NOVIBET_HOST}/apostas-esportivas/futebol/{path.lstrip('/')}"


def _parse_line(value: str) -> Decimal | None:
    match = re.search(r"\b(\d+(?:[,.]\d+)?)\b", value)
    if match is None:
        return None
    return _parse_decimal(match.group(1).replace(",", "."))


def _parse_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _object_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
