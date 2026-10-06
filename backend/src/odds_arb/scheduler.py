from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import structlog

from odds_arb.collectors.base import AdapterCollector, Collector, RawEvent
from odds_arb.collectors.registry import REGISTRY
from odds_arb.core.arbitrage import find_arbitrage_opportunities
from odds_arb.core.dedup import cluster_match_ids, init_team_aliases
from odds_arb.core.models import ArbitrageOpportunity, Odd
from odds_arb.notifier import OpportunityMessageContext, TelegramNotifier
from odds_arb.reporting import DEFAULT_OPPORTUNITY_LOG_PATH, report_opportunities
from odds_arb.store import (
    DEFAULT_DB_PATH,
    DEFAULT_MISSING_GRACE_SCANS,
    CollectorSnapshot,
    load_collector_states,
    save_collector_states,
    save_scan,
    start_scan,
)

DEFAULT_BANKROLL = Decimal("1000")
DEFAULT_MIN_ARB_PCT = Decimal("1.5")
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 3
DEFAULT_CIRCUIT_BREAKER_PAUSE = timedelta(minutes=5)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ScanResult:
    events: list[RawEvent]
    odds: list[Odd]
    opportunities: list[ArbitrageOpportunity]
    raw_matches: int
    unified_matches: int
    started_at: datetime
    finished_at: datetime
    scan_id: int | None = None


@dataclass(frozen=True)
class CollectorBatch:
    events: list[RawEvent]
    snapshot: CollectorSnapshot


@dataclass
class CollectorState:
    failures: int = 0
    paused_until: datetime | None = None

    def is_paused(self, now: datetime) -> bool:
        return self.paused_until is not None and self.paused_until > now


class Scanner:
    def __init__(
        self,
        collectors: Sequence[Collector] | None = None,
        *,
        db_path: Path = DEFAULT_DB_PATH,
        bankroll: Decimal = DEFAULT_BANKROLL,
        min_profit_pct: Decimal = DEFAULT_MIN_ARB_PCT,
        opportunity_log_path: Path = DEFAULT_OPPORTUNITY_LOG_PATH,
        missing_grace_scans: int = DEFAULT_MISSING_GRACE_SCANS,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self.collectors = list(collectors or default_collectors())
        self.db_path = db_path
        self.bankroll = bankroll
        self.min_profit_pct = min_profit_pct
        self.opportunity_log_path = opportunity_log_path
        self.missing_grace_scans = missing_grace_scans
        self.notifier = notifier
        self._states = {collector.name: CollectorState() for collector in self.collectors}
        # Carrega aliases aprovados uma unica vez por processo (cache em memoria no dedup).
        init_team_aliases(self.db_path)

    async def scan_once(self) -> ScanResult:
        started_at = datetime.now(UTC)
        scan_id = await start_scan(db_path=self.db_path, started_at=started_at)
        await self._load_states()
        collector_batches = await asyncio.gather(
            *(self._fetch_with_circuit_breaker(collector) for collector in self.collectors)
        )
        await self._persist_states()
        events = _unify_match_ids([event for batch in collector_batches for event in batch.events])
        events, discarded_odds = _discard_invalid_odds(events)
        if discarded_odds:
            logger.warning("scan.invalid_odds_discarded", discarded_odds=discarded_odds)
        odds = [odd for event in events for odd in event.odds()]
        opportunities = find_arbitrage_opportunities(
            odds,
            bankroll=self.bankroll,
            min_profit_pct=self.min_profit_pct,
        )
        finished_at = datetime.now(UTC)
        await save_scan(
            events,
            opportunities,
            db_path=self.db_path,
            scan_id=scan_id,
            started_at=started_at,
            ended_at=finished_at,
            collector_snapshots=[
                _snapshot_with_filtered_counts(batch.snapshot, events)
                for batch in collector_batches
            ],
            missing_grace_scans=self.missing_grace_scans,
        )
        if self.notifier is not None:
            try:
                await self.notifier.send_opportunities(
                    opportunities,
                    contexts=_notification_contexts(events),
                )
            except Exception as exc:  # Defensive guard: notifications cannot stop scans.
                logger.error(
                    "telegram.cycle.failed",
                    error_type=type(exc).__name__,
                )
        report_opportunities(
            opportunities,
            match_names={
                event.match.match_id: (event.match.home_team, event.match.away_team)
                for event in events
            },
            bankroll=self.bankroll,
            log_path=self.opportunity_log_path,
        )
        result = ScanResult(
            events=events,
            odds=odds,
            opportunities=opportunities,
            raw_matches=len(events),
            unified_matches=len({event.match.match_id for event in events}),
            started_at=started_at,
            finished_at=finished_at,
            scan_id=scan_id,
        )
        logger.info(
            "scan.completed",
            events=len(result.events),
            odds=len(result.odds),
            opportunities=len(result.opportunities),
            raw_matches=result.raw_matches,
            unified_matches=result.unified_matches,
            latency_ms=int((finished_at - started_at).total_seconds() * 1000),
        )
        return result

    async def notify_started(self) -> bool:
        if self.notifier is None:
            return False
        try:
            return await self.notifier.send_startup_message(len(self.collectors))
        except Exception as exc:  # Defensive guard for injected notifier implementations.
            logger.error(
                "telegram.startup.failed",
                error_type=type(exc).__name__,
            )
            return False

    async def _fetch_with_circuit_breaker(self, collector: Collector) -> CollectorBatch:
        state = self._states[collector.name]
        now = datetime.now(UTC)
        is_complete_snapshot = bool(getattr(collector, "is_complete_snapshot", True))
        if state.is_paused(now):
            logger.warning(
                "collector.paused",
                collector=collector.name,
                paused_until=state.paused_until.isoformat() if state.paused_until else None,
            )
            return CollectorBatch(
                events=[],
                snapshot=CollectorSnapshot(
                    bookmaker=collector.name,
                    status="failure",
                    is_complete_snapshot=is_complete_snapshot,
                    error="circuit_breaker_open",
                ),
            )

        try:
            events = list(await collector.fetch())
        except Exception as exc:  # Defensive guard for third-party collector implementations.
            state.failures += 1
            if state.failures >= DEFAULT_CIRCUIT_BREAKER_THRESHOLD:
                state.paused_until = now + DEFAULT_CIRCUIT_BREAKER_PAUSE
            logger.warning(
                "collector.unhandled_error",
                collector=collector.name,
                failures=state.failures,
                error=str(exc),
            )
            return CollectorBatch(
                events=[],
                snapshot=CollectorSnapshot(
                    bookmaker=collector.name,
                    status="failure",
                    is_complete_snapshot=is_complete_snapshot,
                    error=str(exc),
                ),
            )

        if collector.last_error:
            state.failures += 1
            if state.failures >= DEFAULT_CIRCUIT_BREAKER_THRESHOLD:
                state.paused_until = now + DEFAULT_CIRCUIT_BREAKER_PAUSE
            logger.warning(
                "collector.reported_error",
                collector=collector.name,
                failures=state.failures,
                paused_until=state.paused_until.isoformat() if state.paused_until else None,
                error=collector.last_error,
            )
            return CollectorBatch(
                events=[],
                snapshot=CollectorSnapshot(
                    bookmaker=collector.name,
                    status="failure",
                    is_complete_snapshot=is_complete_snapshot,
                    error=collector.last_error,
                ),
            )

        state.failures = 0
        state.paused_until = None
        return CollectorBatch(
            events=events,
            snapshot=CollectorSnapshot(
                bookmaker=collector.name,
                status="ok" if is_complete_snapshot else "partial",
                is_complete_snapshot=is_complete_snapshot,
                event_count=len(events),
                odd_count=sum(len(event.odds()) for event in events),
            ),
        )

    async def _load_states(self) -> None:
        persisted = await load_collector_states(db_path=self.db_path)
        self._states = {
            collector.name: CollectorState(*persisted[collector.name])
            if collector.name in persisted
            else CollectorState()
            for collector in self.collectors
        }

    async def _persist_states(self) -> None:
        await save_collector_states(
            {name: (state.failures, state.paused_until) for name, state in self._states.items()},
            db_path=self.db_path,
        )


def _snapshot_with_filtered_counts(
    snapshot: CollectorSnapshot,
    events: Sequence[RawEvent],
) -> CollectorSnapshot:
    bookmaker_events = [event for event in events if event.bookmaker == snapshot.bookmaker]
    return CollectorSnapshot(
        bookmaker=snapshot.bookmaker,
        status=snapshot.status,
        is_complete_snapshot=snapshot.is_complete_snapshot,
        error=snapshot.error,
        event_count=len(bookmaker_events),
        odd_count=sum(len(event.odds()) for event in bookmaker_events),
    )


def _unify_match_ids(events: list[RawEvent]) -> list[RawEvent]:
    """Reassign a shared canonical match_id to events that the fuzzy matcher considers
    the same game, so odds from different bookmakers agrupam no mesmo match na engine."""
    mapping = cluster_match_ids([event.match for event in events])
    unified: list[RawEvent] = []
    for event in events:
        canonical = mapping.get(event.match.match_id, event.match.match_id)
        if canonical == event.match.match_id:
            unified.append(event)
            continue
        new_match = event.match.model_copy(update={"match_id": canonical})
        new_markets = [
            market.model_copy(
                update={
                    "selections": [
                        odd.model_copy(update={"match_id": canonical}) for odd in market.selections
                    ]
                }
            )
            for market in event.markets
        ]
        unified.append(event.model_copy(update={"match": new_match, "markets": new_markets}))
    return unified


def _discard_invalid_odds(events: list[RawEvent]) -> tuple[list[RawEvent], int]:
    filtered_events: list[RawEvent] = []
    discarded = 0
    for event in events:
        filtered_markets = []
        for market in event.markets:
            valid_selections = [
                odd
                for odd in market.selections
                if odd.price.is_finite() and Decimal("1.01") <= odd.price <= Decimal("50")
            ]
            discarded += len(market.selections) - len(valid_selections)
            if valid_selections:
                filtered_markets.append(market.model_copy(update={"selections": valid_selections}))
        if filtered_markets:
            filtered_events.append(event.model_copy(update={"markets": filtered_markets}))
    return filtered_events, discarded


def _notification_contexts(
    events: Sequence[RawEvent],
) -> dict[str, OpportunityMessageContext]:
    contexts: dict[str, OpportunityMessageContext] = {}
    for event in events:
        candidate = OpportunityMessageContext(
            home_team=event.match.home_team,
            away_team=event.match.away_team,
            starts_at=event.match.starts_at,
            league=event.match.league,
        )
        current = contexts.get(event.match.match_id)
        if current is None or (current.league is None and candidate.league is not None):
            contexts[event.match.match_id] = candidate
    return contexts


def default_collectors() -> list[Collector]:
    return [AdapterCollector(adapter) for adapter in REGISTRY.values()]


async def run_daemon(
    scanner: Scanner,
    *,
    interval_seconds: float,
    iterations: int | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    """Run the scan loop in a single long-lived process.

    The persistent process keeps circuit-breaker state in memory across cycles and
    write-throughs it to SQLite, so the breaker actually accumulates failures (unlike
    the previous one-shot-per-cron-tick model). Pass ``iterations`` to bound the loop
    in tests; ``None`` runs forever until interrupted.
    """
    completed = 0
    await scanner.notify_started()
    while iterations is None or completed < iterations:
        await scanner.scan_once()
        completed += 1
        if iterations is not None and completed >= iterations:
            break
        await sleep(interval_seconds)
    return completed


async def run_scan_once(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    collectors: Sequence[Collector] | None = None,
    bankroll: Decimal = DEFAULT_BANKROLL,
    min_profit_pct: Decimal = DEFAULT_MIN_ARB_PCT,
    opportunity_log_path: Path = DEFAULT_OPPORTUNITY_LOG_PATH,
    missing_grace_scans: int = DEFAULT_MISSING_GRACE_SCANS,
    notifier: TelegramNotifier | None = None,
) -> ScanResult:
    scanner = Scanner(
        collectors,
        db_path=db_path,
        bankroll=bankroll,
        min_profit_pct=min_profit_pct,
        opportunity_log_path=opportunity_log_path,
        missing_grace_scans=missing_grace_scans,
        notifier=notifier,
    )
    return await scanner.scan_once()
