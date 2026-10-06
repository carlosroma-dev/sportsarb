# src/odds_arb/deep_live.py
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

from odds_arb.collectors.deep.altenar_deep import parse_estrelabet_deep_event
from odds_arb.collectors.deep.altenar_live import (
    discover_altenar_events,
    pair_altenar_to_betano_universe,
)
from odds_arb.collectors.deep.betano_deep import parse_betano_deep_event
from odds_arb.collectors.deep.competitions import DeepCompetition
from odds_arb.collectors.deep.kto_deep import parse_kto_deep_event
from odds_arb.collectors.deep.kto_live import (
    discover_kto_events,
    pair_kto_to_betano_universe,
)
from odds_arb.collectors.deep.novibet_deep import parse_novibet_deep_event
from odds_arb.collectors.deep.novibet_live import (
    NOVIBET_DETAIL_ALIASES,
    discover_novibet_events,
)
from odds_arb.collectors.deep.sportingbet_deep import parse_sportingbet_deep_event
from odds_arb.collectors.deep.sportingbet_live import (
    discover_sportingbet_events,
    pair_sportingbet_to_betano_universe,
)
from odds_arb.collectors.deep.superbet_deep import parse_superbet_deep_event
from odds_arb.collectors.deep.superbet_live import (
    discover_superbet_events,
    pair_to_betano_universe,
)
from odds_arb.core.dedup import canonical_team_name
from odds_arb.core.deep_markets import (
    DeepArbOpportunity,
    DeepMarketOdd,
    detect_deep_market_arbs,
)
from odds_arb.core.stake import calculate_stakes
from odds_arb.deep_scanner import append_opportunity_audit

logger = structlog.get_logger(__name__)

BetanoFetcher = Callable[[DeepCompetition], list[Mapping[str, Any]]]
SuperbetListFetcher = Callable[[datetime, datetime], Mapping[str, Any]]
SuperbetDetailFetcher = Callable[[str], Mapping[str, Any] | None]
SportingbetListFetcher = Callable[[], Mapping[str, Any]]
SportingbetDetailFetcher = Callable[[str], Mapping[str, Any] | None]
KtoListFetcher = Callable[[], Mapping[str, Any]]
KtoDetailFetcher = Callable[[str], Mapping[str, Any] | None]
EstrelabetListFetcher = Callable[[], Mapping[str, Any]]
EstrelabetDetailFetcher = Callable[[str], Mapping[str, Any] | None]
NovibetListFetcher = Callable[[], object]
NovibetDetailFetcher = Callable[[str, str], Mapping[str, Any] | None]

# Lucro a partir do qual a linha sai em CAIXA ALTA emoldurada, pra destacar.
HIGHLIGHT_PROFIT_PCT = Decimal("4.5")

# Métricas cujo settlement depende do provedor (definição varia entre casas);
# a linha sai com aviso para conferência manual antes de confiar.
SETTLEMENT_SENSITIVE = frozenset({"tackles", "throw_ins"})


@dataclass(frozen=True)
class LiveDeepConfig:
    competition: DeepCompetition
    min_profit_pct: Decimal
    bankroll: Decimal
    interval_seconds: float
    audit_path: Path


@dataclass(frozen=True)
class StakedOpportunity:
    opportunity: DeepArbOpportunity
    stakes: dict[str, Decimal]


@dataclass(frozen=True)
class LiveDeepCycleReport:
    collected_by_house: dict[str, int]
    fresh_odds: int
    dropped_started: int
    opportunities: list[StakedOpportunity]
    # Candidatas frescas de todas as casas (nao so o par vencedor), preservadas
    # para permitir recalculo barato por usuario (ex.: casa vetada) sem nova coleta.
    fresh_odds_list: tuple[DeepMarketOdd, ...] = ()


def _accepted_odds(results: list[Any]) -> list[DeepMarketOdd]:
    return [r.odd for r in results if r.accepted and r.odd is not None]


def _collect_betano(
    config: LiveDeepConfig,
    betano_fetcher: BetanoFetcher,
) -> list[DeepMarketOdd]:
    try:
        events = betano_fetcher(config.competition)
    except Exception as exc:  # a house outage must not crash the cycle
        logger.warning("deep_live.betano_failed", error=str(exc))
        return []
    odds: list[DeepMarketOdd] = []
    for event in events:
        odds.extend(_accepted_odds(parse_betano_deep_event(event)))
    return odds


def _collect_superbet(
    betano_team_sets: set[frozenset[str]],
    window_start: datetime,
    window_end: datetime,
    superbet_list_fetcher: SuperbetListFetcher,
    superbet_detail_fetcher: SuperbetDetailFetcher,
) -> list[DeepMarketOdd]:
    try:
        list_payload = superbet_list_fetcher(window_start, window_end)
        paired_ids = pair_to_betano_universe(
            discover_superbet_events(list_payload), betano_team_sets
        )
    except Exception as exc:
        logger.warning("deep_live.superbet_list_failed", error=str(exc))
        return []
    odds: list[DeepMarketOdd] = []
    for event_id in paired_ids:
        try:
            detail = superbet_detail_fetcher(event_id)
        except Exception as exc:  # one bad event must not kill the house
            logger.warning("deep_live.superbet_detail_failed", event_id=event_id, error=str(exc))
            continue
        if detail is not None:
            odds.extend(_accepted_odds(parse_superbet_deep_event(detail)))
    return odds


def _collect_sportingbet(
    betano_team_sets: set[frozenset[str]],
    sportingbet_list_fetcher: SportingbetListFetcher,
    sportingbet_detail_fetcher: SportingbetDetailFetcher,
) -> list[DeepMarketOdd]:
    try:
        list_payload = sportingbet_list_fetcher()
        paired_ids = pair_sportingbet_to_betano_universe(
            discover_sportingbet_events(list_payload), betano_team_sets
        )
    except Exception as exc:
        logger.warning("deep_live.sportingbet_list_failed", error=str(exc))
        return []
    odds: list[DeepMarketOdd] = []
    for fixture_id in paired_ids:
        try:
            detail = sportingbet_detail_fetcher(fixture_id)
        except Exception as exc:  # one bad event must not kill the house
            logger.warning(
                "deep_live.sportingbet_detail_failed",
                fixture_id=fixture_id,
                error=str(exc),
            )
            continue
        if detail is not None:
            odds.extend(_accepted_odds(parse_sportingbet_deep_event(detail)))
    return odds


def _collect_kto(
    betano_team_sets: set[frozenset[str]],
    kto_list_fetcher: KtoListFetcher,
    kto_detail_fetcher: KtoDetailFetcher,
) -> list[DeepMarketOdd]:
    try:
        list_payload = kto_list_fetcher()
        paired_ids = pair_kto_to_betano_universe(
            discover_kto_events(list_payload), betano_team_sets
        )
    except Exception as exc:
        logger.warning("deep_live.kto_list_failed", error=str(exc))
        return []
    odds: list[DeepMarketOdd] = []
    for event_id in paired_ids:
        try:
            detail = kto_detail_fetcher(event_id)
        except Exception as exc:  # one bad event must not kill the house
            logger.warning("deep_live.kto_detail_failed", event_id=event_id, error=str(exc))
            continue
        if detail is not None:
            odds.extend(_accepted_odds(parse_kto_deep_event(detail)))
    return odds


def _collect_estrelabet(
    betano_team_sets: set[frozenset[str]],
    estrelabet_list_fetcher: EstrelabetListFetcher,
    estrelabet_detail_fetcher: EstrelabetDetailFetcher,
) -> list[DeepMarketOdd]:
    try:
        list_payload = estrelabet_list_fetcher()
        paired_ids = pair_altenar_to_betano_universe(
            discover_altenar_events(list_payload), betano_team_sets
        )
    except Exception as exc:
        logger.warning("deep_live.estrelabet_list_failed", error=str(exc))
        return []
    odds: list[DeepMarketOdd] = []
    for event_id in paired_ids:
        try:
            detail = estrelabet_detail_fetcher(event_id)
        except Exception as exc:  # one bad event must not kill the house
            logger.warning("deep_live.estrelabet_detail_failed", event_id=event_id, error=str(exc))
            continue
        if detail is not None:
            odds.extend(_accepted_odds(parse_estrelabet_deep_event(detail)))
    return odds


def _collect_novibet(
    betano_team_sets: set[frozenset[str]],
    novibet_list_fetcher: NovibetListFetcher,
    novibet_detail_fetcher: NovibetDetailFetcher | None = None,
) -> list[DeepMarketOdd]:
    try:
        list_payload = novibet_list_fetcher()
        paired_list_events = [
            event
            for event in discover_novibet_events(list_payload)
            if frozenset(
                {
                    canonical_team_name(event.home_team),
                    canonical_team_name(event.away_team),
                }
            )
            in betano_team_sets
        ]
    except Exception as exc:
        logger.warning("deep_live.novibet_list_failed", error=str(exc))
        return []
    odds: list[DeepMarketOdd] = []
    for event in paired_list_events:
        parsed_event_odds: list[DeepMarketOdd] = []
        if novibet_detail_fetcher is not None:
            seen_selections: set[tuple[str, str]] = set()
            for alias in NOVIBET_DETAIL_ALIASES:
                try:
                    detail = novibet_detail_fetcher(event.event_id, alias)
                except Exception as exc:  # one bad event must not kill the house
                    logger.warning(
                        "deep_live.novibet_detail_failed",
                        event_id=event.event_id,
                        alias=alias,
                        error=str(exc),
                    )
                    continue
                if detail is None:
                    continue
                for odd in _accepted_odds(parse_novibet_deep_event(detail)):
                    key = (odd.raw_market_id, odd.raw_selection_id)
                    if key in seen_selections:
                        continue
                    seen_selections.add(key)
                    parsed_event_odds.append(odd)
        if parsed_event_odds:
            odds.extend(parsed_event_odds)
        else:
            odds.extend(_accepted_odds(parse_novibet_deep_event(event.payload)))
    return odds


def run_live_deep_cycle(
    config: LiveDeepConfig,
    *,
    betano_fetcher: BetanoFetcher,
    superbet_list_fetcher: SuperbetListFetcher,
    superbet_detail_fetcher: SuperbetDetailFetcher,
    sportingbet_list_fetcher: SportingbetListFetcher | None = None,
    sportingbet_detail_fetcher: SportingbetDetailFetcher | None = None,
    kto_list_fetcher: KtoListFetcher | None = None,
    kto_detail_fetcher: KtoDetailFetcher | None = None,
    estrelabet_list_fetcher: EstrelabetListFetcher | None = None,
    estrelabet_detail_fetcher: EstrelabetDetailFetcher | None = None,
    novibet_list_fetcher: NovibetListFetcher | None = None,
    novibet_detail_fetcher: NovibetDetailFetcher | None = None,
    now: datetime,
) -> LiveDeepCycleReport:
    betano_odds = _collect_betano(config, betano_fetcher)
    superbet_odds: list[DeepMarketOdd] = []
    sportingbet_odds: list[DeepMarketOdd] = []
    kto_odds: list[DeepMarketOdd] = []
    estrelabet_odds: list[DeepMarketOdd] = []
    novibet_odds: list[DeepMarketOdd] = []
    if betano_odds:
        betano_team_sets = {
            frozenset({canonical_team_name(o.home_team), canonical_team_name(o.away_team)})
            for o in betano_odds
        }
        starts = [o.start_time for o in betano_odds]
        superbet_odds = _collect_superbet(
            betano_team_sets,
            min(starts),
            max(starts),
            superbet_list_fetcher,
            superbet_detail_fetcher,
        )
        if sportingbet_list_fetcher is not None and sportingbet_detail_fetcher is not None:
            sportingbet_odds = _collect_sportingbet(
                betano_team_sets,
                sportingbet_list_fetcher,
                sportingbet_detail_fetcher,
            )
        if kto_list_fetcher is not None and kto_detail_fetcher is not None:
            kto_odds = _collect_kto(
                betano_team_sets,
                kto_list_fetcher,
                kto_detail_fetcher,
            )
        if estrelabet_list_fetcher is not None and estrelabet_detail_fetcher is not None:
            estrelabet_odds = _collect_estrelabet(
                betano_team_sets,
                estrelabet_list_fetcher,
                estrelabet_detail_fetcher,
            )
        if novibet_list_fetcher is not None:
            novibet_odds = _collect_novibet(
                betano_team_sets,
                novibet_list_fetcher,
                novibet_detail_fetcher,
            )

    all_odds = [
        *betano_odds,
        *superbet_odds,
        *sportingbet_odds,
        *kto_odds,
        *estrelabet_odds,
        *novibet_odds,
    ]
    # Pre-game phantom-arb guard. `is_live` is reserved for a future source-level
    # live flag; today both deep parsers hardcode it False, so freshness rests
    # entirely on `start_time > now` (a started/in-play event is dropped). Keep
    # both conditions so a real `is_live` signal slots in without touching this.
    fresh = [o for o in all_odds if not o.is_live and o.start_time > now]
    opportunities = detect_deep_market_arbs(fresh, min_profit_pct=config.min_profit_pct)
    staked = [
        StakedOpportunity(
            opportunity=opp,
            stakes=calculate_stakes(
                {"over": opp.over_leg.odd, "under": opp.under_leg.odd}, config.bankroll
            ),
        )
        for opp in opportunities
    ]
    append_opportunity_audit(config.audit_path, opportunities)
    return LiveDeepCycleReport(
        collected_by_house={
            "betano": len(betano_odds),
            "superbet": len(superbet_odds),
            "sportingbet": len(sportingbet_odds),
            "kto": len(kto_odds),
            "estrelabet": len(estrelabet_odds),
            "novibet": len(novibet_odds),
        },
        fresh_odds=len(fresh),
        dropped_started=len(all_odds) - len(fresh),
        opportunities=staked,
        fresh_odds_list=tuple(fresh),
    )


def format_cycle_report(report: LiveDeepCycleReport, competition: DeepCompetition) -> str:
    lines = [
        f"[{competition.label}] betano={report.collected_by_house.get('betano', 0)} "
        f"superbet={report.collected_by_house.get('superbet', 0)} "
        f"sportingbet={report.collected_by_house.get('sportingbet', 0)} "
        f"kto={report.collected_by_house.get('kto', 0)} "
        f"estrelabet={report.collected_by_house.get('estrelabet', 0)} "
        f"novibet={report.collected_by_house.get('novibet', 0)} "
        f"frescas={report.fresh_odds} descartadas_iniciadas={report.dropped_started} "
        f"oportunidades={len(report.opportunities)}"
    ]
    for staked in report.opportunities:
        opp = staked.opportunity
        _match, period, family, metric, subject, line = opp.key
        subject_label = subject or "partida"
        match_label = f"{opp.over_leg.home_team} x {opp.over_leg.away_team}"
        stakes = " ".join(f"{side}=R${value}" for side, value in sorted(staked.stakes.items()))
        body = (
            f"{match_label} | {metric}/{family} {subject_label} {period} linha={line} "
            f"lucro={opp.profit_pct:.2f}% "
            f"[over {opp.over_leg.bookmaker} {opp.over_leg.odd} | "
            f"under {opp.under_leg.bookmaker} {opp.under_leg.odd}] {stakes}"
        )
        if metric in SETTLEMENT_SENSITIVE:
            body = f"{body}  ⚠ conferir settlement"
        lines.append("")  # blank line separating each opportunity, for readability
        if opp.profit_pct >= HIGHLIGHT_PROFIT_PCT:
            lines.append(f"  *** {body.upper()} ***")
        else:
            lines.append(f"  * {body}")
    return "\n".join(lines)


def _utc_now() -> datetime:
    return datetime.now(UTC)


async def run_live_deep_loop(
    config: LiveDeepConfig,
    *,
    betano_fetcher: BetanoFetcher,
    superbet_list_fetcher: SuperbetListFetcher,
    superbet_detail_fetcher: SuperbetDetailFetcher,
    sportingbet_list_fetcher: SportingbetListFetcher | None = None,
    sportingbet_detail_fetcher: SportingbetDetailFetcher | None = None,
    kto_list_fetcher: KtoListFetcher | None = None,
    kto_detail_fetcher: KtoDetailFetcher | None = None,
    estrelabet_list_fetcher: EstrelabetListFetcher | None = None,
    estrelabet_detail_fetcher: EstrelabetDetailFetcher | None = None,
    novibet_list_fetcher: NovibetListFetcher | None = None,
    novibet_detail_fetcher: NovibetDetailFetcher | None = None,
    printer: Callable[[str], None] = print,
    now_fn: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    max_iterations: int | None = None,
    on_cycle: Callable[[LiveDeepCycleReport], None] | None = None,
) -> list[LiveDeepCycleReport]:
    reports: list[LiveDeepCycleReport] = []
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        try:
            report = run_live_deep_cycle(
                config,
                betano_fetcher=betano_fetcher,
                superbet_list_fetcher=superbet_list_fetcher,
                superbet_detail_fetcher=superbet_detail_fetcher,
                sportingbet_list_fetcher=sportingbet_list_fetcher,
                sportingbet_detail_fetcher=sportingbet_detail_fetcher,
                kto_list_fetcher=kto_list_fetcher,
                kto_detail_fetcher=kto_detail_fetcher,
                estrelabet_list_fetcher=estrelabet_list_fetcher,
                estrelabet_detail_fetcher=estrelabet_detail_fetcher,
                novibet_list_fetcher=novibet_list_fetcher,
                novibet_detail_fetcher=novibet_detail_fetcher,
                now=now_fn(),
            )
            printer(format_cycle_report(report, config.competition))
            reports.append(report)
            if on_cycle is not None:
                try:
                    on_cycle(report)
                except Exception as exc:  # a bad callback must not stop the loop
                    logger.warning("deep_live.on_cycle_failed", error=str(exc))
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception as exc:  # one bad cycle must not stop the loop
            logger.warning("deep_live.cycle_failed", error=str(exc))
        if max_iterations is not None and iteration >= max_iterations:
            break
        await sleep(config.interval_seconds)
    return reports
