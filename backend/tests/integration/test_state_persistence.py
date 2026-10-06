from __future__ import annotations

from datetime import UTC, datetime, timedelta

import aiosqlite

from odds_arb.store import (
    init_db,
    load_collector_states,
    mark_opportunities_reported,
    recently_reported_fingerprints,
    save_collector_states,
)


async def test_init_db_enables_wal_journal_mode(tmp_path) -> None:
    db_path = tmp_path / "wal.db"

    await init_db(db_path)

    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("PRAGMA journal_mode")
        row = await cursor.fetchone()
    assert row is not None
    assert str(row[0]).lower() == "wal"


async def test_load_collector_states_on_empty_db_returns_empty(tmp_path) -> None:
    states = await load_collector_states(db_path=tmp_path / "state.db")
    assert states == {}


async def test_collector_states_round_trip(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    paused_until = datetime(2026, 6, 16, 21, 30, tzinfo=UTC)

    await save_collector_states(
        {
            "betano": (3, paused_until),
            "kto": (0, None),
        },
        db_path=db_path,
    )

    states = await load_collector_states(db_path=db_path)

    assert states == {
        "betano": (3, paused_until),
        "kto": (0, None),
    }


async def test_collector_states_overwrite_on_resave(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    await save_collector_states({"betano": (3, datetime(2026, 6, 16, tzinfo=UTC))}, db_path=db_path)

    await save_collector_states({"betano": (0, None)}, db_path=db_path)

    assert await load_collector_states(db_path=db_path) == {"betano": (0, None)}


async def test_recently_reported_fingerprints_respects_window(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    now = datetime(2026, 6, 16, 21, 0, tzinfo=UTC)

    await mark_opportunities_reported(["fp-recent"], db_path=db_path, now=now)
    await mark_opportunities_reported(["fp-old"], db_path=db_path, now=now - timedelta(minutes=30))

    recent = await recently_reported_fingerprints(
        window=timedelta(minutes=10), db_path=db_path, now=now
    )

    assert recent == {"fp-recent"}


async def test_mark_opportunities_reported_updates_timestamp(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    first = datetime(2026, 6, 16, 21, 0, tzinfo=UTC)
    later = first + timedelta(minutes=20)

    await mark_opportunities_reported(["fp"], db_path=db_path, now=first)
    await mark_opportunities_reported(["fp"], db_path=db_path, now=later)

    recent = await recently_reported_fingerprints(
        window=timedelta(minutes=10), db_path=db_path, now=later + timedelta(minutes=5)
    )

    assert recent == {"fp"}
