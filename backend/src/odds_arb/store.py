from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, TypedDict

import aiosqlite

from odds_arb.collectors.base import RawEvent
from odds_arb.core.arbitrage import MIN_ODD, REQUIRED_OUTCOMES, _best_odds_by_outcome
from odds_arb.core.models import ArbitrageOpportunity, MarketKey, Odd

DEFAULT_DB_PATH = Path("odds_arb.db")
RETENTION_HOURS = 24
ARB_HALL_OF_FAME_LIMIT = 10
DEFAULT_MISSING_GRACE_SCANS = 2
DEFAULT_ODD_FRESHNESS = timedelta(minutes=10)
DEFAULT_ARB_LEG_MAX_SKEW = timedelta(minutes=5)
LIFECYCLE_BOOTSTRAP_KEY = "quote_lifecycle_v1"

CollectorScanStatus = Literal["ok", "failure", "partial"]
QuoteLifecycleState = Literal["active", "grace", "inactive", "indeterminate"]
OpportunityCloseReason = Literal[
    "price_changed",
    "selection_missing",
    "collector_unhealthy",
    "event_removed",
]


@dataclass(frozen=True)
class CollectorSnapshot:
    bookmaker: str
    status: CollectorScanStatus
    is_complete_snapshot: bool = True
    error: str | None = None
    event_count: int = 0
    odd_count: int = 0


class OpportunityRow(TypedDict):
    id: int
    fingerprint: str
    match_id: str
    market_key: str
    implied_probability_sum: str
    profit_pct: str
    best_odds_json: str
    stakes_json: str
    detected_at: str
    first_seen_at: str
    last_seen_at: str
    last_seen_scan_id: int | None
    active: bool
    close_reason: str | None
    closed_at: str | None


class OddRow(TypedDict):
    id: int
    match_id: str
    bookmaker: str
    market_key: str
    outcome_key: str
    price: str
    captured_at: str
    raw_label: str | None


class CurrentOddRow(TypedDict):
    match_id: str
    bookmaker: str
    market_key: str
    outcome_key: str
    price: str
    captured_at: str
    event_id: str | None
    market_id: str | None
    selection_id: str | None
    first_seen_at: str
    last_seen_at: str
    last_seen_scan_id: int | None
    active: bool
    state: str
    consecutive_misses: int


class QaSummaryRow(TypedDict):
    total_odds: int
    total_matches: int
    total_opportunities: int
    latest_odd_at: str | None
    bookmakers: list[str]
    markets: list[str]


class QaRecentMatchRow(TypedDict):
    match_id: str
    home_team: str
    away_team: str
    starts_at: str
    latest_odd_at: str | None
    markets: list[str]


class QaBookmakerRow(TypedDict):
    bookmaker: str
    odds_count: int
    match_count: int
    markets: list[str]
    latest_odd_at: str | None
    recent_matches: list[QaRecentMatchRow]


class QaSelectionOddRow(TypedDict):
    outcome_key: str
    price: str


class QaMarketOddsRow(TypedDict):
    market_key: str
    selections: list[QaSelectionOddRow]


class QaMatchBookmakerRow(TypedDict):
    bookmaker: str
    odds_count: int
    markets: list[str]
    missing_markets: list[str]
    market_odds: list[QaMarketOddsRow]


class QaMatchRow(TypedDict):
    match_id: str
    home_team: str
    away_team: str
    starts_at: str
    latest_odd_at: str | None
    bookmaker_count: int
    status: str
    bookmakers: list[QaMatchBookmakerRow]


class QaTopOddRow(TypedDict):
    outcome_key: str
    bookmaker: str
    price: str


class QaTopCandidateRow(TypedDict):
    match_id: str
    home_team: str
    away_team: str
    starts_at: str
    market_key: str
    implied_probability_sum: str
    status_label: str
    is_arbitrage: bool
    best_odds: list[QaTopOddRow]


class ArbHallOfFameRow(TypedDict):
    rank: int
    match_id: str
    home_team: str
    away_team: str
    league: str | None
    starts_at: str
    market_key: str
    profit_pct: str
    implied_probability_sum: str
    best_odds: list[QaTopOddRow]
    detected_at: str


async def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    async with aiosqlite.connect(db_path) as db:
        # WAL: leitor (serve) e escritor (scan/daemon) nao se bloqueiam; persiste no arquivo.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                sport TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                league TEXT,
                raw_event_id TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS odds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT NOT NULL,
                bookmaker TEXT NOT NULL,
                market_key TEXT NOT NULL,
                outcome_key TEXT NOT NULL,
                price TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                event_id TEXT,
                market_id TEXT,
                selection_id TEXT,
                raw_label TEXT,
                scan_id INTEGER,
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
            );

            CREATE TABLE IF NOT EXISTS scans (
                scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'running'
            );

            CREATE TABLE IF NOT EXISTS scan_collectors (
                scan_id INTEGER NOT NULL,
                bookmaker TEXT NOT NULL,
                status TEXT NOT NULL,
                is_complete_snapshot INTEGER NOT NULL DEFAULT 1,
                error TEXT,
                event_count INTEGER NOT NULL DEFAULT 0,
                odd_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scan_id, bookmaker),
                FOREIGN KEY (scan_id) REFERENCES scans(scan_id)
            );

            CREATE TABLE IF NOT EXISTS current_odds (
                match_id TEXT NOT NULL,
                bookmaker TEXT NOT NULL,
                market_key TEXT NOT NULL,
                outcome_key TEXT NOT NULL,
                history_odd_id INTEGER,
                price TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                event_id TEXT,
                market_id TEXT,
                selection_id TEXT,
                raw_label TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_seen_scan_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                state TEXT NOT NULL DEFAULT 'active',
                consecutive_misses INTEGER NOT NULL DEFAULT 0,
                tombstoned_at TEXT,
                PRIMARY KEY (match_id, bookmaker, market_key, outcome_key),
                FOREIGN KEY (match_id) REFERENCES matches(match_id),
                FOREIGN KEY (history_odd_id) REFERENCES odds(id),
                FOREIGN KEY (last_seen_scan_id) REFERENCES scans(scan_id)
            );

            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                match_id TEXT NOT NULL,
                market_key TEXT NOT NULL,
                implied_probability_sum TEXT NOT NULL,
                profit_pct TEXT NOT NULL,
                best_odds_json TEXT NOT NULL,
                stakes_json TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                first_seen_at TEXT,
                last_seen_at TEXT,
                last_seen_scan_id INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                close_reason TEXT,
                closed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS arb_hall_of_fame (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedup_key TEXT NOT NULL UNIQUE,
                composition_key TEXT NOT NULL,
                match_id TEXT NOT NULL,
                market_key TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                league TEXT,
                starts_at TEXT NOT NULL,
                implied_probability_sum TEXT NOT NULL,
                profit_pct TEXT NOT NULL,
                best_odds_json TEXT NOT NULL,
                home_bookmaker TEXT,
                home_odd TEXT,
                draw_bookmaker TEXT,
                draw_odd TEXT,
                away_bookmaker TEXT,
                away_odd TEXT,
                detected_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_arb_hall_of_fame_margin
            ON arb_hall_of_fame (CAST(profit_pct AS REAL) DESC, detected_at DESC);

            CREATE TABLE IF NOT EXISTS collector_state (
                name TEXT PRIMARY KEY,
                failures INTEGER NOT NULL,
                paused_until TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_dedup (
                fingerprint TEXT PRIMARY KEY,
                last_margin_pct TEXT,
                last_reported_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lifecycle_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS team_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_name TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'ai',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                approved INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                UNIQUE(raw_name)
            );
            """
        )
        await _ensure_column(db, "odds", "scan_id", "INTEGER")
        await _ensure_column(db, "opportunities", "first_seen_at", "TEXT")
        await _ensure_column(db, "opportunities", "last_seen_at", "TEXT")
        await _ensure_column(db, "opportunities", "last_seen_scan_id", "INTEGER")
        await _ensure_column(db, "opportunities", "active", "INTEGER NOT NULL DEFAULT 1")
        await _ensure_column(db, "opportunities", "close_reason", "TEXT")
        await _ensure_column(db, "opportunities", "closed_at", "TEXT")
        await _ensure_column(db, "alert_dedup", "last_margin_pct", "TEXT")
        await db.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_odds_recent
            ON odds (captured_at, match_id, market_key);

            CREATE INDEX IF NOT EXISTS idx_odds_latest_by_match
            ON odds (match_id, bookmaker, market_key, outcome_key, id);

            CREATE INDEX IF NOT EXISTS idx_odds_latest_by_market
            ON odds (market_key, match_id, bookmaker, outcome_key, id);

            CREATE INDEX IF NOT EXISTS idx_odds_scan
            ON odds (scan_id, bookmaker, match_id, market_key, outcome_key);

            CREATE INDEX IF NOT EXISTS idx_current_odds_live
            ON current_odds (active, state, last_seen_at, match_id, market_key);

            CREATE INDEX IF NOT EXISTS idx_current_odds_bookmaker_scan
            ON current_odds (bookmaker, last_seen_scan_id, state);

            CREATE INDEX IF NOT EXISTS idx_scan_collectors_status
            ON scan_collectors (scan_id, status, is_complete_snapshot);

            CREATE INDEX IF NOT EXISTS idx_opportunities_recent
            ON opportunities (active, last_seen_at, profit_pct);

            CREATE INDEX IF NOT EXISTS idx_opportunities_live
            ON opportunities (active, last_seen_at, market_key, profit_pct);
            """
        )
        await _bootstrap_lifecycle(db)
        await db.execute("PRAGMA user_version=1")
        await db.commit()


async def start_scan(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    started_at: datetime | None = None,
) -> int:
    await init_db(db_path)
    timestamp = (started_at or datetime.now(UTC)).isoformat()
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO scans (started_at, status) VALUES (?, 'running')",
            (timestamp,),
        )
        await db.commit()
        scan_id = cursor.lastrowid
    if scan_id is None:
        msg = "SQLite did not return a scan_id"
        raise RuntimeError(msg)
    return int(scan_id)


async def save_scan(
    events: Sequence[RawEvent],
    opportunities: Sequence[ArbitrageOpportunity],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    retention_hours: int = RETENTION_HOURS,
    scan_id: int | None = None,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
    collector_snapshots: Sequence[CollectorSnapshot] | None = None,
    missing_grace_scans: int = DEFAULT_MISSING_GRACE_SCANS,
) -> int:
    if missing_grace_scans < 1:
        msg = "missing_grace_scans must be at least 1"
        raise ValueError(msg)
    await init_db(db_path)
    effective_started_at = started_at or datetime.now(UTC)
    effective_ended_at = ended_at or datetime.now(UTC)
    if scan_id is None:
        scan_id = await start_scan(db_path=db_path, started_at=effective_started_at)
    snapshots = list(collector_snapshots or _infer_collector_snapshots(events))
    cutoff = effective_ended_at - timedelta(hours=retention_hours)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await _save_scan_collectors(db, scan_id, snapshots)
        await _save_events(db, events, scan_id=scan_id)
        await _upsert_current_odds_from_scan(
            db,
            scan_id=scan_id,
            seen_at=effective_ended_at,
        )
        await _apply_collector_absences(
            db,
            scan_id=scan_id,
            snapshots=snapshots,
            missing_grace_scans=missing_grace_scans,
            changed_at=effective_ended_at,
        )
        await _seed_arb_hall_of_fame(db)
        await _save_opportunities(
            db,
            opportunities,
            scan_id=scan_id,
            seen_at=effective_ended_at,
        )
        await _close_missing_opportunities(
            db,
            opportunities,
            snapshots=snapshots,
            scan_id=scan_id,
            closed_at=effective_ended_at,
        )
        await _save_arb_hall_of_fame(db, events, opportunities)
        await _prune(db, cutoff)
        await db.execute(
            """
            UPDATE scans
            SET ended_at = ?, status = 'completed'
            WHERE scan_id = ?
            """,
            (effective_ended_at.isoformat(), scan_id),
        )
        await db.commit()
    return scan_id


async def get_recent_opportunities(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    market: str | None = None,
    min_arb_pct: Decimal = Decimal("0"),
    limit: int = 100,
    freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    now: datetime | None = None,
) -> list[OpportunityRow]:
    await init_db(db_path)
    cutoff = ((now or datetime.now(UTC)) - freshness).isoformat()
    query = """
        SELECT id, fingerprint, match_id, market_key, implied_probability_sum, profit_pct,
               best_odds_json, stakes_json, detected_at, first_seen_at, last_seen_at,
               last_seen_scan_id, active, close_reason, closed_at
        FROM opportunities
        WHERE active = 1
          AND last_seen_at >= ?
          AND CAST(profit_pct AS REAL) >= ?
    """
    params: list[object] = [cutoff, float(min_arb_pct)]
    if market:
        query += " AND market_key = ?"
        params.append(market)
    query += " ORDER BY CAST(profit_pct AS REAL) DESC, detected_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(query, params)
    return [_opportunity_row(row) for row in rows]


async def get_recent_odds(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 500,
    freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    now: datetime | None = None,
) -> list[OddRow]:
    await init_db(db_path)
    cutoff = ((now or datetime.now(UTC)) - freshness).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT
                COALESCE(history_odd_id, 0) AS id,
                match_id,
                bookmaker,
                market_key,
                outcome_key,
                price,
                captured_at,
                raw_label
            FROM current_odds
            WHERE active = 1
              AND state = 'active'
              AND last_seen_at >= ?
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
    return [_odd_row(row) for row in rows]


async def get_current_odds(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    bookmaker: str | None = None,
    match_id: str | None = None,
) -> list[CurrentOddRow]:
    await init_db(db_path)
    conditions: list[str] = []
    params: list[object] = []
    if bookmaker is not None:
        conditions.append("bookmaker = ?")
        params.append(bookmaker)
    if match_id is not None:
        conditions.append("match_id = ?")
        params.append(match_id)
    where_clause = _where_clause(conditions)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""
            SELECT
                match_id,
                bookmaker,
                market_key,
                outcome_key,
                price,
                captured_at,
                event_id,
                market_id,
                selection_id,
                first_seen_at,
                last_seen_at,
                last_seen_scan_id,
                active,
                state,
                consecutive_misses
            FROM current_odds
            {where_clause}
            ORDER BY match_id, bookmaker, market_key, outcome_key
            """,
            params,
        )
    return [_current_odd_row(row) for row in rows]


async def get_qa_summary(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    now: datetime | None = None,
) -> QaSummaryRow:
    await init_db(db_path)
    cutoff = ((now or datetime.now(UTC)) - freshness).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = list(
            await db.execute_fetchall(
                """
                SELECT
                    (SELECT COUNT(*) FROM odds) AS total_odds,
                    (SELECT COUNT(*) FROM matches) AS total_matches,
                    (
                        SELECT COUNT(*)
                        FROM opportunities
                        WHERE active = 1 AND last_seen_at >= ?
                    ) AS total_opportunities,
                    (
                        SELECT MAX(last_seen_at)
                        FROM current_odds
                        WHERE active = 1 AND state = 'active' AND last_seen_at >= ?
                    ) AS latest_odd_at
                """,
                (cutoff, cutoff),
            )
        )
        bookmaker_rows = await db.execute_fetchall(
            """
            SELECT DISTINCT bookmaker
            FROM current_odds
            WHERE active = 1 AND state = 'active' AND last_seen_at >= ?
            ORDER BY bookmaker
            """,
            (cutoff,),
        )
        market_rows = await db.execute_fetchall(
            """
            SELECT DISTINCT market_key
            FROM current_odds
            WHERE active = 1 AND state = 'active' AND last_seen_at >= ?
            ORDER BY market_key
            """,
            (cutoff,),
        )

    row = rows[0] if rows else None
    return {
        "total_odds": int(row["total_odds"]) if row is not None else 0,
        "total_matches": int(row["total_matches"]) if row is not None else 0,
        "total_opportunities": int(row["total_opportunities"]) if row is not None else 0,
        "latest_odd_at": None
        if row is None or row["latest_odd_at"] is None
        else str(row["latest_odd_at"]),
        "bookmakers": [str(item["bookmaker"]) for item in bookmaker_rows],
        "markets": [str(item["market_key"]) for item in market_rows],
    }


async def get_qa_bookmakers(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    bookmaker: str | None = None,
    market: str | None = None,
    q: str | None = None,
    freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    now: datetime | None = None,
) -> list[QaBookmakerRow]:
    await init_db(db_path)
    conditions, params = _qa_filter_conditions(bookmaker=bookmaker, market=market, q=q)
    conditions = [
        "o.active = 1",
        "o.state = 'active'",
        "o.last_seen_at >= ?",
        *conditions,
    ]
    params = [((now or datetime.now(UTC)) - freshness).isoformat(), *params]
    where_clause = _where_clause(conditions)

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""
            SELECT
                o.bookmaker,
                COUNT(*) AS odds_count,
                COUNT(DISTINCT o.match_id) AS match_count,
                GROUP_CONCAT(DISTINCT o.market_key) AS markets,
                MAX(o.last_seen_at) AS latest_odd_at
            FROM current_odds o
            JOIN matches m ON m.match_id = o.match_id
            {where_clause}
            GROUP BY o.bookmaker
            ORDER BY o.bookmaker
            """,
            params,
        )

        bookmaker_rows: list[QaBookmakerRow] = []
        for row in rows:
            current_bookmaker = str(row["bookmaker"])
            example_conditions = [*conditions, "o.bookmaker = ?"]
            example_params = [*params, current_bookmaker]
            example_rows = await db.execute_fetchall(
                f"""
                SELECT
                    o.match_id,
                    m.home_team,
                    m.away_team,
                    m.starts_at,
                    MAX(o.last_seen_at) AS latest_odd_at,
                    GROUP_CONCAT(DISTINCT o.market_key) AS markets
                FROM current_odds o
                JOIN matches m ON m.match_id = o.match_id
                {_where_clause(example_conditions)}
                GROUP BY o.match_id, m.home_team, m.away_team, m.starts_at
                ORDER BY latest_odd_at DESC, m.starts_at ASC
                LIMIT 5
                """,
                example_params,
            )
            bookmaker_rows.append(
                {
                    "bookmaker": current_bookmaker,
                    "odds_count": int(row["odds_count"]),
                    "match_count": int(row["match_count"]),
                    "markets": _csv_values(row["markets"]),
                    "latest_odd_at": (
                        None if row["latest_odd_at"] is None else str(row["latest_odd_at"])
                    ),
                    "recent_matches": [_qa_recent_match_row(example) for example in example_rows],
                }
            )

    return bookmaker_rows


async def get_qa_matches(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    bookmaker: str | None = None,
    market: str | None = None,
    q: str | None = None,
    only_multi: bool = False,
    limit: int = 50,
    freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    now: datetime | None = None,
) -> list[QaMatchRow]:
    await init_db(db_path)
    bounded_limit = max(1, min(limit, 500))
    conditions, params = _qa_filter_conditions(bookmaker=bookmaker, market=market, q=q)
    conditions = [
        "o.active = 1",
        "o.state = 'active'",
        "o.last_seen_at >= ?",
        *conditions,
    ]
    params = [((now or datetime.now(UTC)) - freshness).isoformat(), *params]
    having_clause = "HAVING COUNT(DISTINCT o.bookmaker) >= 2" if only_multi else ""

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        match_rows = await db.execute_fetchall(
            f"""
            SELECT
                o.match_id,
                MAX(o.last_seen_at) AS latest_odd_at,
                COUNT(DISTINCT o.bookmaker) AS bookmaker_count
            FROM current_odds o
            JOIN matches m ON m.match_id = o.match_id
            {_where_clause(conditions)}
            GROUP BY o.match_id
            {having_clause}
            ORDER BY latest_odd_at DESC, o.match_id
            LIMIT ?
            """,
            [*params, bounded_limit],
        )
        match_ids = [str(row["match_id"]) for row in match_rows]
        if not match_ids:
            return []

        placeholders = ", ".join("?" for _ in match_ids)
        detail_conditions = [f"o.match_id IN ({placeholders})", *conditions]
        detail_rows = list(
            await db.execute_fetchall(
                f"""
                SELECT
                    o.match_id,
                    m.home_team,
                    m.away_team,
                    m.starts_at,
                    o.bookmaker,
                    COUNT(*) AS odds_count,
                    GROUP_CONCAT(DISTINCT o.market_key) AS markets,
                    MAX(o.last_seen_at) AS latest_odd_at
                FROM current_odds o
                JOIN matches m ON m.match_id = o.match_id
                {_where_clause(detail_conditions)}
                GROUP BY o.match_id, m.home_team, m.away_team, m.starts_at, o.bookmaker
                ORDER BY o.match_id, o.bookmaker
                """,
                [*match_ids, *params],
            )
        )
        latest_odd_rows = list(
            await db.execute_fetchall(
                f"""
                SELECT
                    o.match_id,
                    o.bookmaker,
                    o.market_key,
                    o.outcome_key,
                    o.price
                FROM current_odds o
                JOIN matches m ON m.match_id = o.match_id
                {_where_clause(detail_conditions)}
                ORDER BY o.match_id, o.bookmaker, o.market_key, o.outcome_key
                """,
                [*match_ids, *params],
            )
        )

    return _qa_match_rows(match_ids, detail_rows, latest_odd_rows)


async def get_qa_top_candidates(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 3,
    freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    max_leg_skew: timedelta = DEFAULT_ARB_LEG_MAX_SKEW,
    now: datetime | None = None,
) -> list[QaTopCandidateRow]:
    await init_db(db_path)
    supported_markets = sorted(REQUIRED_OUTCOMES)
    placeholders = ", ".join("?" for _ in supported_markets)
    cutoff = ((now or datetime.now(UTC)) - freshness).isoformat()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            f"""
            SELECT
                o.match_id,
                m.home_team,
                m.away_team,
                m.starts_at,
                o.bookmaker,
                o.market_key,
                o.outcome_key,
                o.price,
                o.captured_at,
                o.last_seen_at,
                o.last_seen_scan_id
            FROM current_odds o
            JOIN matches m ON m.match_id = o.match_id
            WHERE o.market_key IN ({placeholders})
              AND o.active = 1
              AND o.state = 'active'
              AND o.last_seen_at >= ?
            ORDER BY o.match_id, o.market_key, o.bookmaker, o.outcome_key
            """,
            [*supported_markets, cutoff],
        )

    return _qa_top_candidate_rows(rows, limit=limit, max_leg_skew=max_leg_skew)


async def get_arb_hall_of_fame(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    limit: int = 3,
) -> list[ArbHallOfFameRow]:
    await seed_arb_hall_of_fame(db_path=db_path)
    bounded_limit = max(1, min(limit, ARB_HALL_OF_FAME_LIMIT))
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT
                match_id,
                home_team,
                away_team,
                league,
                starts_at,
                market_key,
                profit_pct,
                implied_probability_sum,
                best_odds_json,
                detected_at
            FROM arb_hall_of_fame
            ORDER BY CAST(profit_pct AS REAL) DESC, detected_at ASC
            LIMIT ?
            """,
            (bounded_limit,),
        )
    return [_arb_hall_of_fame_row(row, rank=index) for index, row in enumerate(rows, start=1)]


async def seed_arb_hall_of_fame(*, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create and seed the historical hall without modifying retained scan data."""
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await _seed_arb_hall_of_fame(db)
        await db.commit()


CollectorStateRow = tuple[int, datetime | None]
OpportunityAlertState = tuple[datetime, Decimal | None]


async def load_collector_states(*, db_path: Path = DEFAULT_DB_PATH) -> dict[str, CollectorStateRow]:
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall("SELECT name, failures, paused_until FROM collector_state")
    return {
        str(row["name"]): (
            int(row["failures"]),
            datetime.fromisoformat(row["paused_until"]) if row["paused_until"] else None,
        )
        for row in rows
    }


async def save_collector_states(
    states: Mapping[str, CollectorStateRow],
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> None:
    await init_db(db_path)
    now = datetime.now(UTC).isoformat()
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """
            INSERT INTO collector_state (name, failures, paused_until, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                failures = excluded.failures,
                paused_until = excluded.paused_until,
                updated_at = excluded.updated_at
            """,
            [
                (name, failures, paused_until.isoformat() if paused_until else None, now)
                for name, (failures, paused_until) in states.items()
            ],
        )
        await db.commit()


async def mark_opportunities_reported(
    fingerprints: Iterable[str],
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> None:
    timestamp = (now or datetime.now(UTC)).isoformat()
    rows = [(fingerprint, timestamp) for fingerprint in fingerprints]
    if not rows:
        return
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.executemany(
            """
            INSERT INTO alert_dedup (fingerprint, last_reported_at)
            VALUES (?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET last_reported_at = excluded.last_reported_at
            """,
            rows,
        )
        await db.commit()


async def get_opportunity_alert_state(
    fingerprint: str,
    *,
    db_path: Path = DEFAULT_DB_PATH,
) -> OpportunityAlertState | None:
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                """
                SELECT
                    COALESCE(o.first_seen_at, o.detected_at) AS first_seen_at,
                    a.last_margin_pct
                FROM opportunities o
                LEFT JOIN alert_dedup a ON a.fingerprint = o.fingerprint
                WHERE o.fingerprint = ?
                  AND o.active = 1
                """,
                (fingerprint,),
            )
        ).fetchone()
    if row is None:
        return None

    first_seen_at = datetime.fromisoformat(str(row["first_seen_at"]))
    raw_margin = row["last_margin_pct"]
    if raw_margin is None:
        return first_seen_at, None
    try:
        return first_seen_at, Decimal(str(raw_margin))
    except InvalidOperation:
        return first_seen_at, None


async def record_opportunity_alert(
    fingerprint: str,
    margin_pct: Decimal,
    *,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> None:
    timestamp = (now or datetime.now(UTC)).isoformat()
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO alert_dedup (fingerprint, last_margin_pct, last_reported_at)
            VALUES (?, ?, ?)
            ON CONFLICT(fingerprint) DO UPDATE SET
                last_margin_pct = excluded.last_margin_pct,
                last_reported_at = excluded.last_reported_at
            """,
            (fingerprint, str(margin_pct), timestamp),
        )
        await db.commit()


async def recently_reported_fingerprints(
    *,
    window: timedelta,
    db_path: Path = DEFAULT_DB_PATH,
    now: datetime | None = None,
) -> set[str]:
    cutoff = ((now or datetime.now(UTC)) - window).isoformat()
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            "SELECT fingerprint FROM alert_dedup WHERE last_reported_at >= ?",
            (cutoff,),
        )
    return {str(row["fingerprint"]) for row in rows}


async def _ensure_column(
    db: aiosqlite.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
    if any(str(row[1]) == column for row in rows):
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


async def _bootstrap_lifecycle(db: aiosqlite.Connection) -> None:
    marker_rows = await db.execute_fetchall(
        "SELECT value FROM lifecycle_meta WHERE key = ?",
        (LIFECYCLE_BOOTSTRAP_KEY,),
    )
    if marker_rows:
        return

    latest_rows = list(await db.execute_fetchall("SELECT MAX(captured_at) FROM odds"))
    latest_value = latest_rows[0][0] if latest_rows else None
    if latest_value is not None:
        latest_at = datetime.fromisoformat(str(latest_value))
        cutoff = (latest_at - DEFAULT_ODD_FRESHNESS).isoformat()
        await db.execute(
            """
            INSERT INTO current_odds (
                match_id,
                bookmaker,
                market_key,
                outcome_key,
                history_odd_id,
                price,
                captured_at,
                event_id,
                market_id,
                selection_id,
                raw_label,
                first_seen_at,
                last_seen_at,
                last_seen_scan_id,
                active,
                state,
                consecutive_misses,
                tombstoned_at
            )
            SELECT
                latest.match_id,
                latest.bookmaker,
                latest.market_key,
                latest.outcome_key,
                o.id,
                o.price,
                o.captured_at,
                o.event_id,
                o.market_id,
                o.selection_id,
                o.raw_label,
                latest.first_seen_at,
                o.captured_at,
                NULL,
                CASE WHEN o.captured_at >= ? THEN 1 ELSE 0 END,
                CASE WHEN o.captured_at >= ? THEN 'active' ELSE 'inactive' END,
                CASE WHEN o.captured_at >= ? THEN 0 ELSE ? END,
                CASE WHEN o.captured_at >= ? THEN NULL ELSE ? END
            FROM (
                SELECT
                    match_id,
                    bookmaker,
                    market_key,
                    outcome_key,
                    MAX(id) AS latest_id,
                    MIN(captured_at) AS first_seen_at
                FROM odds
                GROUP BY match_id, bookmaker, market_key, outcome_key
            ) AS latest
            JOIN odds o ON o.id = latest.latest_id
            """,
            (
                cutoff,
                cutoff,
                cutoff,
                DEFAULT_MISSING_GRACE_SCANS,
                cutoff,
                latest_at.isoformat(),
            ),
        )
        await _bootstrap_opportunity_lifecycle(
            db,
            cutoff=datetime.fromisoformat(cutoff),
            reference_at=latest_at,
        )

    await db.execute(
        """
        INSERT INTO lifecycle_meta (key, value)
        VALUES (?, ?)
        """,
        (LIFECYCLE_BOOTSTRAP_KEY, datetime.now(UTC).isoformat()),
    )


async def _bootstrap_opportunity_lifecycle(
    db: aiosqlite.Connection,
    *,
    cutoff: datetime,
    reference_at: datetime,
) -> None:
    db.row_factory = aiosqlite.Row
    rows = await db.execute_fetchall(
        """
        SELECT id, match_id, market_key, best_odds_json, detected_at
        FROM opportunities
        """
    )
    for row in rows:
        payload = _json_object_list(row["best_odds_json"])
        live_legs: list[aiosqlite.Row] = []
        reason: OpportunityCloseReason = "selection_missing"
        for leg in payload:
            bookmaker = leg.get("bookmaker")
            outcome_key = leg.get("outcome_key")
            price = leg.get("price")
            if bookmaker is None or outcome_key is None or price is None:
                live_legs = []
                break
            leg_rows = list(
                await db.execute_fetchall(
                    """
                SELECT price, last_seen_at, last_seen_scan_id, active, state
                FROM current_odds
                WHERE match_id = ?
                  AND bookmaker = ?
                  AND market_key = ?
                  AND outcome_key = ?
                """,
                    (
                        str(row["match_id"]),
                        bookmaker,
                        str(row["market_key"]),
                        outcome_key,
                    ),
                )
            )
            if not leg_rows:
                live_legs = []
                break
            current = leg_rows[0]
            if str(current["price"]) != price:
                reason = "price_changed"
                live_legs = []
                break
            if (
                not bool(current["active"])
                or str(current["state"]) != "active"
                or datetime.fromisoformat(str(current["last_seen_at"])) < cutoff
            ):
                live_legs = []
                break
            live_legs.append(current)

        if payload and len(live_legs) == len(payload) and _rows_are_scan_coherent(live_legs):
            last_seen_at = min(
                datetime.fromisoformat(str(leg["last_seen_at"])) for leg in live_legs
            )
            scan_ids = {
                int(leg["last_seen_scan_id"])
                for leg in live_legs
                if leg["last_seen_scan_id"] is not None
            }
            last_seen_scan_id = scan_ids.pop() if len(scan_ids) == 1 else None
            await db.execute(
                """
                UPDATE opportunities
                SET
                    first_seen_at = COALESCE(first_seen_at, detected_at),
                    last_seen_at = ?,
                    last_seen_scan_id = ?,
                    active = 1,
                    close_reason = NULL,
                    closed_at = NULL
                WHERE id = ?
                """,
                (last_seen_at.isoformat(), last_seen_scan_id, int(row["id"])),
            )
            continue

        await db.execute(
            """
            UPDATE opportunities
            SET
                first_seen_at = COALESCE(first_seen_at, detected_at),
                last_seen_at = COALESCE(last_seen_at, detected_at),
                active = 0,
                close_reason = ?,
                closed_at = ?
            WHERE id = ?
            """,
            (reason, reference_at.isoformat(), int(row["id"])),
        )


def _rows_are_scan_coherent(
    rows: Sequence[aiosqlite.Row],
    *,
    max_leg_skew: timedelta = DEFAULT_ARB_LEG_MAX_SKEW,
) -> bool:
    scan_ids = {
        int(row["last_seen_scan_id"]) for row in rows if row["last_seen_scan_id"] is not None
    }
    if len(scan_ids) == 1 and all(row["last_seen_scan_id"] is not None for row in rows):
        return True
    seen_at = [datetime.fromisoformat(str(row["last_seen_at"])) for row in rows]
    return not seen_at or max(seen_at) - min(seen_at) <= max_leg_skew


def _infer_collector_snapshots(events: Sequence[RawEvent]) -> list[CollectorSnapshot]:
    snapshots: list[CollectorSnapshot] = []
    for bookmaker in sorted({event.bookmaker for event in events}):
        bookmaker_events = [event for event in events if event.bookmaker == bookmaker]
        snapshots.append(
            CollectorSnapshot(
                bookmaker=bookmaker,
                status="ok",
                is_complete_snapshot=True,
                event_count=len(bookmaker_events),
                odd_count=sum(len(event.odds()) for event in bookmaker_events),
            )
        )
    return snapshots


async def _save_scan_collectors(
    db: aiosqlite.Connection,
    scan_id: int,
    snapshots: Sequence[CollectorSnapshot],
) -> None:
    await db.executemany(
        """
        INSERT INTO scan_collectors (
            scan_id,
            bookmaker,
            status,
            is_complete_snapshot,
            error,
            event_count,
            odd_count
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scan_id, bookmaker) DO UPDATE SET
            status = excluded.status,
            is_complete_snapshot = excluded.is_complete_snapshot,
            error = excluded.error,
            event_count = excluded.event_count,
            odd_count = excluded.odd_count
        """,
        [
            (
                scan_id,
                snapshot.bookmaker,
                snapshot.status,
                int(snapshot.is_complete_snapshot),
                snapshot.error,
                snapshot.event_count,
                snapshot.odd_count,
            )
            for snapshot in snapshots
        ],
    )


async def _save_events(
    db: aiosqlite.Connection,
    events: Sequence[RawEvent],
    *,
    scan_id: int,
) -> None:
    now = datetime.now(UTC).isoformat()
    for event in events:
        await db.execute(
            """
            INSERT INTO matches (
                match_id, sport, home_team, away_team, starts_at, league, raw_event_id, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(match_id) DO UPDATE SET
                sport = excluded.sport,
                home_team = excluded.home_team,
                away_team = excluded.away_team,
                starts_at = excluded.starts_at,
                league = excluded.league,
                raw_event_id = excluded.raw_event_id,
                updated_at = excluded.updated_at
            """,
            (
                event.match.match_id,
                event.match.sport,
                event.match.home_team,
                event.match.away_team,
                event.match.starts_at.isoformat(),
                event.match.league,
                event.match.raw_event_id,
                now,
            ),
        )
        await db.executemany(
            """
            INSERT INTO odds (
                match_id, bookmaker, market_key, outcome_key, price, captured_at,
                event_id, market_id, selection_id, raw_label, scan_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(*_odd_params(odd), scan_id) for odd in event.odds()],
        )


async def _upsert_current_odds_from_scan(
    db: aiosqlite.Connection,
    *,
    scan_id: int,
    seen_at: datetime,
) -> None:
    seen_at_iso = seen_at.isoformat()
    await db.execute(
        """
        INSERT INTO current_odds (
            match_id,
            bookmaker,
            market_key,
            outcome_key,
            history_odd_id,
            price,
            captured_at,
            event_id,
            market_id,
            selection_id,
            raw_label,
            first_seen_at,
            last_seen_at,
            last_seen_scan_id,
            active,
            state,
            consecutive_misses,
            tombstoned_at
        )
        SELECT
            ranked.match_id,
            ranked.bookmaker,
            ranked.market_key,
            ranked.outcome_key,
            ranked.id,
            ranked.price,
            ranked.captured_at,
            ranked.event_id,
            ranked.market_id,
            ranked.selection_id,
            ranked.raw_label,
            ranked.captured_at,
            ?,
            ?,
            1,
            'active',
            0,
            NULL
        FROM (
            SELECT
                o.*,
                ROW_NUMBER() OVER (
                    PARTITION BY match_id, bookmaker, market_key, outcome_key
                    ORDER BY CAST(price AS REAL) ASC, id DESC
                ) AS row_number
            FROM odds o
            WHERE scan_id = ?
        ) AS ranked
        WHERE ranked.row_number = 1
        ON CONFLICT(match_id, bookmaker, market_key, outcome_key) DO UPDATE SET
            history_odd_id = excluded.history_odd_id,
            price = excluded.price,
            captured_at = excluded.captured_at,
            event_id = excluded.event_id,
            market_id = excluded.market_id,
            selection_id = excluded.selection_id,
            raw_label = excluded.raw_label,
            last_seen_at = excluded.last_seen_at,
            last_seen_scan_id = excluded.last_seen_scan_id,
            active = 1,
            state = 'active',
            consecutive_misses = 0,
            tombstoned_at = NULL
        """,
        (seen_at_iso, scan_id, scan_id),
    )


async def _apply_collector_absences(
    db: aiosqlite.Connection,
    *,
    scan_id: int,
    snapshots: Sequence[CollectorSnapshot],
    missing_grace_scans: int,
    changed_at: datetime,
) -> None:
    changed_at_iso = changed_at.isoformat()
    for snapshot in snapshots:
        unseen_params = (snapshot.bookmaker, scan_id)
        if snapshot.status == "ok" and snapshot.is_complete_snapshot:
            await db.execute(
                """
                UPDATE current_odds
                SET
                    consecutive_misses = consecutive_misses + 1,
                    active = 0,
                    state = CASE
                        WHEN consecutive_misses + 1 >= ? THEN 'inactive'
                        ELSE 'grace'
                    END,
                    tombstoned_at = CASE
                        WHEN consecutive_misses + 1 >= ? THEN ?
                        ELSE NULL
                    END
                WHERE bookmaker = ?
                  AND (last_seen_scan_id IS NULL OR last_seen_scan_id != ?)
                """,
                (
                    missing_grace_scans,
                    missing_grace_scans,
                    changed_at_iso,
                    *unseen_params,
                ),
            )
            continue
        await db.execute(
            """
            UPDATE current_odds
            SET active = 0, state = 'indeterminate'
            WHERE bookmaker = ?
              AND (last_seen_scan_id IS NULL OR last_seen_scan_id != ?)
            """,
            unseen_params,
        )


async def _save_opportunities(
    db: aiosqlite.Connection,
    opportunities: Sequence[ArbitrageOpportunity],
    *,
    scan_id: int,
    seen_at: datetime,
) -> None:
    await db.executemany(
        """
        INSERT INTO opportunities (
            fingerprint, match_id, market_key, implied_probability_sum, profit_pct,
            best_odds_json, stakes_json, detected_at, first_seen_at, last_seen_at,
            last_seen_scan_id, active, close_reason, closed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, NULL)
        ON CONFLICT(fingerprint) DO UPDATE SET
            implied_probability_sum = excluded.implied_probability_sum,
            profit_pct = excluded.profit_pct,
            best_odds_json = excluded.best_odds_json,
            stakes_json = excluded.stakes_json,
            last_seen_at = excluded.last_seen_at,
            last_seen_scan_id = excluded.last_seen_scan_id,
            active = 1,
            close_reason = NULL,
            closed_at = NULL
        """,
        [
            (
                *_opportunity_params(opportunity),
                opportunity.detected_at.isoformat(),
                seen_at.isoformat(),
                scan_id,
            )
            for opportunity in opportunities
        ],
    )


async def _close_missing_opportunities(
    db: aiosqlite.Connection,
    opportunities: Sequence[ArbitrageOpportunity],
    *,
    snapshots: Sequence[CollectorSnapshot],
    scan_id: int,
    closed_at: datetime,
) -> None:
    seen_fingerprints = {opportunity_fingerprint(opportunity) for opportunity in opportunities}
    db.row_factory = aiosqlite.Row
    active_rows = await db.execute_fetchall(
        """
        SELECT fingerprint, match_id, market_key, best_odds_json
        FROM opportunities
        WHERE active = 1
        """
    )
    snapshot_by_bookmaker = {snapshot.bookmaker: snapshot for snapshot in snapshots}
    for row in active_rows:
        fingerprint = str(row["fingerprint"])
        if fingerprint in seen_fingerprints:
            continue
        close_reason = await _opportunity_close_reason(
            db,
            match_id=str(row["match_id"]),
            market_key=str(row["market_key"]),
            best_odds_payload=_json_object_list(row["best_odds_json"]),
            snapshot_by_bookmaker=snapshot_by_bookmaker,
            scan_id=scan_id,
        )
        if close_reason is None:
            continue
        await db.execute(
            """
            UPDATE opportunities
            SET active = 0, close_reason = ?, closed_at = ?
            WHERE fingerprint = ?
            """,
            (close_reason, closed_at.isoformat(), fingerprint),
        )


async def _opportunity_close_reason(
    db: aiosqlite.Connection,
    *,
    match_id: str,
    market_key: str,
    best_odds_payload: Sequence[Mapping[str, str | None]],
    snapshot_by_bookmaker: Mapping[str, CollectorSnapshot],
    scan_id: int,
) -> OpportunityCloseReason | None:
    bookmakers = {
        str(leg["bookmaker"]) for leg in best_odds_payload if leg.get("bookmaker") is not None
    }
    if not bookmakers:
        return "selection_missing"

    snapshots = [snapshot_by_bookmaker.get(bookmaker) for bookmaker in bookmakers]
    if any(snapshot is None for snapshot in snapshots):
        return None
    if any(
        snapshot is not None and (snapshot.status != "ok" or not snapshot.is_complete_snapshot)
        for snapshot in snapshots
    ):
        return "collector_unhealthy"

    selection_missing = False
    event_removed = False
    price_changed = False
    for leg in best_odds_payload:
        bookmaker = leg.get("bookmaker")
        outcome_key = leg.get("outcome_key")
        price = leg.get("price")
        if bookmaker is None or outcome_key is None or price is None:
            selection_missing = True
            continue
        current_rows = list(
            await db.execute_fetchall(
                """
            SELECT price, state, active, last_seen_scan_id
            FROM current_odds
            WHERE match_id = ?
              AND bookmaker = ?
              AND market_key = ?
              AND outcome_key = ?
            """,
                (match_id, bookmaker, market_key, outcome_key),
            )
        )
        if not current_rows:
            selection_missing = True
            continue
        current = current_rows[0]
        if (
            not bool(current["active"])
            or str(current["state"]) != "active"
            or current["last_seen_scan_id"] != scan_id
        ):
            same_event_rows = await db.execute_fetchall(
                """
                SELECT 1
                FROM current_odds
                WHERE match_id = ?
                  AND bookmaker = ?
                  AND last_seen_scan_id = ?
                LIMIT 1
                """,
                (match_id, bookmaker, scan_id),
            )
            if same_event_rows:
                selection_missing = True
            else:
                event_removed = True
            continue
        if str(current["price"]) != price:
            price_changed = True

    if event_removed:
        return "event_removed"
    if selection_missing:
        return "selection_missing"
    if price_changed:
        return "price_changed"
    return "price_changed"


async def _seed_arb_hall_of_fame(db: aiosqlite.Connection) -> None:
    count_rows = list(await db.execute_fetchall("SELECT COUNT(*) FROM arb_hall_of_fame"))
    if count_rows and int(count_rows[0][0]) > 0:
        return
    db.row_factory = aiosqlite.Row
    historical_rows = await db.execute_fetchall(
        """
        SELECT
            o.match_id,
            o.market_key,
            o.implied_probability_sum,
            o.profit_pct,
            o.best_odds_json,
            o.detected_at,
            m.home_team,
            m.away_team,
            m.league,
            m.starts_at
        FROM opportunities o
        JOIN matches m ON m.match_id = o.match_id
        ORDER BY CAST(o.profit_pct AS REAL) DESC, o.detected_at ASC
        """
    )
    seed_rows = [
        hall_row
        for row in historical_rows
        if (hall_row := _hall_params_from_stored_opportunity(row)) is not None
    ]
    await _upsert_arb_hall_rows(db, seed_rows)


async def _save_arb_hall_of_fame(
    db: aiosqlite.Connection,
    events: Sequence[RawEvent],
    opportunities: Sequence[ArbitrageOpportunity],
) -> None:
    metadata = _match_metadata(events)
    rows = [
        _hall_params_from_opportunity(opportunity, metadata[opportunity.match_id])
        for opportunity in opportunities
        if opportunity.match_id in metadata
    ]
    await _upsert_arb_hall_rows(db, rows)


async def _upsert_arb_hall_rows(
    db: aiosqlite.Connection,
    rows: Sequence[tuple[object, ...]],
) -> None:
    if rows:
        await db.executemany(
            """
            INSERT INTO arb_hall_of_fame (
                dedup_key,
                composition_key,
                match_id,
                market_key,
                home_team,
                away_team,
                league,
                starts_at,
                implied_probability_sum,
                profit_pct,
                best_odds_json,
                home_bookmaker,
                home_odd,
                draw_bookmaker,
                draw_odd,
                away_bookmaker,
                away_odd,
                detected_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dedup_key) DO UPDATE SET
                composition_key = excluded.composition_key,
                market_key = excluded.market_key,
                home_team = excluded.home_team,
                away_team = excluded.away_team,
                league = excluded.league,
                starts_at = excluded.starts_at,
                implied_probability_sum = excluded.implied_probability_sum,
                profit_pct = excluded.profit_pct,
                best_odds_json = excluded.best_odds_json,
                home_bookmaker = excluded.home_bookmaker,
                home_odd = excluded.home_odd,
                draw_bookmaker = excluded.draw_bookmaker,
                draw_odd = excluded.draw_odd,
                away_bookmaker = excluded.away_bookmaker,
                away_odd = excluded.away_odd,
                detected_at = excluded.detected_at,
                updated_at = excluded.updated_at
            WHERE CAST(excluded.profit_pct AS REAL) >
                  CAST(arb_hall_of_fame.profit_pct AS REAL)
            """,
            rows,
        )
    await db.execute(
        """
        DELETE FROM arb_hall_of_fame
        WHERE id NOT IN (
            SELECT id
            FROM arb_hall_of_fame
            ORDER BY CAST(profit_pct AS REAL) DESC, detected_at ASC
            LIMIT ?
        )
        """,
        (ARB_HALL_OF_FAME_LIMIT,),
    )


async def _prune(db: aiosqlite.Connection, cutoff: datetime) -> None:
    cutoff_iso = cutoff.isoformat()
    await db.execute("DELETE FROM odds WHERE captured_at < ?", (cutoff_iso,))
    await db.execute(
        """
        DELETE FROM opportunities
        WHERE active = 0
          AND COALESCE(last_seen_at, detected_at) < ?
        """,
        (cutoff_iso,),
    )
    await db.execute("DELETE FROM alert_dedup WHERE last_reported_at < ?", (cutoff_iso,))


def _odd_params(odd: Odd) -> tuple[object, ...]:
    return (
        odd.match_id,
        odd.bookmaker,
        odd.market_key,
        odd.outcome_key,
        str(odd.price),
        odd.captured_at.isoformat(),
        odd.event_id,
        odd.market_id,
        odd.selection_id,
        odd.raw_label,
    )


def _opportunity_params(opportunity: ArbitrageOpportunity) -> tuple[object, ...]:
    return (
        opportunity_fingerprint(opportunity),
        opportunity.match_id,
        opportunity.market_key,
        str(opportunity.implied_probability_sum),
        str(opportunity.profit_pct),
        json.dumps(_best_odds_payload(opportunity.best_odds.values()), ensure_ascii=False),
        json.dumps(
            {key: str(value) for key, value in opportunity.stakes.items()},
            ensure_ascii=False,
        ),
        opportunity.detected_at.isoformat(),
    )


MatchMetadata = tuple[str, str, str | None, str]


def _match_metadata(events: Sequence[RawEvent]) -> dict[str, MatchMetadata]:
    metadata: dict[str, MatchMetadata] = {}
    for event in events:
        current = metadata.get(event.match.match_id)
        candidate = (
            event.match.home_team,
            event.match.away_team,
            event.match.league,
            event.match.starts_at.isoformat(),
        )
        if current is None or (current[2] is None and candidate[2] is not None):
            metadata[event.match.match_id] = candidate
    return metadata


def _hall_params_from_opportunity(
    opportunity: ArbitrageOpportunity,
    metadata: MatchMetadata,
) -> tuple[object, ...]:
    home_team, away_team, league, starts_at = metadata
    best_odds_payload = _ordered_best_odds_payload(
        opportunity.best_odds,
        opportunity.market_key,
    )
    return _hall_params(
        match_id=opportunity.match_id,
        market_key=opportunity.market_key,
        home_team=home_team,
        away_team=away_team,
        league=league,
        starts_at=starts_at,
        implied_probability_sum=str(opportunity.implied_probability_sum),
        profit_pct=str(opportunity.profit_pct),
        best_odds_payload=best_odds_payload,
        detected_at=opportunity.detected_at.isoformat(),
    )


def _hall_params_from_stored_opportunity(row: aiosqlite.Row) -> tuple[object, ...] | None:
    raw_payload = _json_object_list(row["best_odds_json"])
    if not raw_payload:
        return None
    return _hall_params(
        match_id=str(row["match_id"]),
        market_key=str(row["market_key"]),
        home_team=str(row["home_team"]),
        away_team=str(row["away_team"]),
        league=None if row["league"] is None else str(row["league"]),
        starts_at=str(row["starts_at"]),
        implied_probability_sum=str(row["implied_probability_sum"]),
        profit_pct=str(row["profit_pct"]),
        best_odds_payload=raw_payload,
        detected_at=str(row["detected_at"]),
    )


def _hall_params(
    *,
    match_id: str,
    market_key: str,
    home_team: str,
    away_team: str,
    league: str | None,
    starts_at: str,
    implied_probability_sum: str,
    profit_pct: str,
    best_odds_payload: list[dict[str, str | None]],
    detected_at: str,
) -> tuple[object, ...]:
    odds_by_outcome = {
        str(item["outcome_key"]): item
        for item in best_odds_payload
        if item.get("outcome_key") is not None
    }
    composition_key = "|".join(
        f"{item.get('outcome_key')}:{item.get('bookmaker')}:{item.get('selection_id') or ''}"
        for item in sorted(best_odds_payload, key=lambda item: str(item.get("outcome_key")))
    )
    home = odds_by_outcome.get("home", {})
    draw = odds_by_outcome.get("draw", {})
    away = odds_by_outcome.get("away", {})
    updated_at = datetime.now(UTC).isoformat()
    return (
        match_id,
        composition_key,
        match_id,
        market_key,
        home_team,
        away_team,
        league,
        starts_at,
        implied_probability_sum,
        profit_pct,
        json.dumps(best_odds_payload, ensure_ascii=False),
        home.get("bookmaker"),
        home.get("price"),
        draw.get("bookmaker"),
        draw.get("price"),
        away.get("bookmaker"),
        away.get("price"),
        detected_at,
        updated_at,
    )


def opportunity_fingerprint(opportunity: ArbitrageOpportunity) -> str:
    odds_part = "|".join(
        f"{outcome}:{odd.bookmaker}" for outcome, odd in sorted(opportunity.best_odds.items())
    )
    return f"{opportunity.match_id}:{opportunity.market_key}:{odds_part}"


def _best_odds_payload(odds: Iterable[Odd]) -> list[dict[str, str | None]]:
    return [
        {
            "bookmaker": odd.bookmaker,
            "outcome_key": odd.outcome_key,
            "price": str(odd.price),
            "event_id": odd.event_id,
            "market_id": odd.market_id,
            "selection_id": odd.selection_id,
            "raw_label": odd.raw_label,
        }
        for odd in odds
    ]


def _ordered_best_odds_payload(
    best_odds: Mapping[str, Odd],
    market_key: MarketKey,
) -> list[dict[str, str | None]]:
    return _best_odds_payload(
        odd
        for _outcome, odd in sorted(
            best_odds.items(),
            key=lambda item: _outcome_sort_key(market_key, item[0]),
        )
    )


def _json_object_list(value: object) -> list[dict[str, str | None]]:
    try:
        payload = json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []
    items: list[dict[str, str | None]] = []
    for raw_item in payload:
        if not isinstance(raw_item, Mapping):
            continue
        items.append(
            {
                "bookmaker": _optional_text(raw_item.get("bookmaker")),
                "outcome_key": _optional_text(raw_item.get("outcome_key")),
                "price": _optional_text(raw_item.get("price")),
                "event_id": _optional_text(raw_item.get("event_id")),
                "market_id": _optional_text(raw_item.get("market_id")),
                "selection_id": _optional_text(raw_item.get("selection_id")),
                "raw_label": _optional_text(raw_item.get("raw_label")),
            }
        )
    return items


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _opportunity_row(row: aiosqlite.Row) -> OpportunityRow:
    first_seen_at = row["first_seen_at"] or row["detected_at"]
    last_seen_at = row["last_seen_at"] or row["detected_at"]
    return {
        "id": int(row["id"]),
        "fingerprint": str(row["fingerprint"]),
        "match_id": str(row["match_id"]),
        "market_key": str(row["market_key"]),
        "implied_probability_sum": str(row["implied_probability_sum"]),
        "profit_pct": str(row["profit_pct"]),
        "best_odds_json": str(row["best_odds_json"]),
        "stakes_json": str(row["stakes_json"]),
        "detected_at": str(row["detected_at"]),
        "first_seen_at": str(first_seen_at),
        "last_seen_at": str(last_seen_at),
        "last_seen_scan_id": (
            None if row["last_seen_scan_id"] is None else int(row["last_seen_scan_id"])
        ),
        "active": bool(row["active"]),
        "close_reason": None if row["close_reason"] is None else str(row["close_reason"]),
        "closed_at": None if row["closed_at"] is None else str(row["closed_at"]),
    }


def _arb_hall_of_fame_row(row: aiosqlite.Row, *, rank: int) -> ArbHallOfFameRow:
    market_key = str(row["market_key"])
    best_odds: list[QaTopOddRow] = [
        {
            "outcome_key": str(item["outcome_key"]),
            "bookmaker": str(item["bookmaker"]),
            "price": str(item["price"]),
        }
        for item in _json_object_list(row["best_odds_json"])
        if item.get("outcome_key") is not None
        and item.get("bookmaker") is not None
        and item.get("price") is not None
    ]
    best_odds.sort(key=lambda item: _outcome_sort_key(market_key, item["outcome_key"]))
    return {
        "rank": rank,
        "match_id": str(row["match_id"]),
        "home_team": str(row["home_team"]),
        "away_team": str(row["away_team"]),
        "league": None if row["league"] is None else str(row["league"]),
        "starts_at": str(row["starts_at"]),
        "market_key": market_key,
        "profit_pct": _safe_format_decimal(row["profit_pct"], Decimal("0.01")),
        "implied_probability_sum": _safe_format_decimal(
            row["implied_probability_sum"],
            Decimal("0.0001"),
        ),
        "best_odds": best_odds,
        "detected_at": str(row["detected_at"]),
    }


def _odd_row(row: aiosqlite.Row) -> OddRow:
    return {
        "id": int(row["id"]),
        "match_id": str(row["match_id"]),
        "bookmaker": str(row["bookmaker"]),
        "market_key": str(row["market_key"]),
        "outcome_key": str(row["outcome_key"]),
        "price": str(row["price"]),
        "captured_at": str(row["captured_at"]),
        "raw_label": row["raw_label"] if row["raw_label"] is None else str(row["raw_label"]),
    }


def _current_odd_row(row: aiosqlite.Row) -> CurrentOddRow:
    return {
        "match_id": str(row["match_id"]),
        "bookmaker": str(row["bookmaker"]),
        "market_key": str(row["market_key"]),
        "outcome_key": str(row["outcome_key"]),
        "price": str(row["price"]),
        "captured_at": str(row["captured_at"]),
        "event_id": None if row["event_id"] is None else str(row["event_id"]),
        "market_id": None if row["market_id"] is None else str(row["market_id"]),
        "selection_id": None if row["selection_id"] is None else str(row["selection_id"]),
        "first_seen_at": str(row["first_seen_at"]),
        "last_seen_at": str(row["last_seen_at"]),
        "last_seen_scan_id": (
            None if row["last_seen_scan_id"] is None else int(row["last_seen_scan_id"])
        ),
        "active": bool(row["active"]),
        "state": str(row["state"]),
        "consecutive_misses": int(row["consecutive_misses"]),
    }


def _qa_filter_conditions(
    *,
    bookmaker: str | None,
    market: str | None,
    q: str | None,
) -> tuple[list[str], list[object]]:
    conditions: list[str] = []
    params: list[object] = []
    if bookmaker:
        conditions.append("o.bookmaker = ?")
        params.append(bookmaker)
    if market:
        conditions.append("o.market_key = ?")
        params.append(market)
    if q:
        pattern = f"%{q.lower()}%"
        conditions.append(
            "("
            "LOWER(o.match_id) LIKE ? OR "
            "LOWER(m.home_team) LIKE ? OR "
            "LOWER(m.away_team) LIKE ? OR "
            "LOWER(COALESCE(m.league, '')) LIKE ?"
            ")"
        )
        params.extend([pattern, pattern, pattern, pattern])
    return conditions, params


def _where_clause(conditions: Sequence[str]) -> str:
    if not conditions:
        return ""
    return "WHERE " + " AND ".join(conditions)


def _csv_values(value: object) -> list[str]:
    if value is None:
        return []
    return sorted({item for item in str(value).split(",") if item})


MARKET_ORDER = ("1x2", "both_teams_score", "double_chance", "over_under_2_5")
OUTCOME_ORDER: dict[str, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "both_teams_score": ("yes", "no"),
    # canonical double_chance outcome keys are home_draw/home_away/draw_away
    "double_chance": ("home_draw", "home_away", "draw_away"),
    "over_under_2_5": ("over", "under"),
}


def _market_sort_key(market_key: str) -> tuple[int, str]:
    try:
        return (MARKET_ORDER.index(market_key), market_key)
    except ValueError:
        return (len(MARKET_ORDER), market_key)


def _outcome_sort_key(market_key: str, outcome_key: str) -> tuple[int, str]:
    order = OUTCOME_ORDER.get(market_key, ())
    try:
        return (order.index(outcome_key), outcome_key)
    except ValueError:
        return (len(order), outcome_key)


def _qa_recent_match_row(row: aiosqlite.Row) -> QaRecentMatchRow:
    return {
        "match_id": str(row["match_id"]),
        "home_team": str(row["home_team"]),
        "away_team": str(row["away_team"]),
        "starts_at": str(row["starts_at"]),
        "latest_odd_at": None if row["latest_odd_at"] is None else str(row["latest_odd_at"]),
        "markets": _csv_values(row["markets"]),
    }


def _qa_match_rows(
    match_ids: Sequence[str],
    rows: Sequence[aiosqlite.Row],
    latest_odd_rows: Sequence[aiosqlite.Row],
) -> list[QaMatchRow]:
    odds_by_market = _qa_market_odds_by_bookmaker(latest_odd_rows)
    grouped: dict[str, QaMatchRow] = {}
    for row in rows:
        match_id = str(row["match_id"])
        bookmaker = str(row["bookmaker"])
        current = grouped.setdefault(
            match_id,
            {
                "match_id": match_id,
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "starts_at": str(row["starts_at"]),
                "latest_odd_at": None
                if row["latest_odd_at"] is None
                else str(row["latest_odd_at"]),
                "bookmaker_count": 0,
                "status": "",
                "bookmakers": [],
            },
        )
        current["bookmakers"].append(
            {
                "bookmaker": bookmaker,
                "odds_count": int(row["odds_count"]),
                "markets": _csv_values(row["markets"]),
                "missing_markets": [],
                "market_odds": odds_by_market.get((match_id, bookmaker), []),
            }
        )

    ordered: list[QaMatchRow] = []
    for match_id in match_ids:
        match_row = grouped[match_id]
        market_union = sorted(
            {market for bookmaker in match_row["bookmakers"] for market in bookmaker["markets"]}
        )
        for bookmaker_row in match_row["bookmakers"]:
            bookmaker_row["missing_markets"] = [
                market for market in market_union if market not in bookmaker_row["markets"]
            ]
        bookmaker_count = len(match_row["bookmakers"])
        match_row["bookmaker_count"] = bookmaker_count
        if bookmaker_count == 1:
            only_bookmaker = match_row["bookmakers"][0]["bookmaker"]
            match_row["status"] = f"somente {only_bookmaker}"
        else:
            match_row["status"] = f"{bookmaker_count} casas"
        ordered.append(match_row)
    return ordered


def _qa_market_odds_by_bookmaker(
    rows: Sequence[aiosqlite.Row],
) -> dict[tuple[str, str], list[QaMarketOddsRow]]:
    grouped: dict[tuple[str, str, str], list[QaSelectionOddRow]] = {}
    for row in rows:
        match_id = str(row["match_id"])
        bookmaker = str(row["bookmaker"])
        market_key = str(row["market_key"])
        grouped.setdefault((match_id, bookmaker, market_key), []).append(
            {
                "outcome_key": str(row["outcome_key"]),
                "price": str(row["price"]),
            }
        )

    by_bookmaker: dict[tuple[str, str], list[QaMarketOddsRow]] = {}
    for (match_id, bookmaker, market_key), selections in grouped.items():
        ordered_selections = sorted(
            selections,
            key=lambda selection: _outcome_sort_key(market_key, selection["outcome_key"]),
        )
        by_bookmaker.setdefault((match_id, bookmaker), []).append(
            {
                "market_key": market_key,
                "selections": ordered_selections,
            }
        )

    for markets in by_bookmaker.values():
        markets.sort(key=lambda market: _market_sort_key(market["market_key"]))
    return by_bookmaker


def _qa_top_candidate_rows(
    rows: Iterable[aiosqlite.Row],
    *,
    limit: int,
    max_leg_skew: timedelta,
) -> list[QaTopCandidateRow]:
    grouped: dict[tuple[str, MarketKey], list[Odd]] = {}
    match_labels: dict[tuple[str, MarketKey], tuple[str, str, str]] = {}
    lifecycle: dict[tuple[str, MarketKey, str, str], tuple[datetime, int | None]] = {}
    for row in rows:
        market_key = _supported_market_key(str(row["market_key"]))
        if market_key is None:
            continue
        try:
            price = Decimal(str(row["price"]))
        except ValueError:
            continue
        if not price.is_finite() or price <= MIN_ODD:
            continue

        match_id = str(row["match_id"])
        key = (match_id, market_key)
        grouped.setdefault(key, []).append(
            Odd(
                match_id=match_id,
                market_key=market_key,
                outcome_key=str(row["outcome_key"]),
                price=price,
                bookmaker=str(row["bookmaker"]),
                captured_at=datetime.fromisoformat(str(row["captured_at"])),
            )
        )
        lifecycle[
            (
                match_id,
                market_key,
                str(row["bookmaker"]),
                str(row["outcome_key"]),
            )
        ] = (
            datetime.fromisoformat(str(row["last_seen_at"])),
            None if row["last_seen_scan_id"] is None else int(row["last_seen_scan_id"]),
        )
        match_labels[key] = (
            str(row["home_team"]),
            str(row["away_team"]),
            str(row["starts_at"]),
        )

    candidates: list[tuple[Decimal, QaTopCandidateRow]] = []
    for (match_id, market_key), odds in grouped.items():
        required_outcomes = REQUIRED_OUTCOMES[market_key]
        best_odds = _best_odds_by_outcome(odds, required_outcomes)
        if set(best_odds) != required_outcomes:
            continue
        if len({odd.bookmaker for odd in best_odds.values()}) < 2:
            continue
        leg_lifecycle = [
            lifecycle[(match_id, market_key, odd.bookmaker, odd.outcome_key)]
            for odd in best_odds.values()
        ]
        if not _candidate_legs_are_coherent(leg_lifecycle, max_leg_skew=max_leg_skew):
            continue

        implied_probability_sum = sum(
            (Decimal("1") / odd.price for odd in best_odds.values()),
            Decimal("0"),
        )
        home_team, away_team, starts_at = match_labels[(match_id, market_key)]
        candidates.append(
            (
                implied_probability_sum,
                {
                    "match_id": match_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "starts_at": starts_at,
                    "market_key": market_key,
                    "implied_probability_sum": _format_decimal(
                        implied_probability_sum,
                        Decimal("0.001"),
                    ),
                    "status_label": _top_status_label(implied_probability_sum),
                    "is_arbitrage": implied_probability_sum < Decimal("1"),
                    "best_odds": _qa_top_odd_rows(best_odds, market_key),
                },
            )
        )

    return [candidate for _score, candidate in sorted(candidates, key=lambda item: item[0])[:limit]]


def _candidate_legs_are_coherent(
    lifecycle: Sequence[tuple[datetime, int | None]],
    *,
    max_leg_skew: timedelta,
) -> bool:
    scan_ids = {scan_id for _seen_at, scan_id in lifecycle if scan_id is not None}
    if len(scan_ids) == 1 and all(scan_id is not None for _seen_at, scan_id in lifecycle):
        return True
    seen_at = [seen_at for seen_at, _scan_id in lifecycle]
    return not seen_at or max(seen_at) - min(seen_at) <= max_leg_skew


def _supported_market_key(value: str) -> MarketKey | None:
    if value not in REQUIRED_OUTCOMES:
        return None
    return value


def _qa_top_odd_rows(best_odds: Mapping[str, Odd], market_key: MarketKey) -> list[QaTopOddRow]:
    return [
        {
            "outcome_key": outcome_key,
            "bookmaker": odd.bookmaker,
            "price": str(odd.price),
        }
        for outcome_key, odd in sorted(
            best_odds.items(),
            key=lambda item: _outcome_sort_key(market_key, item[0]),
        )
    ]


def _top_status_label(implied_probability_sum: Decimal) -> str:
    if implied_probability_sum < Decimal("1"):
        profit_pct = ((Decimal("1") / implied_probability_sum) - Decimal("1")) * Decimal("100")
        return f"arb real: lucro {_format_decimal(profit_pct, Decimal('0.1'))}%"
    gap_pct = (implied_probability_sum - Decimal("1")) * Decimal("100")
    return f"falta {_format_decimal(gap_pct, Decimal('0.1'))}% pra fechar"


def _format_decimal(value: Decimal, quantum: Decimal) -> str:
    return str(value.quantize(quantum))


def _safe_format_decimal(value: object, quantum: Decimal) -> str:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    return _format_decimal(parsed, quantum)
