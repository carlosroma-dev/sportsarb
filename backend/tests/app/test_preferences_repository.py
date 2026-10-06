from decimal import Decimal

from app.models.user_preferences import UserPreferences
from app.repositories.preferences_repository import LocalDatabase


def test_local_preferences_round_trip(tmp_path):
    db = LocalDatabase(tmp_path / "portfolio.db")
    assert db.get() == UserPreferences()
    db.save(
        UserPreferences(
            excluded_bookmakers=frozenset({"demo_a"}),
            min_profit_pct=Decimal("1.5"),
            default_bankroll=Decimal("250"),
        )
    )
    assert LocalDatabase(tmp_path / "portfolio.db").get().default_bankroll == Decimal("250")
