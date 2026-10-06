from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from odds_arb.core.deep_markets import (
    DeepMarketNormalizationResult,
    LineSource,
    normalize_deep_market,
)

KTO_SOURCE_EVENT_URL = "https://www.kto.bet.br/app/esportes"

JsonObject = Mapping[str, Any]

_OPTA_SUFFIX = re.compile(r"\s*\((?:decidido|fechado)[^)]*opta[^)]*\)", re.IGNORECASE)


def parse_kto_deep_event(payload: Mapping[str, Any]) -> list[DeepMarketNormalizationResult]:
    event = _primary_event(payload)
    event_id = _string(event.get("id"), default="unknown")
    event_name = _string(event.get("name"), default="Unknown - Unknown")
    home_team, away_team = _extract_teams(event, event_name)
    start_time = _parse_datetime(event.get("start"))
    state = (_optional_string(event.get("state")) or "").upper()
    is_live = state in {"STARTED", "LIVE", "FINISHED", "ENDED", "CLOSED", "SETTLED"}
    competition_name = _optional_string(event.get("group"))
    competition_id = _competition_id(event)

    results: list[DeepMarketNormalizationResult] = []
    raw_offers = payload.get("betOffers")
    if not isinstance(raw_offers, list):
        return results
    for offer in raw_offers:
        if not isinstance(offer, Mapping):
            continue
        criterion = offer.get("criterion")
        if not isinstance(criterion, Mapping):
            continue
        market_id = _string(offer.get("id"), default=_string(criterion.get("id"), default="market"))
        market_name = _clean_market_name(_string(criterion.get("label"), default=market_id))
        bet_offer_type = offer.get("betOfferType")
        if isinstance(bet_offer_type, Mapping) and _is_player_offer(bet_offer_type):
            market_name = f"{market_name}; player prop"
        raw_outcomes = offer.get("outcomes")
        if not isinstance(raw_outcomes, list):
            continue
        for outcome in raw_outcomes:
            if not isinstance(outcome, Mapping):
                continue
            line = _parse_scaled_decimal(outcome.get("line"))
            odd = _parse_scaled_decimal(outcome.get("odds"))
            if odd is None:
                continue
            selection_id = _string(outcome.get("id"), default=f"{market_id}:{len(results)}")
            status = _optional_string(outcome.get("status"))
            is_available = None if status is None else status == "OPEN"
            raw_line_value = str(outcome.get("line")) if outcome.get("line") is not None else None
            results.append(
                normalize_deep_market(
                    bookmaker="kto",
                    raw_event_id=event_id,
                    raw_market_id=market_id,
                    raw_selection_id=selection_id,
                    event_name=event_name,
                    home_team=home_team,
                    away_team=away_team,
                    start_time=start_time,
                    raw_market_name=market_name,
                    raw_selection_name=_selection_name(outcome, line),
                    line=line,
                    raw_line_value=raw_line_value,
                    line_source=LineSource.SELECTION_HANDICAP,
                    odd=odd,
                    is_live=is_live,
                    is_available=is_available,
                    competition_name=competition_name,
                    competition_id=competition_id,
                    source_event_url=KTO_SOURCE_EVENT_URL,
                )
            )
    return results


def _primary_event(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    raw_events = payload.get("events")
    if isinstance(raw_events, list):
        for event in raw_events:
            if isinstance(event, Mapping):
                return event
    return {}


def _selection_name(outcome: Mapping[str, Any], line: Decimal | None) -> str:
    outcome_type = _optional_string(outcome.get("type"))
    label = _string(
        outcome.get("label"),
        default=_string(outcome.get("englishLabel"), default="selection"),
    )
    if line is not None:
        if outcome_type == "OT_OVER" or _optional_string(outcome.get("englishLabel")) == "Over":
            return f"Mais de {line}"
        if outcome_type == "OT_UNDER" or _optional_string(outcome.get("englishLabel")) == "Under":
            return f"Menos de {line}"
    english = _optional_string(outcome.get("englishLabel"))
    return f"{label} {english}" if english and english != label else label


def _clean_market_name(value: str) -> str:
    cleaned = _OPTA_SUFFIX.sub("", value).strip()
    return re.sub(r"\s+", " ", cleaned)


def _is_player_offer(bet_offer_type: Mapping[str, Any]) -> bool:
    name = " ".join(
        value
        for value in (
            _optional_string(bet_offer_type.get("name")),
            _optional_string(bet_offer_type.get("englishName")),
        )
        if value is not None
    ).lower()
    return "player" in name or "jogador" in name


def _extract_teams(event: Mapping[str, Any], event_name: str) -> tuple[str, str]:
    home = _optional_string(event.get("homeName"))
    away = _optional_string(event.get("awayName"))
    if home is not None and away is not None:
        return home, away
    for separator in (" - ", " vs ", " x ", " v "):
        if separator in event_name:
            home_name, away_name = event_name.split(separator, 1)
            return home_name.strip(), away_name.strip()
    return event_name, "Unknown"


def _competition_id(event: Mapping[str, Any]) -> str | None:
    path = event.get("path")
    if not isinstance(path, list):
        return None
    for item in reversed(path):
        if not isinstance(item, Mapping):
            continue
        value = _optional_string(item.get("id"))
        if value is not None:
            return value
    return None


def _parse_scaled_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not raw.is_finite():
        return None
    return raw / Decimal("1000") if raw >= Decimal("100") else raw


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _string(value: object, *, default: str) -> str:
    return _optional_string(value) or default
