from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite
import httpx

from odds_arb.collectors.base import Collector, RawEvent, RawMarket
from odds_arb.core.arbitrage import find_arbitrage_opportunities
from odds_arb.core.dedup import canonical_match_id
from odds_arb.core.models import ArbitrageOpportunity, Match, Odd
from odds_arb.notifier import TelegramNotifier
from odds_arb.scheduler import DEFAULT_CIRCUIT_BREAKER_THRESHOLD, Scanner, run_daemon
from odds_arb.server import create_app
from odds_arb.store import (
    ARB_HALL_OF_FAME_LIMIT,
    get_arb_hall_of_fame,
    get_recent_odds,
    get_recent_opportunities,
    init_db,
    save_scan,
)


def _match() -> Match:
    return Match(
        match_id="flamengo-vasco",
        home_team="Flamengo",
        away_team="Vasco",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        league="Brasileirao Serie A",
        raw_event_id="raw-1",
    )


def _odd(bookmaker: str, outcome_key: str, price: str) -> Odd:
    return Odd(
        match_id="flamengo-vasco",
        market_key="1x2",
        outcome_key=outcome_key,
        price=Decimal(price),
        bookmaker=bookmaker,
        captured_at=datetime.now(UTC),
        event_id=f"{bookmaker}:flamengo-vasco",
        market_id=f"{bookmaker}:flamengo-vasco:1x2",
        selection_id=f"{bookmaker}:flamengo-vasco:1x2:{outcome_key}",
        raw_label=outcome_key,
    )


def _event(bookmaker: str, prices: dict[str, str]) -> RawEvent:
    selections = [_odd(bookmaker, outcome_key, price) for outcome_key, price in prices.items()]
    return RawEvent(
        event_id=f"{bookmaker}:event-1",
        bookmaker=bookmaker,
        match=_match(),
        markets=[
            RawMarket(
                market_id=f"{bookmaker}:market-1",
                label="Resultado",
                selections=selections,
            )
        ],
    )


def _arb_events() -> list[RawEvent]:
    return [
        _event("betano", {"home": "2.50", "draw": "3.30", "away": "4.00"}),
        _event("kto", {"home": "2.40", "draw": "3.60", "away": "3.90"}),
    ]


def _hall_opportunity(
    *,
    match_id: str,
    profit_pct: str,
    detected_at: datetime,
    bookmakers: tuple[str, str, str] = ("betano", "kto", "superbet"),
) -> ArbitrageOpportunity:
    prices = {"home": "3.00", "draw": "4.00", "away": "5.00"}
    best_odds = {
        outcome: Odd(
            match_id=match_id,
            market_key="1x2",
            outcome_key=outcome,
            price=Decimal(prices[outcome]),
            bookmaker=bookmaker,
            selection_id=f"{bookmaker}:{outcome}",
        )
        for outcome, bookmaker in zip(prices, bookmakers, strict=True)
    }
    return ArbitrageOpportunity(
        match_id=match_id,
        market_key="1x2",
        best_odds=best_odds,
        implied_probability_sum=Decimal("0.783333"),
        profit_pct=Decimal(profit_pct),
        detected_at=detected_at,
    )


def _hall_event(match_id: str, *, league: str = "Liga Teste") -> RawEvent:
    match = Match(
        match_id=match_id,
        home_team=f"Casa {match_id}",
        away_team=f"Fora {match_id}",
        starts_at=datetime.now(UTC) + timedelta(days=1),
        league=league,
    )
    return RawEvent(
        event_id=f"betano:{match_id}",
        bookmaker="betano",
        match=match,
        markets=[
            RawMarket(
                market_id=f"betano:{match_id}:1x2",
                label="Resultado",
                selections=[
                    Odd(
                        match_id=match_id,
                        market_key="1x2",
                        outcome_key=outcome,
                        price=Decimal(price),
                        bookmaker="betano",
                    )
                    for outcome, price in {
                        "home": "3.00",
                        "draw": "4.00",
                        "away": "5.00",
                    }.items()
                ],
            )
        ],
    )


def _house_event(
    bookmaker: str,
    home: str,
    away: str,
    kickoff: datetime,
    prices: dict[str, str],
) -> RawEvent:
    temporary = Match(
        match_id="raw",
        sport="soccer",
        home_team=home,
        away_team=away,
        starts_at=kickoff,
    )
    match = temporary.model_copy(update={"match_id": canonical_match_id(temporary)})
    selections = [
        Odd(
            match_id=match.match_id,
            market_key="1x2",
            outcome_key=outcome_key,
            price=Decimal(price),
            bookmaker=bookmaker,
        )
        for outcome_key, price in prices.items()
    ]
    return RawEvent(
        event_id=f"{bookmaker}:event-1",
        bookmaker=bookmaker,
        match=match,
        markets=[
            RawMarket(market_id=f"{bookmaker}:market-1", label="Resultado", selections=selections)
        ],
    )


class FakeCollector(Collector):
    def __init__(self, name: str, events: Sequence[RawEvent]) -> None:
        self.name = name
        self._events = list(events)

    async def fetch(self, sport: str = "soccer") -> Sequence[RawEvent]:
        return self._events


class BrokenCollector(Collector):
    name = "broken"

    async def fetch(self, sport: str = "soccer") -> Sequence[RawEvent]:
        raise RuntimeError("collector exploded")


class ReportingFailureCollector(Collector):
    name = "reporting_failure"

    async def fetch(self, sport: str = "soccer") -> Sequence[RawEvent]:
        self.last_error = "blocked by upstream"
        return []


async def test_store_persists_odds_and_opportunities(tmp_path) -> None:
    db_path = tmp_path / "arb.db"
    events = _arb_events()
    odds = [odd for event in events for odd in event.odds()]
    opportunities = find_arbitrage_opportunities(
        odds,
        bankroll=Decimal("1000"),
        min_profit_pct=Decimal("0"),
    )

    await save_scan(events, opportunities, db_path=db_path)

    odd_rows = await get_recent_odds(db_path=db_path, limit=10)
    opportunity_rows = await get_recent_opportunities(
        db_path=db_path,
        market="1x2",
        min_arb_pct=Decimal("0"),
    )

    assert len(odd_rows) == 6
    assert opportunity_rows
    assert opportunity_rows[0]["match_id"] == "flamengo-vasco"
    assert opportunity_rows[0]["market_key"] == "1x2"
    assert "betano" in opportunity_rows[0]["best_odds_json"]
    hall = await get_arb_hall_of_fame(db_path=db_path)
    assert hall[0]["home_team"] == "Flamengo"
    assert hall[0]["away_team"] == "Vasco"
    assert hall[0]["league"] == "Brasileirao Serie A"
    assert {odd["outcome_key"] for odd in hall[0]["best_odds"]} == {
        "home",
        "draw",
        "away",
    }


async def test_hall_of_fame_keeps_only_best_version_of_same_match(tmp_path) -> None:
    db_path = tmp_path / "hall-dedup.db"
    event = _hall_event("record-match")
    first_seen = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)

    await save_scan(
        [event],
        [
            _hall_opportunity(
                match_id="record-match",
                profit_pct="5.00",
                detected_at=first_seen,
            )
        ],
        db_path=db_path,
    )
    await save_scan(
        [event],
        [
            _hall_opportunity(
                match_id="record-match",
                profit_pct="3.00",
                detected_at=first_seen + timedelta(hours=1),
            )
        ],
        db_path=db_path,
    )
    await save_scan(
        [event],
        [
            _hall_opportunity(
                match_id="record-match",
                profit_pct="8.00",
                detected_at=first_seen + timedelta(hours=2),
                bookmakers=("novibet", "sportingbet", "betfair"),
            )
        ],
        db_path=db_path,
    )
    await save_scan(
        [event],
        [
            ArbitrageOpportunity(
                match_id="record-match",
                market_key="both_teams_score",
                best_odds={
                    "yes": Odd(
                        match_id="record-match",
                        market_key="both_teams_score",
                        outcome_key="yes",
                        price=Decimal("2.20"),
                        bookmaker="betano",
                    ),
                    "no": Odd(
                        match_id="record-match",
                        market_key="both_teams_score",
                        outcome_key="no",
                        price=Decimal("2.40"),
                        bookmaker="kto",
                    ),
                },
                implied_probability_sum=Decimal("0.871212"),
                profit_pct=Decimal("9.00"),
                detected_at=first_seen + timedelta(hours=3),
            )
        ],
        db_path=db_path,
    )

    hall = await get_arb_hall_of_fame(db_path=db_path, limit=10)

    assert len(hall) == 1
    assert hall[0]["market_key"] == "both_teams_score"
    assert hall[0]["profit_pct"] == "9.00"
    assert hall[0]["detected_at"] == (first_seen + timedelta(hours=3)).isoformat()
    assert {odd["bookmaker"] for odd in hall[0]["best_odds"]} == {
        "betano",
        "kto",
    }


async def test_hall_of_fame_persists_top_ten_and_returns_requested_top_three(tmp_path) -> None:
    db_path = tmp_path / "hall-limit.db"
    detected_at = datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    events = [_hall_event(f"match-{index}") for index in range(12)]
    opportunities = [
        _hall_opportunity(
            match_id=f"match-{index}",
            profit_pct=str(index + 1),
            detected_at=detected_at + timedelta(minutes=index),
        )
        for index in range(12)
    ]

    await save_scan(events, opportunities, db_path=db_path)

    top_ten = await get_arb_hall_of_fame(db_path=db_path, limit=ARB_HALL_OF_FAME_LIMIT)
    top_three = await get_arb_hall_of_fame(db_path=db_path, limit=3)

    assert len(top_ten) == 10
    assert [row["profit_pct"] for row in top_three] == ["12.00", "11.00", "10.00"]
    assert {row["match_id"] for row in top_ten}.isdisjoint({"match-0", "match-1"})


async def test_hall_of_fame_seeds_from_existing_opportunities(tmp_path) -> None:
    db_path = tmp_path / "hall-seed.db"
    detected_at = datetime(2026, 6, 16, 9, 30, tzinfo=UTC)
    starts_at = datetime(2026, 6, 17, 20, 0, tzinfo=UTC)
    best_odds_json = """
    [
      {"outcome_key":"home","bookmaker":"betano","price":"3.00","selection_id":"h"},
      {"outcome_key":"draw","bookmaker":"kto","price":"4.00","selection_id":"d"},
      {"outcome_key":"away","bookmaker":"superbet","price":"5.00","selection_id":"a"}
    ]
    """
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO matches (
                match_id, sport, home_team, away_team, starts_at, league, raw_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-match",
                "soccer",
                "Palmeiras",
                "Santos",
                starts_at.isoformat(),
                "Brasileirao",
                "legacy",
                detected_at.isoformat(),
            ),
        )
        await db.execute(
            """
            INSERT INTO opportunities (
                fingerprint, match_id, market_key, implied_probability_sum, profit_pct,
                best_odds_json, stakes_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-fingerprint",
                "legacy-match",
                "1x2",
                "0.783333",
                "27.66",
                best_odds_json,
                "{}",
                detected_at.isoformat(),
            ),
        )
        await db.commit()

    await save_scan([], [], db_path=db_path)

    hall = await get_arb_hall_of_fame(db_path=db_path)
    assert len(hall) == 1
    assert hall[0]["match_id"] == "legacy-match"
    assert hall[0]["profit_pct"] == "27.66"
    assert hall[0]["detected_at"] == detected_at.isoformat()


async def test_scanner_runs_collectors_detects_arbs_and_saves_scan(tmp_path) -> None:
    db_path = tmp_path / "scan.db"
    collectors = [
        FakeCollector("betano", [_arb_events()[0]]),
        FakeCollector("kto", [_arb_events()[1]]),
    ]

    result = await Scanner(
        collectors,
        db_path=db_path,
        min_profit_pct=Decimal("0"),
        opportunity_log_path=tmp_path / "opps.log",
    ).scan_once()

    assert result.raw_matches == 2
    assert result.unified_matches == 1
    assert len(result.odds) == 6
    assert result.opportunities
    assert await get_recent_opportunities(db_path=db_path, min_arb_pct=Decimal("0"))


async def test_scanner_discards_invalid_odd_prices_before_arbitrage(tmp_path) -> None:
    db_path = tmp_path / "invalid-odds.db"
    collectors = [
        FakeCollector("betano", [_event("betano", {"home": "2.50", "draw": "1", "away": "51"})])
    ]

    result = await Scanner(
        collectors,
        db_path=db_path,
        min_profit_pct=Decimal("0"),
        opportunity_log_path=tmp_path / "opps.log",
    ).scan_once()

    assert [(odd.outcome_key, odd.price) for odd in result.odds] == [("home", Decimal("2.50"))]
    assert result.opportunities == []
    saved_odds = await get_recent_odds(db_path=db_path, limit=10)
    assert len(saved_odds) == 1
    assert saved_odds[0]["outcome_key"] == "home"


async def test_scanner_keeps_started_events_for_live_classification(tmp_path) -> None:
    db_path = tmp_path / "past-events.db"
    past_event = _house_event(
        "betano",
        "Argentina",
        "Argelia",
        datetime.now(UTC) - timedelta(hours=1),
        {"home": "2.50", "draw": "3.30", "away": "4.00"},
    )

    result = await Scanner(
        [FakeCollector("betano", [past_event])],
        db_path=db_path,
        min_profit_pct=Decimal("0"),
        opportunity_log_path=tmp_path / "opps.log",
    ).scan_once()

    assert result.events == [past_event]
    assert len(result.odds) == 3
    assert len(result.opportunities) == 1
    assert len(await get_recent_odds(db_path=db_path, limit=10)) == 3


async def test_telegram_failure_does_not_propagate_to_scanner(tmp_path) -> None:
    db_path = tmp_path / "telegram-failure.db"
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ConnectError("offline", request=request)

    async def no_sleep(_: float) -> None:
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            sleep=no_sleep,
        )
        result = await Scanner(
            [
                FakeCollector("betano", [_arb_events()[0]]),
                FakeCollector("kto", [_arb_events()[1]]),
            ],
            db_path=db_path,
            min_profit_pct=Decimal("0"),
            opportunity_log_path=tmp_path / "opps.log",
            notifier=notifier,
        ).scan_once()

    assert result.opportunities
    assert requests == 2


async def test_scanner_writes_opportunity_log_with_team_names(tmp_path) -> None:
    db_path = tmp_path / "scan.db"
    log_path = tmp_path / "logs" / "opportunities.log"
    collectors = [
        FakeCollector("betano", [_arb_events()[0]]),
        FakeCollector("kto", [_arb_events()[1]]),
    ]

    result = await Scanner(
        collectors,
        db_path=db_path,
        min_profit_pct=Decimal("0"),
        opportunity_log_path=log_path,
    ).scan_once()

    assert result.opportunities
    assert log_path.exists()
    contents = log_path.read_text(encoding="utf-8")
    assert "ARB ENCONTRADO" in contents
    assert "Flamengo vs Vasco" in contents


async def test_scanner_unifies_cross_house_variants_and_detects_arb(tmp_path) -> None:
    # Mesmo jogo, nomes variantes e 2 min de diferenca cruzando o bucket de 10 min.
    kickoff = (datetime.now(UTC) + timedelta(days=1)).replace(
        hour=21,
        minute=9,
        second=0,
        microsecond=0,
    )
    kto = _house_event(
        "kto",
        "Flamengo",
        "Vasco",
        kickoff,
        {"home": "2.10", "draw": "3.40", "away": "3.50"},
    )
    superbet = _house_event(
        "superbet",
        "Flamengo RJ",
        "Vasco da Gama",
        kickoff + timedelta(minutes=2),
        {"home": "1.90", "draw": "3.60", "away": "4.30"},
    )
    assert kto.match.match_id != superbet.match.match_id

    result = await Scanner(
        [FakeCollector("kto", [kto]), FakeCollector("superbet", [superbet])],
        db_path=tmp_path / "unify.db",
        min_profit_pct=Decimal("0"),
        opportunity_log_path=tmp_path / "opps.log",
    ).scan_once()

    assert result.raw_matches == 2
    assert result.unified_matches == 1
    assert len(result.opportunities) == 1
    best_odds = result.opportunities[0].best_odds
    assert {odd.bookmaker for odd in best_odds.values()} == {"kto", "superbet"}


async def test_scanner_circuit_breaker_pauses_repeated_failures(tmp_path) -> None:
    scanner = Scanner([BrokenCollector()], db_path=tmp_path / "broken.db")

    for _ in range(DEFAULT_CIRCUIT_BREAKER_THRESHOLD):
        result = await scanner.scan_once()
        assert result.events == []

    state = scanner._states["broken"]
    assert state.paused_until is not None

    paused_result = await scanner.scan_once()
    assert paused_result.events == []


async def test_circuit_breaker_state_persists_across_scanner_restart(tmp_path) -> None:
    db_path = tmp_path / "persist.db"
    log_path = tmp_path / "opps.log"

    first = Scanner([BrokenCollector()], db_path=db_path, opportunity_log_path=log_path)
    for _ in range(DEFAULT_CIRCUIT_BREAKER_THRESHOLD):
        await first.scan_once()
    assert first._states["broken"].paused_until is not None

    restarted = Scanner([BrokenCollector()], db_path=db_path, opportunity_log_path=log_path)
    await restarted.scan_once()

    assert restarted._states["broken"].failures >= DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    assert restarted._states["broken"].paused_until is not None


async def test_scanner_circuit_breaker_counts_collector_reported_errors(tmp_path) -> None:
    scanner = Scanner([ReportingFailureCollector()], db_path=tmp_path / "reported.db")

    for _ in range(DEFAULT_CIRCUIT_BREAKER_THRESHOLD):
        await scanner.scan_once()

    state = scanner._states["reporting_failure"]
    assert state.paused_until is not None


async def test_run_daemon_runs_fixed_iterations_and_sleeps_between(tmp_path) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    scanner = Scanner(
        [FakeCollector("kto", [])],
        db_path=tmp_path / "daemon.db",
        opportunity_log_path=tmp_path / "opps.log",
    )

    completed = await run_daemon(
        scanner,
        interval_seconds=120,
        iterations=3,
        sleep=fake_sleep,
    )

    assert completed == 3
    assert sleeps == [120, 120]


async def test_run_daemon_sends_startup_message_once(tmp_path) -> None:
    payloads: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=tmp_path / "startup.db",
            client=client,
        )
        scanner = Scanner(
            [FakeCollector("kto", [])],
            db_path=tmp_path / "startup.db",
            opportunity_log_path=tmp_path / "opps.log",
            notifier=notifier,
        )
        assert await run_daemon(scanner, interval_seconds=120, iterations=1) == 1

    assert [payload["text"] for payload in payloads] == ["🤖 Scanner iniciado — monitorando 1 casa"]


async def test_dashboard_renders_saved_data_and_sanitizes_filter(tmp_path) -> None:
    db_path = tmp_path / "dashboard.db"
    events = _arb_events()
    odds = [odd for event in events for odd in event.odds()]
    opportunities = find_arbitrage_opportunities(
        odds,
        bankroll=Decimal("1000"),
        min_profit_pct=Decimal("0"),
    )
    await save_scan(events, opportunities, db_path=db_path)

    transport = httpx.ASGITransport(app=create_app(db_path=db_path))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/", params={"market": "1x2", "min_arb": "not-a-number"})

    assert response.status_code == 200
    assert "odds-arb-br" in response.text
    assert "flamengo-vasco" in response.text
    assert "Nenhuma oportunidade" not in response.text
