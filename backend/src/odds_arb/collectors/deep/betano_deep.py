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


def _price(value: object) -> Decimal | None:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return price if price.is_finite() else None


def _line(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _start_time(value: object) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=UTC)
    return datetime.now(UTC)


def _team_names(event: Mapping[str, Any]) -> tuple[str, str]:
    home = event.get("homeName")
    away = event.get("awayName")
    if isinstance(home, str) and isinstance(away, str) and home and away:
        return home, away
    name = str(event.get("name") or "Unknown - Unknown")
    for sep in (" - ", " vs ", " x "):
        if sep in name:
            h, a = name.split(sep, 1)
            return h.strip(), a.strip()
    return name, "Unknown"


def parse_betano_deep_event(
    event: Mapping[str, Any],
) -> list[DeepMarketNormalizationResult]:
    event_id = str(event.get("id") or "")
    event_name = str(event.get("name") or "")
    home, away = _team_names(event)
    start_time = _start_time(event.get("startTime"))
    competition = event.get("leagueName") or event.get("regionName")
    # The slug path (event["url"] = "/odds/{slug}/{id}/") is the URL that actually
    # resolves; the id-only form "/odds/{id}/" returns 404. Prefer the slug; fall
    # back to id-only only when the event omits its url.
    event_url = event.get("url")
    if isinstance(event_url, str) and event_url:
        source_event_url: str | None = f"https://www.betano.bet.br{event_url}"
    elif event_id:
        source_event_url = f"https://www.betano.bet.br/odds/{event_id}/"
    else:
        source_event_url = None
    common = {
        "bookmaker": "betano",
        "raw_event_id": event_id,
        "event_name": event_name,
        "home_team": home,
        "away_team": away,
        "start_time": start_time,
        "is_live": False,
        "competition_name": str(competition) if competition else None,
        "source_event_url": source_event_url,
    }
    results: list[DeepMarketNormalizationResult] = []
    markets = event.get("markets")
    if not isinstance(markets, list):
        return results
    for market in markets:
        if not isinstance(market, Mapping):
            continue
        market_id = str(market.get("id") or "")
        market_name = str(market.get("name") or "")
        table_layout = market.get("tableLayout")
        if isinstance(table_layout, Mapping):
            results.extend(_parse_table_layout(table_layout, market_id, common))
            continue
        selections = market.get("selections")
        if not isinstance(selections, list):
            continue
        for selection in selections:
            if not isinstance(selection, Mapping):
                continue
            price = _price(selection.get("price"))
            if price is None:
                continue
            hc = selection.get("handicap")
            results.append(
                normalize_deep_market(
                    raw_market_id=market_id,
                    raw_selection_id=str(
                        selection.get("id") or f"{market_id}:{selection.get('name')}"
                    ),
                    raw_market_name=market_name,
                    raw_selection_name=str(selection.get("name") or ""),
                    line=_line(hc),
                    raw_line_value=(str(hc) if hc is not None else None),
                    line_source=LineSource.SELECTION_HANDICAP,
                    odd=price,
                    **common,  # type: ignore[arg-type]
                )
            )
    return results


def _parse_table_layout(
    table_layout: Mapping[str, Any],
    market_id: str,
    common: dict[str, Any],
) -> list[DeepMarketNormalizationResult]:
    title = str(table_layout.get("title") or "")
    groups = {
        str(g.get("id")): str(g.get("title") or "")
        for g in table_layout.get("groups", [])
        if isinstance(g, Mapping)
    }
    rows = table_layout.get("rows")
    results: list[DeepMarketNormalizationResult] = []
    if not isinstance(rows, list):
        return results
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        subject = groups.get(str(row.get("groupId")), "")
        line = _line(row.get("line"))
        market_name = f"{subject} {title}".strip()
        for side_key in ("over", "under"):
            cell = row.get(side_key)
            if not isinstance(cell, Mapping):
                continue
            price = _price(cell.get("price"))
            if price is None:
                continue
            results.append(
                normalize_deep_market(
                    raw_market_id=market_id,
                    raw_selection_id=str(cell.get("id") or f"{market_id}:{side_key}:{line}"),
                    raw_market_name=market_name,
                    raw_selection_name=str(
                        cell.get("name")
                        or ("Mais de " if side_key == "over" else "Menos de ") + str(line)
                    ),
                    line=line,
                    raw_line_value=str(row.get("line")),
                    line_source=LineSource.TABLE_LAYOUT_ROW,
                    odd=price,
                    **common,
                )
            )
    return results
