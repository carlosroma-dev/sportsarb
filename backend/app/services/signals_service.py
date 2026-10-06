from __future__ import annotations

from typing import Any

from app.repositories.signal_repository import SignalRepository
from app.scanner import MARKET_OPTIONS, row_to_dict, rows_for_request


class SignalsService:
    def __init__(self, repo: SignalRepository, default_min_arb: str = "0") -> None:
        self._repo = repo
        self._default_min_arb = default_min_arb

    def get_signals(
        self,
        *,
        min_arb: str | None,
        market: str | None,
        bankroll: str,
        competition: str | None,
        excluded_bookmakers: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        snapshot = self._repo.latest()
        rows = rows_for_request(
            snapshot,
            competition=competition,
            min_arb=self._default_min_arb if min_arb is None else min_arb,
            market=market,
            bankroll=bankroll,
            excluded_bookmakers=excluded_bookmakers,
        )
        return {
            "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
            "competition": snapshot.competition_alias if snapshot else None,
            "collected_by_house": dict(snapshot.collected_by_house) if snapshot else {},
            "signals": [row_to_dict(r) for r in rows],
        }

    def market_options(self) -> list[dict[str, str]]:
        return [{"value": value, "label": label} for value, label in MARKET_OPTIONS]
