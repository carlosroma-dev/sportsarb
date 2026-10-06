import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from odds_arb.collectors.deep.competitions import DeepCompetition
from odds_arb.core.deep_markets import DeepArbOpportunity, DeepMarketOdd, build_deep_market_key
from odds_arb.deep_live import LiveDeepConfig
from odds_arb.deep_web import (
    InMemorySignalStore,
    SignalSnapshot,
    _rows_for_request,
    build_combined_loop_runner,
    build_loop_runner,
    create_app,
    filter_signals,
    render_rows,
)


def _snapshot(updated: datetime) -> SignalSnapshot:
    return SignalSnapshot(
        opportunities=(),
        collected_by_house={"betano": 1, "superbet": 2},
        competition_alias="copa-do-mundo",
        updated_at=updated,
    )


def test_store_starts_empty():
    store = InMemorySignalStore()
    assert store.latest() is None


def test_store_returns_last_set_snapshot():
    store = InMemorySignalStore()
    first = _snapshot(datetime(2026, 6, 27, 12, 0, tzinfo=UTC))
    second = _snapshot(datetime(2026, 6, 27, 12, 5, tzinfo=UTC))
    store.set(first)
    store.set(second)
    assert store.latest() is second


def test_store_update_competition_publishes_before_other_competitions_finish():
    store = InMemorySignalStore()
    t1 = datetime(2026, 6, 27, 20, 0, tzinfo=UTC)
    store.update_competition(
        "copa-do-mundo",
        opportunities=(_opp("corners", "3.0"),),
        raw_odds=(),
        collected_by_house={"betano": 1},
        updated_at=t1,
    )
    snap = store.latest()
    assert snap is not None
    assert snap.competition_alias == "copa-do-mundo"
    assert len(snap.opportunities) == 1
    assert snap.updated_at == t1


def test_store_update_competition_merges_across_competitions():
    store = InMemorySignalStore()
    t1 = datetime(2026, 6, 27, 20, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 27, 20, 10, tzinfo=UTC)
    store.update_competition(
        "copa-do-mundo",
        opportunities=(_opp("corners", "3.0"),),
        raw_odds=(),
        collected_by_house={"betano": 1},
        updated_at=t1,
    )
    store.update_competition(
        "serie-b",
        opportunities=(_opp("shots", "2.0"),),
        raw_odds=(),
        collected_by_house={"betano": 1},
        updated_at=t2,
    )
    snap = store.latest()
    assert snap is not None
    assert snap.competition_alias == "copa-do-mundo,serie-b"
    assert len(snap.opportunities) == 2
    assert snap.collected_by_house["betano"] == 2
    # o timestamp global reflete a atualizacao mais recente entre as fatias
    assert snap.updated_at == t2


def test_store_update_competition_replaces_stale_slice_for_same_alias():
    store = InMemorySignalStore()
    t1 = datetime(2026, 6, 27, 20, 0, tzinfo=UTC)
    t2 = datetime(2026, 6, 27, 20, 5, tzinfo=UTC)
    store.update_competition(
        "copa-do-mundo",
        opportunities=(_opp("corners", "3.0"),),
        raw_odds=(),
        collected_by_house={"betano": 1},
        updated_at=t1,
    )
    store.update_competition(
        "copa-do-mundo",
        opportunities=(_opp("shots", "4.0"),),
        raw_odds=(),
        collected_by_house={"betano": 2},
        updated_at=t2,
    )
    snap = store.latest()
    assert snap is not None
    # a fatia antiga da copa-do-mundo eh substituida, nao acumulada
    assert len(snap.opportunities) == 1
    assert snap.opportunities[0].profit_pct == Decimal("4.0")
    assert snap.collected_by_house["betano"] == 2


def _opp(metric: str, profit: str, line: str = "9.5") -> DeepArbOpportunity:
    start = datetime(2026, 6, 27, 21, 0, tzinfo=UTC)

    def leg(house: str, side: str) -> DeepMarketOdd:
        return DeepMarketOdd(
            bookmaker=house,
            raw_event_id="e",
            raw_market_id="m",
            raw_selection_id=f"{house}{side}",
            event_name="A - B",
            home_team="Egito",
            away_team="Irã",
            start_time=start,
            period="full_time",
            market_family="match_total",
            metric=metric,
            subject=None,
            side=side,
            odd=Decimal("2.10"),
            raw_market_name="r",
            raw_selection_name="r",
            is_live=False,
            line=Decimal(line),
            line_source="selection_handicap",
            source_event_url=f"https://x/{house}",
        )

    over, under = leg("superbet", "over"), leg("betano", "under")
    return DeepArbOpportunity(
        key=build_deep_market_key(over),
        over_leg=over,
        under_leg=under,
        implied_probability_sum=Decimal("0.95"),
        profit_pct=Decimal(profit),
        detected_at=start,
    )


def test_filter_by_min_margin():
    opps = [_opp("corners", "1.0"), _opp("corners", "3.0")]
    out = filter_signals(opps, min_margin_pct=Decimal("2.0"))
    assert [o.profit_pct for o in out] == [Decimal("3.0")]


def test_filter_by_market():
    opps = [_opp("corners", "3.0"), _opp("shots", "3.0")]
    out = filter_signals(opps, market="shots")
    assert {o.key[3] for o in out} == {"shots"}


def test_filter_no_args_returns_all():
    opps = [_opp("corners", "1.0"), _opp("shots", "3.0")]
    assert len(filter_signals(opps)) == 2


def test_render_rows_recomputes_stakes_and_flags():
    rows = render_rows([_opp("throw_ins", "5.0"), _opp("corners", "1.0")], Decimal("1000"))
    assert len(rows) == 2
    throw, corner = rows[0], rows[1]
    # match label from the legs
    assert throw.match == "Egito x Irã"
    # stakes sum to bankroll
    assert throw.over_stake + throw.under_stake == Decimal("1000.00")
    # throw_ins is settlement-sensitive; corners is not
    assert throw.settlement_warning is True
    assert corner.settlement_warning is False
    # 5.0% >= 4.5% highlight; 1.0% not
    assert throw.highlight is True
    assert corner.highlight is False
    # each leg exposes its bookmaker event url
    assert throw.over_url == "https://x/superbet"
    assert throw.under_url == "https://x/betano"


_NOW = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
_FUT_MS = int((_NOW + timedelta(hours=3)).timestamp() * 1000)


def _betano(comp):  # type: ignore[no-untyped-def]
    return [
        {
            "id": 900,
            "name": "Egito - Irã",
            "startTime": _FUT_MS,
            "markets": [
                {
                    "id": 34,
                    "name": "Total de Escanteios",
                    "selections": [
                        {"id": 1, "name": "Mais de 9.5", "handicap": "9.5", "price": "2.20"},
                        {"id": 2, "name": "Menos de 9.5", "handicap": "9.5", "price": "1.85"},
                    ],
                }
            ],
        }
    ]


def _sb_detail(eid):  # type: ignore[no-untyped-def]
    iso = (_NOW + timedelta(hours=3)).isoformat()
    return {
        "eventId": 111,
        "matchName": "Egito · Irã",
        "utcDate": iso,
        "odds": [
            {
                "marketId": 50,
                "marketName": "Total de Escanteios",
                "name": "Mais de 9.5",
                "price": "2.30",
                "status": "active",
                "uuid": "o",
            },
            {
                "marketId": 50,
                "marketName": "Total de Escanteios",
                "name": "Menos de 9.5",
                "price": "1.95",
                "status": "active",
                "uuid": "u",
            },
        ],
    }


def _config(tmp_path):  # type: ignore[no-untyped-def]
    return LiveDeepConfig(
        competition=DeepCompetition(alias="copa-do-mundo", betano_url="https://b/", label="Copa"),
        min_profit_pct=Decimal("0"),
        bankroll=Decimal("1000"),
        interval_seconds=300.0,
        audit_path=tmp_path / "audit.jsonl",
    )


def test_build_loop_runner_writes_snapshot_to_store(tmp_path):  # type: ignore[no-untyped-def]
    store = InMemorySignalStore()

    async def no_sleep(_s: float) -> None:
        return None

    runner = build_loop_runner(
        store,
        _config(tmp_path),
        betano_fetcher=_betano,
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=_sb_detail,
        max_iterations=1,
        sleep=no_sleep,
    )
    asyncio.run(runner())
    snap = store.latest()
    assert snap is not None
    assert snap.competition_alias == "copa-do-mundo"
    assert len(snap.opportunities) >= 1
    assert snap.collected_by_house["betano"] > 0


def test_build_combined_loop_runner_merges_competition_snapshots(tmp_path):
    store = InMemorySignalStore()
    configs = [
        _config(tmp_path / "copa"),
        LiveDeepConfig(
            competition=DeepCompetition(alias="serie-b", betano_url="https://b2/", label="Serie B"),
            min_profit_pct=Decimal("0"),
            bankroll=Decimal("1000"),
            interval_seconds=300.0,
            audit_path=tmp_path / "serie-b" / "audit.jsonl",
        ),
    ]

    async def no_sleep(_s: float) -> None:
        return None

    printed: list[str] = []
    runner = build_combined_loop_runner(
        store,
        configs,
        betano_fetcher=_betano,
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=_sb_detail,
        max_iterations=1,
        sleep=no_sleep,
        printer=printed.append,
    )
    asyncio.run(runner())
    snap = store.latest()
    assert snap is not None
    assert snap.competition_alias == "copa-do-mundo,serie-b"
    assert len(snap.opportunities) >= 2
    assert snap.collected_by_house["betano"] >= 2
    assert any("[Copa]" in line for line in printed)
    assert any("[Serie B]" in line for line in printed)


class _RecordingStore:
    """Store double que grava a sequencia de update_competition — usado pra
    provar que o runner publica assim que CADA competicao termina, sem
    esperar as demais (em vez de um unico set() no fim do ciclo)."""

    def __init__(self) -> None:
        self.competition_calls: list[str] = []
        self._real = InMemorySignalStore()

    def set(self, snapshot):  # type: ignore[no-untyped-def]
        self._real.set(snapshot)

    def latest(self):  # type: ignore[no-untyped-def]
        return self._real.latest()

    def update_competition(self, alias, **kwargs):  # type: ignore[no-untyped-def]
        self.competition_calls.append(alias)
        self._real.update_competition(alias, **kwargs)


def test_build_combined_loop_runner_publishes_per_competition_as_it_finishes(tmp_path):
    store = _RecordingStore()
    configs = [
        _config(tmp_path / "copa"),
        LiveDeepConfig(
            competition=DeepCompetition(alias="serie-b", betano_url="https://b2/", label="Serie B"),
            min_profit_pct=Decimal("0"),
            bankroll=Decimal("1000"),
            interval_seconds=300.0,
            audit_path=tmp_path / "serie-b" / "audit.jsonl",
        ),
    ]

    async def no_sleep(_s: float) -> None:
        return None

    runner = build_combined_loop_runner(
        store,
        configs,
        betano_fetcher=_betano,
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=_sb_detail,
        max_iterations=1,
        sleep=no_sleep,
    )
    asyncio.run(runner())
    # duas publicacoes distintas — uma por competicao — nao um unico set() no final
    assert store.competition_calls == ["copa-do-mundo", "serie-b"]
    snap = store.latest()
    assert snap is not None
    assert snap.competition_alias == "copa-do-mundo,serie-b"
    assert len(snap.opportunities) >= 2


def _seeded_store(tmp_path):  # type: ignore[no-untyped-def]
    store = InMemorySignalStore()
    store.set(
        SignalSnapshot(
            opportunities=(_opp("throw_ins", "5.0"), _opp("corners", "1.0")),
            collected_by_house={"betano": 3, "superbet": 4},
            competition_alias="copa-do-mundo",
            updated_at=_NOW,
        )
    )
    return store


def test_api_signals_returns_filtered_rows(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/signals", params={"min_arb": "2.0", "bankroll": "1000"})
    assert resp.status_code == 200
    data = resp.json()
    # only the 5.0% throw_ins passes the 2.0% filter
    assert len(data["signals"]) == 1
    sig = data["signals"][0]
    assert sig["metric"] == "throw_ins"
    assert sig["settlement_warning"] is True
    assert sig["over_url"] == "https://x/superbet"
    assert str(sig["over_stake"])
    assert str(sig["under_stake"])


def test_api_signals_market_filter(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/signals", params={"market": "corners"})
    assert [s["metric"] for s in resp.json()["signals"]] == ["corners"]


def test_health_reports_age(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    client = TestClient(app)
    body = client.get("/health").json()
    assert body["signals"] == 2
    assert body["updated_at"] is not None


def test_health_empty_store():  # type: ignore[no-untyped-def]
    app = create_app(InMemorySignalStore())
    body = TestClient(app).get("/health").json()
    assert body["signals"] == 0
    assert body["updated_at"] is None


def test_index_renders_cards_with_links(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    client = TestClient(app)
    html = client.get("/", params={"bankroll": "1000"}).text
    assert "Egito x Irã" in html
    assert "throw_ins" in html
    # house deep-links present
    assert "https://x/superbet" in html
    assert "https://x/betano" in html
    # settlement warning surfaced for throw_ins
    assert "conferir settlement" in html


def test_index_warming_state_when_empty():  # type: ignore[no-untyped-def]
    app = create_app(InMemorySignalStore())
    html = TestClient(app).get("/").text
    assert "aquecendo" in html.lower()


def test_api_signals_tolerates_malformed_min_arb(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/signals", params={"min_arb": "abc"})
    assert resp.status_code == 200
    # invalid min_arb falls back to 0 -> both seeded signals returned
    assert len(resp.json()["signals"]) == 2


def test_api_signals_tolerates_nonpositive_bankroll(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/signals", params={"bankroll": "0"})
    assert resp.status_code == 200
    sig = resp.json()["signals"][0]
    # bankroll<=0 falls back to 1000 -> stakes sum to 1000.00
    from decimal import Decimal

    assert Decimal(sig["over_stake"]) + Decimal(sig["under_stake"]) == Decimal("1000.00")


def test_index_tolerates_malformed_bankroll(tmp_path):  # type: ignore[no-untyped-def]
    app = create_app(_seeded_store(tmp_path))
    html = TestClient(app).get("/", params={"bankroll": "xyz"}).text
    assert "Egito x Irã" in html  # renders fine with default bankroll


def test_default_min_arb_applies_when_query_absent():
    store = InMemorySignalStore()
    store.set(
        SignalSnapshot(
            opportunities=(_opp("corners", "1.5"), _opp("shots", "3.0")),
            collected_by_house={},
            competition_alias="copa-do-mundo",
            updated_at=datetime.now(UTC),
        )
    )
    client = TestClient(create_app(store, default_min_arb="2"))
    # no min_arb -> falls back to default "2" -> only the 3.0% signal
    assert len(client.get("/api/signals").json()["signals"]) == 1
    # explicit min_arb=1 overrides the default -> both signals
    assert len(client.get("/api/signals", params={"min_arb": "1"}).json()["signals"]) == 2


def test_index_lays_out_signals_in_a_grid(tmp_path):
    app = create_app(_seeded_store(tmp_path))
    html = TestClient(app).get("/").text
    assert 'class="grid"' in html


def test_render_rows_includes_kickoff_in_brt():
    # _opp legs start at 2026-06-27 21:00 UTC -> 18:00 BRT (UTC-3)
    rows = render_rows([_opp("corners", "2.0")], Decimal("1000"))
    assert rows[0].kickoff == "27/06 18:00"


def test_index_shows_line_badge_and_kickoff(tmp_path):
    html = TestClient(create_app(_seeded_store(tmp_path))).get("/").text
    assert "line-badge" in html
    assert "kickoff" in html


def test_empty_market_means_all_not_none(tmp_path):
    # The form's "Todos" option submits market="" — must behave like no filter,
    # not wipe every signal (regression).
    client = TestClient(create_app(_seeded_store(tmp_path)))
    n_empty = len(client.get("/api/signals", params={"market": ""}).json()["signals"])
    n_omitted = len(client.get("/api/signals").json()["signals"])
    assert n_empty == n_omitted == 2


def test_index_market_filter_is_a_select_with_options(tmp_path):
    html = TestClient(create_app(_seeded_store(tmp_path))).get("/").text
    assert "<select" in html
    assert 'name="market"' in html
    assert "Todos os mercados" in html
    assert "Escanteios" in html


def test_render_rows_humanizes_model():
    row = render_rows([_opp("throw_ins", "2.0")], Decimal("1000"))[0]
    assert row.metric_label == "Laterais"
    assert row.scope_label == "na partida"  # _opp builds a match_total
    assert row.subject is None
    assert row.period_label == "Tempo integral"


def test_render_rows_team_total_keeps_display_subject():
    from odds_arb.core.deep_markets import (
        DeepArbOpportunity,
        DeepMarketOdd,
        build_deep_market_key,
    )

    start = datetime(2026, 6, 27, 21, 0, tzinfo=UTC)

    def leg(house: str, side: str) -> DeepMarketOdd:
        return DeepMarketOdd(
            bookmaker=house,
            raw_event_id="e",
            raw_market_id="m",
            raw_selection_id=f"{house}{side}",
            event_name="A - B",
            home_team="Paraguai",
            away_team="Austrália",
            start_time=start,
            period="full_time",
            market_family="team_total",
            metric="throw_ins",
            subject="Paraguai",
            side=side,
            odd=Decimal("2.1"),
            raw_market_name="r",
            raw_selection_name="r",
            is_live=False,
            line=Decimal("4.5"),
            line_source="selection_handicap",
            source_event_url="https://x",
        )

    ov, un = leg("superbet", "over"), leg("betano", "under")
    opp = DeepArbOpportunity(
        key=build_deep_market_key(ov),
        over_leg=ov,
        under_leg=un,
        implied_probability_sum=Decimal("0.95"),
        profit_pct=Decimal("3.0"),
        detected_at=start,
    )
    row = render_rows([opp], Decimal("1000"))[0]
    assert row.metric_label == "Laterais"
    assert row.scope_label == "por time"
    assert row.subject == "Paraguai"  # proper case, not canonical


def test_index_shows_humanized_model(tmp_path):
    html = TestClient(create_app(_seeded_store(tmp_path))).get("/").text
    assert "Laterais" in html
    assert "na partida" in html
    assert "Tempo integral" in html


def test_render_rows_has_stable_signal_id():
    rows = render_rows([_opp("throw_ins", "5.0"), _opp("corners", "1.0")], Decimal("1000"))
    ids = [r.signal_id for r in rows]
    assert all(ids)
    assert len(set(ids)) == 2
    again = render_rows([_opp("throw_ins", "5.0")], Decimal("1000"))[0].signal_id
    assert again == rows[0].signal_id  # stable for the same signal


def test_api_signals_includes_signal_id(tmp_path):
    data = TestClient(create_app(_seeded_store(tmp_path))).get("/api/signals").json()
    assert all(s.get("signal_id") for s in data["signals"])


def test_index_includes_notify_button_and_threshold(tmp_path):
    html = (
        TestClient(create_app(_seeded_store(tmp_path), notify_profit_pct=Decimal("8")))
        .get("/")
        .text
    )
    assert "notify-btn" in html
    assert "Notification" in html  # the browser-notification script
    assert "alertas ≥8%" in html


def test_index_notify_threshold_is_configurable(tmp_path):
    html = (
        TestClient(create_app(_seeded_store(tmp_path), notify_profit_pct=Decimal("5")))
        .get("/")
        .text
    )
    assert "alertas ≥5%" in html


def _deep_odd(house: str, side: str, odd: str) -> DeepMarketOdd:
    return DeepMarketOdd(
        bookmaker=house,
        raw_event_id="e",
        raw_market_id="m",
        raw_selection_id=f"{house}{side}",
        event_name="A - B",
        home_team="Time A",
        away_team="Time B",
        start_time=_NOW + timedelta(hours=3),
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


def test_excluded_bookmaker_recomputes_next_best_pair():
    # Sem veto: Superbet 2.25 + Sportingbet 2.10 e o melhor par (menor soma de
    # probabilidades implicitas). Com Sportingbet vetada, o proximo melhor par
    # elegivel e Superbet + Novibet, nao o desaparecimento do sinal.
    over = _deep_odd("superbet", "over", "2.25")
    under_sportingbet = _deep_odd("sportingbet", "under", "2.10")
    under_novibet = _deep_odd("novibet", "under", "2.05")
    snapshot = SignalSnapshot(
        opportunities=(),
        collected_by_house={},
        competition_alias="copa-do-mundo",
        updated_at=_NOW,
        raw_odds=(over, under_sportingbet, under_novibet),
    )

    baseline = _rows_for_request(
        snapshot, competition=None, min_arb="0", market=None, bankroll="1000"
    )
    # no exclusion -> recomputed from raw_odds too; best pair is superbet+sportingbet
    assert len(baseline) == 1
    assert baseline[0].under_bookmaker == "sportingbet"

    without_sportingbet = _rows_for_request(
        snapshot,
        competition=None,
        min_arb="0",
        market=None,
        bankroll="1000",
        excluded_bookmakers=frozenset({"sportingbet"}),
    )
    assert len(without_sportingbet) == 1
    row = without_sportingbet[0]
    assert row.over_bookmaker == "superbet"
    assert row.under_bookmaker == "novibet"


def test_excluding_all_bookmakers_of_a_pair_yields_no_signal():
    over = _deep_odd("superbet", "over", "2.25")
    under = _deep_odd("sportingbet", "under", "2.10")
    snapshot = SignalSnapshot(
        opportunities=(),
        collected_by_house={},
        competition_alias="copa-do-mundo",
        updated_at=_NOW,
        raw_odds=(over, under),
    )
    rows = _rows_for_request(
        snapshot,
        competition=None,
        min_arb="0",
        market=None,
        bankroll="1000",
        excluded_bookmakers=frozenset({"sportingbet"}),
    )
    assert rows == []


def test_excluded_bookmakers_apply_to_tennis_raw_odds_too():
    from odds_arb.core.tennis_markets import Side, TennisMarket, TennisMarketOdd

    start = _NOW + timedelta(hours=3)

    def leg(house: str, side: Side, odd: str) -> TennisMarketOdd:
        return TennisMarketOdd(
            bookmaker=house,
            raw_event_id="e",
            raw_market_id="m",
            raw_selection_id=f"{house}{side.value}",
            event_name="Jogador A x Jogador B",
            player_a="Jogador A",
            player_b="Jogador B",
            start_time=start,
            market=TennisMarket.MATCH_TOTAL_GAMES,
            side=side,
            winner_player=None,
            odd=Decimal(odd),
            raw_market_name="r",
            raw_selection_name="r",
            is_live=False,
            line=Decimal("21.5"),
            source_event_url=f"https://x/{house}",
        )

    over = leg("superbet", Side.OVER, "2.25")
    under_sportingbet_equivalent = leg("betano", Side.UNDER, "2.10")
    under_novibet = leg("novibet", Side.UNDER, "2.05")
    snapshot = SignalSnapshot(
        opportunities=(),
        collected_by_house={},
        competition_alias="copa-do-mundo",
        updated_at=_NOW,
        tennis_raw_odds=(over, under_sportingbet_equivalent, under_novibet),
    )
    rows = _rows_for_request(
        snapshot,
        competition=None,
        min_arb="0",
        market=None,
        bankroll="1000",
        excluded_bookmakers=frozenset({"betano"}),
    )
    assert len(rows) == 1
    assert rows[0].over_bookmaker == "superbet"
    assert rows[0].under_bookmaker == "novibet"
