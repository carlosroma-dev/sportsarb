from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from odds_arb.core.deep_markets import (
    DeepMarketNormalizationResult,
    LineSource,
    normalize_deep_market,
)

ALTENAR_ODD_OVER = 12
ALTENAR_ODD_UNDER = 13
ESTRELABET_SOURCE_EVENT_URL = "https://www.estrelabet.bet.br/aposta-esportiva"


def parse_estrelabet_deep_event(
    payload: Mapping[str, Any],
) -> list[DeepMarketNormalizationResult]:
    return parse_altenar_deep_event(
        payload,
        bookmaker="estrelabet",
        source_event_url=ESTRELABET_SOURCE_EVENT_URL,
    )


def parse_altenar_deep_event(
    payload: Mapping[str, Any],
    *,
    bookmaker: str,
    source_event_url: str | None = None,
) -> list[DeepMarketNormalizationResult]:
    event_id = _string(payload.get("id"), default="unknown")
    event_name = _string(payload.get("name"), default="Unknown vs. Unknown")
    home_team, away_team = _extract_teams(payload, event_name)
    start_time = _parse_datetime(payload.get("startDate"))
    is_live = _optional_int(payload.get("et")) not in {None, 0} or payload.get("rc") is True
    competition = payload.get("champ")
    category = payload.get("category")

    odds_by_id = _objects_by_id(payload.get("odds"))
    results: list[DeepMarketNormalizationResult] = []
    seen_market_ids: set[str] = set()
    for market in _object_list(payload.get("markets")):
        market_id = _string(market.get("id"), default="market")
        if market_id in seen_market_ids:
            continue
        seen_market_ids.add(market_id)
        raw_market_name = _market_name(market)
        for odd in _market_odds(market, odds_by_id):
            odd_value = _parse_decimal(odd.get("price"))
            if odd_value is None:
                continue
            line = _parse_line(odd.get("sv")) or _parse_line(market.get("sv"))
            selection_id = _string(odd.get("id"), default=f"{market_id}:{len(results)}")
            selection_name = _selection_name(odd, line)
            status = _optional_int(odd.get("oddStatus"))
            results.append(
                normalize_deep_market(
                    bookmaker=bookmaker,
                    raw_event_id=event_id,
                    raw_market_id=market_id,
                    raw_selection_id=selection_id,
                    event_name=event_name,
                    home_team=home_team,
                    away_team=away_team,
                    start_time=start_time,
                    raw_market_name=raw_market_name,
                    raw_selection_name=selection_name,
                    line=line,
                    raw_line_value=_optional_string(odd.get("sv"))
                    or _optional_string(market.get("sv")),
                    line_source=LineSource.SELECTION_HANDICAP,
                    odd=odd_value,
                    is_live=is_live,
                    is_available=None if status is None else status == 0,
                    competition_name=_optional_string(_mapping_value(competition, "name")),
                    competition_id=_optional_string(_mapping_value(competition, "id")),
                    country=_optional_string(_mapping_value(category, "name")),
                    source_event_url=source_event_url,
                )
            )
    return results


def _market_name(market: Mapping[str, Any]) -> str:
    name = _string(market.get("name"), default=_string(market.get("id"), default="market"))
    normalized = name.lower()
    if "jogador" in normalized or "player" in normalized:
        return f"{name}; player prop"
    return name


def _selection_name(odd: Mapping[str, Any], line: Decimal | None) -> str:
    type_id = _optional_int(odd.get("typeId"))
    if line is not None:
        if type_id == ALTENAR_ODD_OVER:
            return f"Mais de {line}"
        if type_id == ALTENAR_ODD_UNDER:
            return f"Menos de {line}"
    return _string(odd.get("name"), default=_string(odd.get("id"), default="selection"))


def _market_odds(
    market: Mapping[str, Any],
    odds_by_id: Mapping[str, Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    ids = _flatten_odd_ids(market.get("desktopOddIds")) or _flatten_odd_ids(
        market.get("mobileOddIds")
    )
    odds: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_id in ids:
        odd_id = _optional_string(raw_id)
        if odd_id is None or odd_id in seen_ids:
            continue
        seen_ids.add(odd_id)
        odd = odds_by_id.get(odd_id)
        if odd is not None:
            odds.append(odd)
    return odds


def _flatten_odd_ids(value: object) -> list[object]:
    ids: list[object] = []
    if not isinstance(value, list):
        return ids
    for item in value:
        if isinstance(item, list):
            ids.extend(_flatten_odd_ids(item))
        else:
            ids.append(item)
    return ids


def _extract_teams(payload: Mapping[str, Any], event_name: str) -> tuple[str, str]:
    competitors = _object_list(payload.get("competitors"))
    if len(competitors) >= 2:
        home = _optional_string(competitors[0].get("name"))
        away = _optional_string(competitors[1].get("name"))
        if home is not None and away is not None:
            return home, away
    for separator in (" vs. ", " vs ", " - ", " x ", " v "):
        if separator in event_name:
            home_name, away_name = event_name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return event_name, "Unknown"


def _parse_line(value: object) -> Decimal | None:
    raw = _optional_string(value)
    if raw is None:
        return None
    first_part = raw.split("|", 1)[0].replace(",", ".")
    return _parse_decimal(first_part)


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


def _mapping_value(value: object, key: str) -> object:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _object_list(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _objects_by_id(value: object) -> dict[str, Mapping[str, Any]]:
    return {
        object_id: item
        for item in _object_list(value)
        if (object_id := _optional_string(item.get("id"))) is not None
    }


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().isdigit():
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
