from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from odds_arb.collectors.sportingbet import SPORTINGBET_HOST
from odds_arb.core.deep_markets import (
    DeepMarketNormalizationResult,
    LineSource,
    normalize_deep_market,
)

JsonObject = Mapping[str, Any]


def parse_sportingbet_deep_event(
    fixture: Mapping[str, Any],
) -> list[DeepMarketNormalizationResult]:
    event_id = str(fixture.get("id") or "")
    event_name = _string(_name_value(fixture), default="Unknown - Unknown")
    home, away = _extract_teams(fixture)
    start_time = _parse_datetime(fixture.get("startDate"))
    competition_name = _name_value(fixture.get("competition"))
    country = _name_value(fixture.get("region"))
    source_url = f"{SPORTINGBET_HOST}/pt-br/sports"

    results: list[DeepMarketNormalizationResult] = []
    for market in _market_payloads(fixture):
        if not isinstance(market, Mapping):
            continue
        market_id = str(market.get("id") or "")
        market_name = _market_name(market)
        parameters = _parameters(market)
        line = _parse_decimal(parameters.get("DecimalValue"))
        raw_options = market.get("options")
        if not isinstance(raw_options, list):
            raw_options = market.get("results")
        if not isinstance(raw_options, list):
            continue
        for option in raw_options:
            if not isinstance(option, Mapping):
                continue
            option_name = _string(_name_value(option), default=str(option.get("id") or "selection"))
            odd = _price(option.get("price"))
            if odd is None:
                continue
            option_id = str(option.get("id") or f"{market_id}:{option_name}")
            status = _optional_string(option.get("status"))
            is_available = None if status is None else status == "Visible"
            results.append(
                normalize_deep_market(
                    bookmaker="sportingbet",
                    raw_event_id=event_id,
                    raw_market_id=market_id,
                    raw_selection_id=option_id,
                    event_name=event_name,
                    home_team=home,
                    away_team=away,
                    start_time=start_time,
                    raw_market_name=market_name,
                    raw_selection_name=option_name,
                    line=line,
                    raw_line_value=str(parameters.get("DecimalValue"))
                    if parameters.get("DecimalValue") is not None
                    else None,
                    line_source=LineSource.MARKET_PARAMETER,
                    odd=odd,
                    is_live=False,
                    is_available=is_available,
                    competition_name=competition_name,
                    competition_id=_optional_string(_dig(fixture, "competition", "id")),
                    country=country,
                    source_event_url=source_url,
                )
            )
    return results


def _market_payloads(fixture: Mapping[str, Any]) -> list[object]:
    markets: list[object] = []
    option_markets = fixture.get("optionMarkets")
    if isinstance(option_markets, list):
        markets.extend(option_markets)
    games = fixture.get("games")
    if isinstance(games, list):
        markets.extend(games)
    return markets


def _market_name(market: Mapping[str, Any]) -> str:
    label = _string(_name_value(market), default=str(market.get("id") or "market"))
    parameters = _parameters(market)
    period = _optional_string(parameters.get("Period"))
    if period == "FirstHalf" and "tempo" not in label.lower():
        return f"1º Tempo - {label}"
    if period == "SecondHalf" and "tempo" not in label.lower():
        return f"2º Tempo - {label}"
    return label


def _parameters(raw: Mapping[str, Any]) -> dict[str, object]:
    raw_parameters = raw.get("parameters")
    if isinstance(raw_parameters, Mapping):
        return {str(key): value for key, value in raw_parameters.items()}
    if not isinstance(raw_parameters, list):
        return {}
    parameters: dict[str, object] = {}
    for item in raw_parameters:
        if not isinstance(item, Mapping):
            continue
        key = _optional_string(item.get("key"))
        if key is not None:
            parameters[key] = item.get("value")
    return parameters


def _extract_teams(fixture: Mapping[str, Any]) -> tuple[str, str]:
    participants = fixture.get("participants")
    if isinstance(participants, list):
        home: str | None = None
        away: str | None = None
        for participant in participants:
            if not isinstance(participant, Mapping):
                continue
            participant_type = _dig(participant, "properties", "type")
            participant_name = _name_value(participant)
            if participant_type == "HomeTeam":
                home = participant_name
            elif participant_type == "AwayTeam":
                away = participant_name
        if home and away:
            return home, away

    name = _string(_name_value(fixture), default="Unknown - Unknown")
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in name:
            home_name, away_name = name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return name, "Unknown"


def _name_value(raw: object) -> str | None:
    if isinstance(raw, Mapping):
        name = raw.get("name")
        if isinstance(name, Mapping):
            value = name.get("value")
            if isinstance(value, str) and value.strip():
                return value.strip()
        value = raw.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _price(value: object) -> Decimal | None:
    if isinstance(value, Mapping):
        value = value.get("odds")
    return _parse_decimal(value)


def _parse_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return decimal if decimal.is_finite() else None


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _dig(mapping: Mapping[str, Any], *keys: str) -> object:
    value: object = mapping
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
