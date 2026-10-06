import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.competitions import resolve_competition
from odds_arb.core.deep_markets import Side
from odds_arb.core.tennis_markets import (
    TennisMarket,
    TennisMarketOdd,
    detect_tennis_arbs,
)
from odds_arb.deep_live import LiveDeepConfig
from odds_arb.deep_web import (
    MARKET_OPTIONS,
    InMemorySignalStore,
    SignalSnapshot,
    _row_to_dict,
    _rows_for_request,
    build_loop_runner,
    render_tennis_rows,
)

START = datetime(2026, 7, 2, 22, 0, tzinfo=UTC)

BETANO_FIXTURE = Path("tests/fixtures/deep_markets/betano_tennis_event_ssr_sample.json")
SUPERBET_LIST_FIXTURE = Path("tests/fixtures/deep_markets/superbet_tennis_list_sample.json")
SUPERBET_DETAIL_FIXTURE = Path(
    "tests/fixtures/deep_markets/superbet_tennis_event_detail_sample.json"
)


def _winner_odd(bookmaker: str, player: str, odd: str) -> TennisMarketOdd:
    return TennisMarketOdd(
        bookmaker=bookmaker,
        raw_event_id="e1",
        raw_market_id="m-winner",
        raw_selection_id=f"{bookmaker}:{player}",
        event_name="Andrej Nedic - Keegan Smith",
        player_a="Andrej Nedic",
        player_b="Keegan Smith",
        start_time=START,
        market=TennisMarket.MATCH_WINNER,
        winner_player=player,
        odd=Decimal(odd),
        raw_market_name="Vencedor",
        raw_selection_name=player,
        source_event_url=f"https://{bookmaker}.example/evento",
    )


def _total_odd(bookmaker: str, side: Side, odd: str) -> TennisMarketOdd:
    return TennisMarketOdd(
        bookmaker=bookmaker,
        raw_event_id="e1",
        raw_market_id="m-total",
        raw_selection_id=f"{bookmaker}:{side.value}",
        event_name="Andrej Nedic - Keegan Smith",
        player_a="Andrej Nedic",
        player_b="Keegan Smith",
        start_time=START,
        market=TennisMarket.MATCH_TOTAL_GAMES,
        side=side,
        line=Decimal("21.5"),
        odd=Decimal(odd),
        raw_market_name="Total de Games",
        raw_selection_name="Mais de 21.5" if side is Side.OVER else "Menos de 21.5",
    )


def _winner_opportunity():
    return detect_tennis_arbs(
        [
            _winner_odd("betano", "Andrej Nedic", "2.10"),
            _winner_odd("superbet", "Keegan Smith", "2.05"),
        ]
    )[0]


def _total_opportunity():
    return detect_tennis_arbs(
        [_total_odd("betano", Side.OVER, "2.10"), _total_odd("superbet", Side.UNDER, "2.05")]
    )[0]


def test_render_tennis_winner_row_uses_player_leg_labels() -> None:
    rows = render_tennis_rows([_winner_opportunity()], Decimal("1000"))
    assert len(rows) == 1
    row = rows[0]
    assert row.metric == "tennis_winner"
    assert row.match == "Andrej Nedic x Keegan Smith"
    assert row.over_label == "Andrej Nedic"
    assert row.under_label == "Keegan Smith"
    assert row.line == ""
    assert row.over_stake + row.under_stake == Decimal("1000")
    assert row.settlement_warning is False


def test_render_tennis_total_row_keeps_over_under_labels() -> None:
    rows = render_tennis_rows([_total_opportunity()], Decimal("1000"))
    row = rows[0]
    assert row.metric == "tennis_games"
    assert row.over_label == "over"
    assert row.under_label == "under"
    assert row.line == "21.5"


def test_market_options_include_tennis() -> None:
    values = {value for value, _label in MARKET_OPTIONS}
    assert "tennis_winner" in values
    assert "tennis_games" in values


def test_rows_for_request_appends_tennis_and_filters_by_market() -> None:
    snapshot = SignalSnapshot(
        opportunities=(),
        collected_by_house={"betano": 0},
        competition_alias="copa-do-mundo",
        updated_at=datetime.now(UTC),
        tennis_opportunities=(_winner_opportunity(), _total_opportunity()),
    )
    all_rows = _rows_for_request(
        snapshot, competition=None, min_arb="0", market=None, bankroll="1000"
    )
    assert {r.metric for r in all_rows} == {"tennis_winner", "tennis_games"}

    winner_only = _rows_for_request(
        snapshot, competition=None, min_arb="0", market="tennis_winner", bankroll="1000"
    )
    assert [r.metric for r in winner_only] == ["tennis_winner"]

    corners_only = _rows_for_request(
        snapshot, competition=None, min_arb="0", market="corners", bankroll="1000"
    )
    assert corners_only == []

    high_min = _rows_for_request(
        snapshot, competition=None, min_arb="50", market=None, bankroll="1000"
    )
    assert high_min == []


def test_row_to_dict_exposes_leg_labels() -> None:
    row = render_tennis_rows([_winner_opportunity()], Decimal("1000"))[0]
    payload = _row_to_dict(row)
    assert payload["over_label"] == "Andrej Nedic"
    assert payload["under_label"] == "Keegan Smith"


def test_loop_runner_stores_tennis_opportunities(tmp_path) -> None:
    store = InMemorySignalStore()
    config = LiveDeepConfig(
        competition=resolve_competition("copa-do-mundo"),
        min_profit_pct=Decimal("0"),
        bankroll=Decimal("1000"),
        interval_seconds=0.01,
        audit_path=tmp_path / "audit.jsonl",
    )
    # As fixtures têm data fixa; o ciclo usa datetime.now(). Empurra o início
    # para o futuro (mesmo dia UTC nas duas casas) para o teste não apodrecer.
    future = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0) + timedelta(
        days=2
    )

    def betano_events():
        event = json.loads(BETANO_FIXTURE.read_text(encoding="utf-8"))
        event["startTime"] = int(future.timestamp() * 1000)
        return [event]

    def superbet_detail(event_id: str):
        payload = json.loads(SUPERBET_DETAIL_FIXTURE.read_text(encoding="utf-8"))
        payload["data"][0]["utcDate"] = future.strftime("%Y-%m-%dT%H:%M:%SZ")
        for odd in payload["data"][0]["odds"]:
            if odd["uuid"] == "sbt-winner-2":
                odd["price"] = 4.6  # abre arb com o Nedic @1.32 da Betano
        return payload["data"][0]

    async def no_sleep(_delay: float) -> None:
        return None

    runner = build_loop_runner(
        store,
        config,
        betano_fetcher=lambda _competition: [],
        superbet_list_fetcher=lambda start, end: {"data": []},
        superbet_detail_fetcher=lambda event_id: None,
        tennis_betano_fetcher=betano_events,
        tennis_superbet_list_fetcher=lambda start, end: json.loads(
            SUPERBET_LIST_FIXTURE.read_text(encoding="utf-8")
        ),
        tennis_superbet_detail_fetcher=superbet_detail,
        max_iterations=1,
        sleep=no_sleep,
    )
    asyncio.run(runner())

    snapshot = store.latest()
    assert snapshot is not None
    assert len(snapshot.tennis_opportunities) == 1
    assert snapshot.tennis_opportunities[0].market is TennisMarket.MATCH_WINNER
    assert snapshot.collected_by_house.get("tenis_betano") == 6
    assert snapshot.collected_by_house.get("tenis_superbet") == 4
