from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.repositories.signal_repository import InMemorySignalRepository
from app.scanner import InMemorySignalStore, SignalSnapshot
from app.services.signals_service import SignalsService

from odds_arb.core.deep_markets import DeepMarketOdd


def _service_with_snapshot(snapshot):
    store = InMemorySignalStore()
    if snapshot is not None:
        store.set(snapshot)
    return SignalsService(InMemorySignalRepository(store))


def test_empty_when_no_snapshot():
    svc = _service_with_snapshot(None)
    result = svc.get_signals(min_arb=None, market=None, bankroll="1000", competition=None)
    assert result == {
        "updated_at": None,
        "competition": None,
        "collected_by_house": {},
        "signals": [],
    }


def test_envelope_maps_snapshot_metadata():
    snapshot = SignalSnapshot(
        opportunities=(),
        collected_by_house={"betano": 3, "superbet": 2},
        competition_alias="copa-do-mundo",
        updated_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
    )
    svc = _service_with_snapshot(snapshot)
    result = svc.get_signals(min_arb=None, market=None, bankroll="1000", competition=None)
    assert result["competition"] == "copa-do-mundo"
    assert result["collected_by_house"] == {"betano": 3, "superbet": 2}
    assert result["updated_at"] == "2026-06-29T12:00:00+00:00"
    assert result["signals"] == []


def test_market_options_shape():
    svc = _service_with_snapshot(None)
    options = svc.market_options()
    assert {"value": "", "label": "Todos os mercados"} in options
    assert all({"value", "label"} == set(o) for o in options)


def _deep_odd(house: str, side: str, odd: str) -> DeepMarketOdd:
    return DeepMarketOdd(
        bookmaker=house,
        raw_event_id="e",
        raw_market_id="m",
        raw_selection_id=f"{house}{side}",
        event_name="A - B",
        home_team="Time A",
        away_team="Time B",
        start_time=datetime(2030, 1, 1, 21, 0, tzinfo=UTC) + timedelta(hours=3),
        period="full_time",
        market_family="match_total",
        metric="corners",
        subject=None,
        side=side,
        odd=Decimal(odd),
        raw_market_name="r",
        raw_selection_name="r",
        is_live=False,
        line=Decimal("9.5"),
        line_source="selection_handicap",
        source_event_url=f"https://x/{house}",
    )


def test_excluded_bookmakers_recompute_from_raw_odds():
    snapshot = SignalSnapshot(
        opportunities=(),
        collected_by_house={},
        competition_alias="copa-do-mundo",
        updated_at=datetime(2026, 6, 29, 12, 0, tzinfo=UTC),
        raw_odds=(
            _deep_odd("superbet", "over", "2.25"),
            _deep_odd("sportingbet", "under", "2.10"),
            _deep_odd("novibet", "under", "2.05"),
        ),
    )
    svc = _service_with_snapshot(snapshot)
    result = svc.get_signals(
        min_arb="0",
        market=None,
        bankroll="1000",
        competition=None,
        excluded_bookmakers=frozenset({"sportingbet"}),
    )
    assert len(result["signals"]) == 1
    assert result["signals"][0]["over_bookmaker"] == "superbet"
    assert result["signals"][0]["under_bookmaker"] == "novibet"
