from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from app.models.user_preferences import UserPreferences


class PreferencesRepository(Protocol):
    def get(self, user_id: str) -> UserPreferences: ...

    def save(self, preferences: UserPreferences) -> None: ...


class LocalDatabase:
    """Single-user portfolio data in a local SQLite file."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS preferences (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    excluded_bookmakers TEXT NOT NULL,
                    min_profit_pct TEXT NOT NULL,
                    default_bankroll TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('concluida', 'cancelada')),
                    payload TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def get(self, user_id: str = "local-demo") -> UserPreferences:
        del user_id
        with self._connect() as db:
            row = db.execute("SELECT * FROM preferences WHERE id = 1").fetchone()
        if row is None:
            return UserPreferences()
        return UserPreferences(
            excluded_bookmakers=frozenset(json.loads(row["excluded_bookmakers"])),
            min_profit_pct=Decimal(row["min_profit_pct"]),
            default_bankroll=Decimal(row["default_bankroll"]),
        )

    def save(self, preferences: UserPreferences) -> None:
        with self._connect() as db:
            db.execute(
                """INSERT INTO preferences VALUES (1, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                   excluded_bookmakers=excluded.excluded_bookmakers,
                   min_profit_pct=excluded.min_profit_pct,
                   default_bankroll=excluded.default_bankroll""",
                (
                    json.dumps(sorted(preferences.excluded_bookmakers)),
                    str(preferences.min_profit_pct),
                    str(preferences.default_bankroll),
                ),
            )

    def list_operations(self) -> list[dict[str, object]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM operations ORDER BY created_at DESC").fetchall()
        return [dict(json.loads(row["payload"]), status=row["status"]) for row in rows]

    def add_operation(self, operation: dict[str, object]) -> dict[str, object]:
        with self._connect() as db:
            db.execute(
                "INSERT INTO operations (id, created_at, status, payload) VALUES (?, ?, ?, ?)",
                (
                    operation["id"],
                    operation["created_at"],
                    operation["status"],
                    json.dumps(operation, ensure_ascii=False),
                ),
            )
        return operation

    def cancel_operation(self, operation_id: str) -> bool:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE operations SET status = 'cancelada' WHERE id = ?", (operation_id,)
            )
            return cursor.rowcount > 0
