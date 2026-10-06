from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite

from odds_arb.collectors.base import RawEvent, RawMarket
from odds_arb.core.arbitrage import find_arbitrage_opportunities
from odds_arb.core.models import Match, Odd
from odds_arb.store import (
    CollectorSnapshot,
    get_current_odds,
    get_qa_top_candidates,
    get_recent_opportunities,
    save_scan,
)

MATCH_ID = "detroit-louisville"


def _match() -> Match:
    return Match(
        match_id=MATCH_ID,
        home_team="Detroit City",
        away_team="Louisville City FC",
        starts_at=datetime(2026, 6, 20, 21, 0, tzinfo=UTC),
        league="USL",
    )


def _event(
    bookmaker: str,
    prices: dict[str, str],
    *,
    captured_at: datetime,
) -> RawEvent:
    match = _match()
    return RawEvent(
        event_id=f"{bookmaker}:event",
        bookmaker=bookmaker,
        match=match,
        markets=[
            RawMarket(
                market_id=f"{bookmaker}:ou25",
                label="Over/Under 2.5",
                selections=[
                    Odd(
                        match_id=match.match_id,
                        market_key="over_under_2_5",
                        outcome_key=outcome_key,
                        price=Decimal(price),
                        bookmaker=bookmaker,
                        captured_at=captured_at,
                        event_id=f"{bookmaker}:event",
                        market_id=f"{bookmaker}:ou25",
                        selection_id=f"{bookmaker}:ou25:{outcome_key}",
                    )
                    for outcome_key, price in prices.items()
                ],
            )
        ],
    )


def _complete(bookmaker: str) -> CollectorSnapshot:
    return CollectorSnapshot(
        bookmaker=bookmaker,
        status="ok",
        is_complete_snapshot=True,
    )


async def test_complete_absence_removes_selection_from_live_set_and_closes_arb(
    tmp_path,
) -> None:
    db_path = tmp_path / "detroit.db"
    first_at = datetime(2026, 6, 18, 2, 14, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=2)
    initial_events = [
        _event("kto", {"over": "1.89", "under": "1.75"}, captured_at=first_at),
        _event("novibet", {"over": "1.65", "under": "2.25"}, captured_at=first_at),
    ]
    initial_opportunities = find_arbitrage_opportunities(
        [odd for event in initial_events for odd in event.odds()]
    )
    assert initial_opportunities

    await save_scan(
        initial_events,
        initial_opportunities,
        db_path=db_path,
        started_at=first_at,
        ended_at=first_at,
        collector_snapshots=[_complete("kto"), _complete("novibet")],
    )
    await save_scan(
        [_event("novibet", {"over": "1.65", "under": "2.25"}, captured_at=second_at)],
        [],
        db_path=db_path,
        started_at=second_at,
        ended_at=second_at,
        collector_snapshots=[_complete("kto"), _complete("novibet")],
    )

    kto_rows = await get_current_odds(
        db_path=db_path,
        bookmaker="kto",
        match_id=MATCH_ID,
    )
    assert {row["state"] for row in kto_rows} == {"grace"}
    assert not any(row["active"] for row in kto_rows)
    assert {row["consecutive_misses"] for row in kto_rows} == {1}
    assert await get_recent_opportunities(db_path=db_path, now=second_at) == []

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        opportunity = (
            await db.execute_fetchall(
                "SELECT active, close_reason FROM opportunities WHERE match_id = ?",
                (MATCH_ID,),
            )
        )[0]
    assert not bool(opportunity["active"])
    assert opportunity["close_reason"] == "event_removed"

    third_at = second_at + timedelta(minutes=2)
    await save_scan(
        [_event("novibet", {"over": "1.65", "under": "2.25"}, captured_at=third_at)],
        [],
        db_path=db_path,
        started_at=third_at,
        ended_at=third_at,
        collector_snapshots=[_complete("kto"), _complete("novibet")],
    )
    kto_rows = await get_current_odds(
        db_path=db_path,
        bookmaker="kto",
        match_id=MATCH_ID,
    )
    assert {row["state"] for row in kto_rows} == {"inactive"}
    assert {row["consecutive_misses"] for row in kto_rows} == {2}


async def test_failed_scan_marks_quote_indeterminate_without_counting_a_miss(
    tmp_path,
) -> None:
    db_path = tmp_path / "failure.db"
    first_at = datetime(2026, 6, 18, 2, 14, tzinfo=UTC)
    failed_at = first_at + timedelta(minutes=2)
    events = [_event("kto", {"over": "1.89", "under": "1.75"}, captured_at=first_at)]

    await save_scan(
        events,
        [],
        db_path=db_path,
        started_at=first_at,
        ended_at=first_at,
        collector_snapshots=[_complete("kto")],
    )
    await save_scan(
        [],
        [],
        db_path=db_path,
        started_at=failed_at,
        ended_at=failed_at,
        collector_snapshots=[
            CollectorSnapshot(
                bookmaker="kto",
                status="failure",
                is_complete_snapshot=True,
                error="timeout",
            )
        ],
    )

    rows = await get_current_odds(db_path=db_path, bookmaker="kto")
    assert {row["state"] for row in rows} == {"indeterminate"}
    assert {row["consecutive_misses"] for row in rows} == {0}
    assert not any(row["active"] for row in rows)

    partial_at = failed_at + timedelta(minutes=2)
    await save_scan(
        [],
        [],
        db_path=db_path,
        started_at=partial_at,
        ended_at=partial_at,
        collector_snapshots=[
            CollectorSnapshot(
                bookmaker="kto",
                status="partial",
                is_complete_snapshot=False,
            )
        ],
    )
    rows = await get_current_odds(db_path=db_path, bookmaker="kto")
    assert {row["state"] for row in rows} == {"indeterminate"}
    assert {row["consecutive_misses"] for row in rows} == {0}


async def test_price_change_keeps_history_and_updates_current_quote(tmp_path) -> None:
    db_path = tmp_path / "price-change.db"
    first_at = datetime(2026, 6, 18, 2, 14, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=2)

    await save_scan(
        [_event("kto", {"over": "1.89"}, captured_at=first_at)],
        [],
        db_path=db_path,
        started_at=first_at,
        ended_at=first_at,
        collector_snapshots=[_complete("kto")],
    )
    await save_scan(
        [_event("kto", {"over": "1.79"}, captured_at=second_at)],
        [],
        db_path=db_path,
        started_at=second_at,
        ended_at=second_at,
        collector_snapshots=[_complete("kto")],
    )

    async with aiosqlite.connect(db_path) as db:
        history = await db.execute_fetchall(
            """
            SELECT price
            FROM odds
            WHERE bookmaker = 'kto' AND outcome_key = 'over'
            ORDER BY id
            """
        )
    assert [str(row[0]) for row in history] == ["1.89", "1.79"]
    current = await get_current_odds(db_path=db_path, bookmaker="kto")
    assert current[0]["price"] == "1.79"
    assert current[0]["last_seen_at"] == second_at.isoformat()


async def test_unchanged_price_advances_last_seen_and_never_becomes_stale(tmp_path) -> None:
    db_path = tmp_path / "stable.db"
    first_at = datetime(2026, 6, 18, 2, 14, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=2)

    first_scan_id = await save_scan(
        [_event("kto", {"over": "1.89"}, captured_at=first_at)],
        [],
        db_path=db_path,
        started_at=first_at,
        ended_at=first_at,
        collector_snapshots=[_complete("kto")],
    )
    second_scan_id = await save_scan(
        [_event("kto", {"over": "1.89"}, captured_at=second_at)],
        [],
        db_path=db_path,
        started_at=second_at,
        ended_at=second_at,
        collector_snapshots=[_complete("kto")],
    )

    current = await get_current_odds(db_path=db_path, bookmaker="kto")
    assert current[0]["state"] == "active"
    assert current[0]["active"]
    assert current[0]["last_seen_at"] == second_at.isoformat()
    assert current[0]["last_seen_scan_id"] == second_scan_id
    assert first_scan_id != second_scan_id
    async with aiosqlite.connect(db_path) as db:
        history_count = (
            await db.execute_fetchall(
                "SELECT COUNT(*) FROM odds WHERE bookmaker = 'kto' AND outcome_key = 'over'"
            )
        )[0][0]
    assert history_count == 2


async def test_reappearing_opportunity_updates_last_seen_instead_of_being_ignored(
    tmp_path,
) -> None:
    db_path = tmp_path / "opportunity-upsert.db"
    first_at = datetime(2026, 6, 18, 2, 14, tzinfo=UTC)
    second_at = first_at + timedelta(minutes=2)
    events = [
        _event("kto", {"over": "1.89", "under": "1.75"}, captured_at=first_at),
        _event("novibet", {"over": "1.65", "under": "2.25"}, captured_at=first_at),
    ]
    opportunities = find_arbitrage_opportunities([odd for event in events for odd in event.odds()])

    await save_scan(
        events,
        opportunities,
        db_path=db_path,
        started_at=first_at,
        ended_at=first_at,
        collector_snapshots=[_complete("kto"), _complete("novibet")],
    )
    second_scan_id = await save_scan(
        events,
        opportunities,
        db_path=db_path,
        started_at=second_at,
        ended_at=second_at,
        collector_snapshots=[_complete("kto"), _complete("novibet")],
    )

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        rows = await db.execute_fetchall(
            """
            SELECT first_seen_at, last_seen_at, last_seen_scan_id, active
            FROM opportunities
            WHERE match_id = ?
            """,
            (MATCH_ID,),
        )
    assert len(rows) == 1
    assert rows[0]["last_seen_at"] == second_at.isoformat()
    assert rows[0]["last_seen_scan_id"] == second_scan_id
    assert bool(rows[0]["active"])


async def test_qa_rejects_legs_two_hours_apart_even_when_individually_fresh(
    tmp_path,
) -> None:
    db_path = tmp_path / "coherence.db"
    old_at = datetime(2026, 6, 18, 2, 0, tzinfo=UTC)
    current_at = old_at + timedelta(hours=2)

    await save_scan(
        [_event("kto", {"over": "1.89", "under": "1.75"}, captured_at=old_at)],
        [],
        db_path=db_path,
        started_at=old_at,
        ended_at=old_at,
        collector_snapshots=[_complete("kto")],
    )
    await save_scan(
        [_event("bateubet", {"over": "1.65", "under": "2.25"}, captured_at=current_at)],
        [],
        db_path=db_path,
        started_at=current_at,
        ended_at=current_at,
        collector_snapshots=[_complete("bateubet")],
    )

    coherent_only = await get_qa_top_candidates(
        db_path=db_path,
        now=current_at,
        freshness=timedelta(hours=3),
        max_leg_skew=timedelta(minutes=5),
    )
    permissive = await get_qa_top_candidates(
        db_path=db_path,
        now=current_at,
        freshness=timedelta(hours=3),
        max_leg_skew=timedelta(hours=3),
    )

    assert coherent_only == []
    assert permissive
    assert permissive[0]["is_arbitrage"]
