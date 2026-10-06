import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.core.tennis_markets import TennisMarket
from odds_arb.tennis_live import (
    TennisLiveConfig,
    format_tennis_cycle_report,
    run_tennis_live_cycle,
)

BETANO_FIXTURE = Path("tests/fixtures/deep_markets/betano_tennis_event_ssr_sample.json")
SUPERBET_LIST_FIXTURE = Path("tests/fixtures/deep_markets/superbet_tennis_list_sample.json")
SUPERBET_DETAIL_FIXTURE = Path(
    "tests/fixtures/deep_markets/superbet_tennis_event_detail_sample.json"
)

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)


def _config() -> TennisLiveConfig:
    return TennisLiveConfig(
        min_profit_pct=Decimal("0"),
        bankroll=Decimal("1000"),
        interval_seconds=60.0,
    )


def _betano_events() -> list[dict]:
    return [json.loads(BETANO_FIXTURE.read_text(encoding="utf-8"))]


def _superbet_list() -> dict:
    payload = json.loads(SUPERBET_LIST_FIXTURE.read_text(encoding="utf-8"))
    payload["data"].append(
        {
            "eventId": 999,
            "matchName": "J.Doe·R.Roe",
            "utcDate": "2026-07-02T20:00:00Z",
            "sportId": 2,
        }
    )
    return payload


def _superbet_detail(*, smith_price: float = 3.4) -> dict:
    payload = json.loads(SUPERBET_DETAIL_FIXTURE.read_text(encoding="utf-8"))
    for odd in payload["data"][0]["odds"]:
        if odd["uuid"] == "sbt-winner-2":
            odd["price"] = smith_price
    return payload


def test_cycle_collects_pairs_and_detects_winner_arb() -> None:
    detail_calls: list[str] = []

    def detail_fetcher(event_id: str) -> dict | None:
        detail_calls.append(event_id)
        return _superbet_detail(smith_price=4.6)["data"][0]

    report = run_tennis_live_cycle(
        _config(),
        betano_fetcher=lambda: _betano_events(),
        superbet_list_fetcher=lambda start, end: _superbet_list(),
        superbet_detail_fetcher=detail_fetcher,
        now=NOW,
    )

    assert report.collected_by_house == {"betano": 6, "superbet": 4}
    assert detail_calls == ["13841825"]  # o evento não pareado (999) nunca é buscado
    assert report.fresh_odds == 10
    assert report.dropped_started == 0

    assert len(report.opportunities) == 1
    opp = report.opportunities[0].opportunity
    assert opp.market is TennisMarket.MATCH_WINNER
    assert {opp.leg_a.bookmaker, opp.leg_b.bookmaker} == {"betano", "superbet"}
    stakes = report.opportunities[0].stakes
    assert sum(stakes.values()) == Decimal("1000")


def test_cycle_drops_started_events() -> None:
    late_now = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    report = run_tennis_live_cycle(
        _config(),
        betano_fetcher=lambda: _betano_events(),
        superbet_list_fetcher=lambda start, end: _superbet_list(),
        superbet_detail_fetcher=lambda event_id: _superbet_detail()["data"][0],
        now=late_now,
    )
    assert report.fresh_odds == 0
    assert report.dropped_started == 10
    assert report.opportunities == []


def test_cycle_survives_betano_failure_and_skips_superbet() -> None:
    list_calls: list[tuple[datetime, datetime]] = []

    def list_fetcher(start: datetime, end: datetime) -> dict:
        list_calls.append((start, end))
        return _superbet_list()

    def betano_fetcher() -> list[dict]:
        raise RuntimeError("bloqueio cloudflare")

    report = run_tennis_live_cycle(
        _config(),
        betano_fetcher=betano_fetcher,
        superbet_list_fetcher=list_fetcher,
        superbet_detail_fetcher=lambda event_id: None,
        now=NOW,
    )
    assert report.collected_by_house == {"betano": 0, "superbet": 0}
    assert list_calls == []
    assert report.opportunities == []


def test_cycle_survives_superbet_detail_failure() -> None:
    def detail_fetcher(event_id: str) -> dict | None:
        raise RuntimeError("timeout")

    report = run_tennis_live_cycle(
        _config(),
        betano_fetcher=lambda: _betano_events(),
        superbet_list_fetcher=lambda start, end: _superbet_list(),
        superbet_detail_fetcher=detail_fetcher,
        now=NOW,
    )
    assert report.collected_by_house == {"betano": 6, "superbet": 0}


def test_format_report_mentions_houses_and_opportunities() -> None:
    report = run_tennis_live_cycle(
        _config(),
        betano_fetcher=lambda: _betano_events(),
        superbet_list_fetcher=lambda start, end: _superbet_list(),
        superbet_detail_fetcher=lambda event_id: _superbet_detail(smith_price=4.6)["data"][0],
        now=NOW,
    )
    text = format_tennis_cycle_report(report)
    assert "betano=6" in text
    assert "superbet=4" in text
    assert "match_winner" in text
