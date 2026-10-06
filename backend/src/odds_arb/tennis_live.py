"""Ciclo live de tenis pre-jogo (MVP: Betano ancora + Superbet pareada).

Mesmo padrao operacional do deep scan de futebol, em um ciclo separado: a
Betano define o universo de partidas; a Superbet e descoberta via listagem,
pareada por par de jogadores (ordem-independente) e so os eventos pareados tem
detalhe buscado. Nada aqui toca o caminho de futebol.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog

from odds_arb.collectors.deep.betano_tennis import parse_betano_tennis_event
from odds_arb.collectors.deep.superbet_tennis import (
    discover_superbet_tennis_events,
    pair_tennis_to_betano_universe,
    parse_superbet_tennis_event,
)
from odds_arb.core.stake import calculate_stakes
from odds_arb.core.tennis_markets import (
    TennisArbOpportunity,
    TennisMarketOdd,
    detect_tennis_arbs,
    tennis_players_key,
)

logger = structlog.get_logger(__name__)

BetanoTennisFetcher = Callable[[], list[Mapping[str, Any]]]
SuperbetTennisListFetcher = Callable[[datetime, datetime], Mapping[str, Any]]
SuperbetTennisDetailFetcher = Callable[[str], Mapping[str, Any] | None]

# Horario de tenis e estimado e diverge entre casas; a janela do by-date da
# Superbet ganha uma folga para nao perder eventos por skew.
WINDOW_PADDING = timedelta(hours=2)


@dataclass(frozen=True)
class TennisLiveConfig:
    min_profit_pct: Decimal
    bankroll: Decimal
    interval_seconds: float


@dataclass(frozen=True)
class StakedTennisOpportunity:
    opportunity: TennisArbOpportunity
    stakes: dict[str, Decimal]


@dataclass(frozen=True)
class TennisLiveCycleReport:
    collected_by_house: dict[str, int]
    fresh_odds: int
    dropped_started: int
    opportunities: list[StakedTennisOpportunity]
    # Candidatas frescas de todas as casas, preservadas para recalculo barato
    # por usuario (ex.: casa vetada) sem nova coleta.
    fresh_odds_list: tuple[TennisMarketOdd, ...] = ()


def _collect_betano(betano_fetcher: BetanoTennisFetcher) -> list[TennisMarketOdd]:
    try:
        events = betano_fetcher()
    except Exception as exc:  # a queda de uma casa nao derruba o ciclo
        logger.warning("tennis_live.betano_failed", error=str(exc))
        return []
    odds: list[TennisMarketOdd] = []
    for event in events:
        odds.extend(parse_betano_tennis_event(event))
    return odds


def _collect_superbet(
    betano_player_sets: set[frozenset[str]],
    window_start: datetime,
    window_end: datetime,
    superbet_list_fetcher: SuperbetTennisListFetcher,
    superbet_detail_fetcher: SuperbetTennisDetailFetcher,
) -> list[TennisMarketOdd]:
    try:
        list_payload = superbet_list_fetcher(window_start, window_end)
        paired_ids = pair_tennis_to_betano_universe(
            discover_superbet_tennis_events(list_payload), betano_player_sets
        )
    except Exception as exc:
        logger.warning("tennis_live.superbet_list_failed", error=str(exc))
        return []
    odds: list[TennisMarketOdd] = []
    for event_id in paired_ids:
        try:
            detail = superbet_detail_fetcher(event_id)
        except Exception as exc:  # um evento ruim nao derruba a casa
            logger.warning("tennis_live.superbet_detail_failed", event_id=event_id, error=str(exc))
            continue
        if detail is not None:
            odds.extend(parse_superbet_tennis_event(detail))
    return odds


def run_tennis_live_cycle(
    config: TennisLiveConfig,
    *,
    betano_fetcher: BetanoTennisFetcher,
    superbet_list_fetcher: SuperbetTennisListFetcher,
    superbet_detail_fetcher: SuperbetTennisDetailFetcher,
    now: datetime,
) -> TennisLiveCycleReport:
    betano_odds = _collect_betano(betano_fetcher)
    superbet_odds: list[TennisMarketOdd] = []
    if betano_odds:
        betano_player_sets = {tennis_players_key(o) for o in betano_odds}
        starts = [o.start_time for o in betano_odds]
        superbet_odds = _collect_superbet(
            betano_player_sets,
            min(starts) - WINDOW_PADDING,
            max(starts) + WINDOW_PADDING,
            superbet_list_fetcher,
            superbet_detail_fetcher,
        )

    all_odds = [*betano_odds, *superbet_odds]
    # Guarda anti arb-fantasma pre-jogo: evento iniciado sai do ciclo.
    fresh = [o for o in all_odds if not o.is_live and o.start_time > now]
    opportunities = detect_tennis_arbs(fresh, min_profit_pct=config.min_profit_pct)
    staked = [
        StakedTennisOpportunity(
            opportunity=opp,
            stakes=calculate_stakes(
                {"leg_a": opp.leg_a.odd, "leg_b": opp.leg_b.odd}, config.bankroll
            ),
        )
        for opp in opportunities
    ]
    return TennisLiveCycleReport(
        collected_by_house={
            "betano": len(betano_odds),
            "superbet": len(superbet_odds),
        },
        fresh_odds=len(fresh),
        dropped_started=len(all_odds) - len(fresh),
        opportunities=staked,
        fresh_odds_list=tuple(fresh),
    )


def _leg_label(leg: TennisMarketOdd) -> str:
    if leg.winner_player is not None:
        return leg.winner_player
    side = leg.side.value if leg.side is not None else "?"
    return f"{side} {leg.line}"


def format_tennis_cycle_report(report: TennisLiveCycleReport) -> str:
    lines = [
        f"[tenis] betano={report.collected_by_house.get('betano', 0)} "
        f"superbet={report.collected_by_house.get('superbet', 0)} "
        f"frescas={report.fresh_odds} descartadas_iniciadas={report.dropped_started} "
        f"oportunidades={len(report.opportunities)}"
    ]
    for staked in report.opportunities:
        opp = staked.opportunity
        match_label = f"{opp.leg_a.player_a} x {opp.leg_a.player_b}"
        stakes = " ".join(f"{side}=R${value}" for side, value in sorted(staked.stakes.items()))
        lines.append("")
        lines.append(
            f"  * {match_label} | {opp.market.value} "
            f"lucro={opp.profit_pct:.2f}% "
            f"[{_leg_label(opp.leg_a)} {opp.leg_a.bookmaker} {opp.leg_a.odd} | "
            f"{_leg_label(opp.leg_b)} {opp.leg_b.bookmaker} {opp.leg_b.odd}] {stakes}"
        )
    return "\n".join(lines)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def run_tennis_live_loop(
    config: TennisLiveConfig,
    *,
    betano_fetcher: BetanoTennisFetcher,
    superbet_list_fetcher: SuperbetTennisListFetcher,
    superbet_detail_fetcher: SuperbetTennisDetailFetcher,
    printer: Callable[[str], None] = print,
    now_fn: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
) -> list[TennisLiveCycleReport]:
    reports: list[TennisLiveCycleReport] = []
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            report = run_tennis_live_cycle(
                config,
                betano_fetcher=betano_fetcher,
                superbet_list_fetcher=superbet_list_fetcher,
                superbet_detail_fetcher=superbet_detail_fetcher,
                now=now_fn(),
            )
            printer(format_tennis_cycle_report(report))
            reports.append(report)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # um ciclo ruim nao para o loop
            logger.warning("tennis_live.cycle_failed", error=str(exc))
        if max_iterations is not None and iteration >= max_iterations:
            break
        await sleep(config.interval_seconds)
    return reports
