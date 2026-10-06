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

_LINE_RE = re.compile(r"(?:mais|menos|over|under)\s+(?:de\s+)?(\d+(?:[.,]\d+)?)", re.IGNORECASE)


def extract_line_from_selection(name: str) -> Decimal | None:
    match = _LINE_RE.search(name)
    if match is None:
        return None
    try:
        return Decimal(match.group(1).replace(",", "."))
    except InvalidOperation:
        return None


def _teams(match_name: str) -> tuple[str, str]:
    for sep in ("·", " - ", " vs ", " x ", " v "):
        if sep in match_name:
            home, away = match_name.split(sep, 1)
            return home.strip(), away.strip()
    return match_name.strip(), "Unknown"


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _price(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_superbet_deep_event(
    event: Mapping[str, Any],
) -> list[DeepMarketNormalizationResult]:
    event_id = str(event.get("eventId") or "")
    home, away = _teams(str(event.get("matchName") or "Unknown·Unknown"))
    start_time = _parse_datetime(event.get("utcDate") or event.get("matchDate"))
    competition_name = event.get("tournamentName")
    competition_id = event.get("tournamentId")
    results: list[DeepMarketNormalizationResult] = []
    raw_odds = event.get("odds")
    if not isinstance(raw_odds, list):
        return results
    for raw in raw_odds:
        if not isinstance(raw, Mapping):
            continue
        selection_name = str(raw.get("name") or "")
        price = _price(raw.get("price"))
        if not selection_name or price is None:
            continue
        status = raw.get("status")
        is_available = None if status is None else status == "active"
        raw_selection_id = str(
            raw.get("uuid") or raw.get("outcomeId") or f"{raw.get('marketId')}:{selection_name}"
        )
        source_url = f"https://superbet.bet.br/eventos/{event_id}" if event_id else None
        results.append(
            normalize_deep_market(
                bookmaker="superbet",
                raw_event_id=event_id,
                raw_market_id=str(raw.get("marketId") or ""),
                raw_selection_id=raw_selection_id,
                event_name=str(event.get("matchName") or ""),
                home_team=home,
                away_team=away,
                start_time=start_time,
                raw_market_name=str(raw.get("marketName") or ""),
                raw_selection_name=selection_name,
                line=extract_line_from_selection(selection_name),
                raw_line_value=selection_name,
                line_source=LineSource.SELECTION_NAME_REGEX,
                odd=price,
                is_live=False,
                is_available=is_available,
                competition_name=str(competition_name) if competition_name else None,
                competition_id=str(competition_id) if competition_id else None,
                source_event_url=source_url,
            )
        )
    return results
