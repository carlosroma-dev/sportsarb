from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# Defaults used until the local SQLite file receives user preferences.
DEFAULT_MIN_PROFIT_PCT = Decimal("3")
DEFAULT_BANKROLL = Decimal("1000")


@dataclass(frozen=True)
class UserPreferences:
    excluded_bookmakers: frozenset[str] = field(default_factory=frozenset)
    min_profit_pct: Decimal = DEFAULT_MIN_PROFIT_PCT
    default_bankroll: Decimal = DEFAULT_BANKROLL
