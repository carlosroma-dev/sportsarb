"""Coleta de tenis pre-jogo da Betano (ancora do universo de tenis).

Fluxo: landing ``/sport/tenis/`` (SSR) -> paginas de torneio de simples ->
paths ``/odds/{slug}/{id}/`` -> objeto SSR ``"event":{...}`` por evento, com
enriquecimento opcional via API ``bt=6`` (mesma usada no futebol) para linhas
alternativas de total de games.

Paginas agregadas (``/competicoes/...``) sao renderizadas por JS e nao expoem
eventos no SSR; torneios de duplas sao excluidos do MVP.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog

from odds_arb.collectors.deep.betano_live import (
    BETANO_BASE,
    HtmlGetter,
    JsonGetter,
    _default_html_getter,
    _default_json_getter,
    _merge_deep_markets,
    build_betano_deep_url,
    discover_betano_event_paths,
    extract_betano_deep_markets,
    extract_betano_event,
)
from odds_arb.core.deep_markets import Side
from odds_arb.core.tennis_markets import TennisMarket, TennisMarketOdd

logger = structlog.get_logger(__name__)

BETANO_TENNIS_LANDING_URL = f"{BETANO_BASE}/sport/tenis/"
BETANO_TENNIS_SPORT_ID = "TENN"

# Vencedor da partida (head-to-head) e total de games da partida. Set totals
# (FSTO), totais por jogador (P1FO/P2FO/OUG1/OUG2), combos (MWOU) e handicaps
# (TGHC) tem type codes proprios e ficam de fora por construcao.
BETANO_WINNER_MARKET_TYPE = "HTOH"
BETANO_TOTAL_GAMES_MARKET_TYPE = "FTGO"

_TOURNAMENT_URL_RE = re.compile(r'"url":"(/sport/tenis/[a-z0-9-]+/[a-z0-9-]+/\d+/)"')


def discover_betano_tennis_tournaments(landing_html: str) -> list[str]:
    tournaments: list[str] = []
    for path in sorted(set(_TOURNAMENT_URL_RE.findall(landing_html))):
        segments = path.strip("/").split("/")
        # /sport/tenis/competicoes/... e pagina agregada client-side (sem SSR).
        if "competicoes" in segments:
            continue
        if "duplas" in path:
            continue
        tournaments.append(path)
    return tournaments


def fetch_betano_tennis_events(
    *,
    html_getter: HtmlGetter = _default_html_getter,
    json_getter: JsonGetter | None = _default_json_getter,
    landing_url: str = BETANO_TENNIS_LANDING_URL,
) -> list[Mapping[str, Any]]:
    landing_html = html_getter(landing_url)
    event_paths: set[str] = set()
    for tournament_path in discover_betano_tennis_tournaments(landing_html):
        try:
            tournament_html = html_getter(BETANO_BASE + tournament_path)
        except Exception as exc:  # um torneio ruim nao derruba a casa
            logger.warning(
                "betano_tennis.tournament_fetch_failed",
                path=tournament_path,
                error=str(exc),
            )
            continue
        event_paths.update(discover_betano_event_paths(tournament_html))

    events: list[Mapping[str, Any]] = []
    for path in sorted(event_paths):
        try:
            event_html = html_getter(BETANO_BASE + path)
        except Exception as exc:  # um evento ruim nao derruba a casa
            logger.warning("betano_tennis.event_fetch_failed", path=path, error=str(exc))
            continue
        event = extract_betano_event(event_html)
        if event is None or str(event.get("sportId") or "") != BETANO_TENNIS_SPORT_ID:
            continue
        if json_getter is not None:
            try:
                deep_markets = extract_betano_deep_markets(json_getter(build_betano_deep_url(path)))
            except Exception as exc:  # enriquecimento e opcional, SSR ja basta
                logger.warning("betano_tennis.deep_fetch_failed", path=path, error=str(exc))
                deep_markets = []
            if deep_markets:
                event = _merge_deep_markets(event, deep_markets)
        events.append(event)
    return events


def _decimal(value: object) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _start_time(value: object) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(ts, tz=UTC)
    return datetime.now(UTC)


def _players(event: Mapping[str, Any]) -> tuple[str, str] | None:
    participants = event.get("participants")
    if isinstance(participants, list) and len(participants) == 2:
        names = [str(p.get("name") or "").strip() for p in participants if isinstance(p, Mapping)]
        if len(names) == 2 and all(names):
            return names[0], names[1]
    name = str(event.get("name") or "")
    for sep in (" - ", " vs ", " x "):
        if sep in name:
            a, b = name.split(sep, 1)
            if a.strip() and b.strip():
                return a.strip(), b.strip()
    return None


def _side_from_selection(name: str) -> Side | None:
    lowered = name.strip().lower()
    if lowered.startswith(("mais de", "over")):
        return Side.OVER
    if lowered.startswith(("menos de", "under")):
        return Side.UNDER
    return None


def _build_odd(**kwargs: Any) -> TennisMarketOdd | None:
    try:
        return TennisMarketOdd(**kwargs)
    except ValueError:
        # Odd fora de faixa, seleção que não é jogador, etc.: pula sem derrubar.
        return None


def parse_betano_tennis_event(event: Mapping[str, Any]) -> list[TennisMarketOdd]:
    if str(event.get("sportId") or "") != BETANO_TENNIS_SPORT_ID:
        return []
    if event.get("liveNow"):
        return []
    players = _players(event)
    if players is None:
        return []
    player_a, player_b = players
    if "/" in player_a or "/" in player_b:
        return []  # duplas fora do MVP

    event_id = str(event.get("id") or "")
    event_name = str(event.get("name") or f"{player_a} - {player_b}")
    start_time = _start_time(event.get("startTime"))
    competition = event.get("leagueName") or event.get("regionName")
    event_url = event.get("url")
    if isinstance(event_url, str) and event_url:
        source_event_url: str | None = f"{BETANO_BASE}{event_url}"
    elif event_id:
        source_event_url = f"{BETANO_BASE}/odds/{event_id}/"
    else:
        source_event_url = None

    common: dict[str, Any] = {
        "bookmaker": "betano",
        "raw_event_id": event_id or "unknown",
        "event_name": event_name,
        "player_a": player_a,
        "player_b": player_b,
        "start_time": start_time,
        "is_live": False,
        "competition_name": str(competition) if competition else None,
        "source_event_url": source_event_url,
    }

    odds: list[TennisMarketOdd] = []
    markets = event.get("markets")
    if not isinstance(markets, list):
        return odds
    for market in markets:
        if not isinstance(market, Mapping):
            continue
        market_type = str(market.get("type") or "")
        if market_type not in (BETANO_WINNER_MARKET_TYPE, BETANO_TOTAL_GAMES_MARKET_TYPE):
            continue
        market_id = str(market.get("id") or "")
        market_name = str(market.get("name") or "")
        selections = market.get("selections")
        if not isinstance(selections, list):
            continue
        for selection in selections:
            if not isinstance(selection, Mapping):
                continue
            price = _decimal(selection.get("price"))
            selection_name = str(selection.get("name") or "")
            if price is None or not selection_name:
                continue
            base: dict[str, Any] = {
                **common,
                "raw_market_id": market_id or "unknown",
                "raw_selection_id": str(selection.get("id") or f"{market_id}:{selection_name}"),
                "raw_market_name": market_name or market_type,
                "raw_selection_name": selection_name,
                "odd": price,
            }
            if market_type == BETANO_WINNER_MARKET_TYPE:
                built = _build_odd(
                    market=TennisMarket.MATCH_WINNER,
                    winner_player=selection_name,
                    **base,
                )
            else:
                side = _side_from_selection(selection_name)
                line = _decimal(selection.get("handicap"))
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
