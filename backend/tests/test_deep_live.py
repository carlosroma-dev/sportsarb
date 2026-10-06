# tests/test_deep_live.py
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.competitions import DeepCompetition
from odds_arb.deep_live import (
    LiveDeepConfig,
    LiveDeepCycleReport,
    format_cycle_report,
    run_live_deep_cycle,
    run_live_deep_loop,
)

NOW = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
FUTURE_MS = int((NOW + timedelta(hours=3)).timestamp() * 1000)
PAST_MS = int((NOW - timedelta(hours=1)).timestamp() * 1000)


def _betano_event(name: str, start_ms: int) -> dict:
    # A match-total corners market the normalizer accepts (over leg).
    return {
        "id": 900,
        "name": name,
        "startTime": start_ms,
        "markets": [
            {
                "id": 34,
                "name": "Total de Escanteios",
                "selections": [
                    {"id": 1, "name": "Mais de 9.5", "handicap": "9.5", "price": "2.10"},
                    {"id": 2, "name": "Menos de 9.5", "handicap": "9.5", "price": "1.85"},
                ],
            }
        ],
    }


def _superbet_detail(event_id: int, match_name: str, start_iso: str) -> dict:
    return {
        "data": [
            {
                "eventId": event_id,
                "matchName": match_name,
                "utcDate": start_iso,
                "odds": [
                    {
                        "marketId": 50,
                        "marketName": "Total de Escanteios",
                        "name": "Mais de 9.5",
                        "price": "2.20",
                        "status": "active",
                        "uuid": "sb-over",
                    },
                    {
                        "marketId": 50,
                        "marketName": "Total de Escanteios",
                        "name": "Menos de 9.5",
                        "price": "1.95",
                        "status": "active",
                        "uuid": "sb-under",
                    },
                ],
            }
        ]
    }


def _sportingbet_detail(fixture_id: str, match_name: str, start_iso: str) -> dict:
    home, away = match_name.split(" - ", 1)
    return {
        "id": fixture_id,
        "name": {"value": match_name},
        "startDate": start_iso,
        "stage": "Prematch",
        "isOpenForBetting": True,
        "participants": [
            {"name": {"value": home}, "properties": {"type": "HomeTeam"}},
            {"name": {"value": away}, "properties": {"type": "AwayTeam"}},
        ],
        "optionMarkets": [
            {
                "id": "sb-corners",
                "name": {"value": "Total de Escanteios"},
                "parameters": [
                    {"key": "DecimalValue", "value": "9.5000"},
                    {"key": "Happening", "value": "Corner"},
                    {"key": "MarketType", "value": "Over/Under"},
                    {"key": "Period", "value": "RegularTime"},
                ],
                "options": [
                    {
                        "id": "sb-over",
                        "status": "Visible",
                        "name": {"value": "Mais de 9.5"},
                        "price": {"odds": 1.80},
                        "parameters": [{"key": "optionTypes", "value": ["Over"]}],
                    },
                    {
                        "id": "sb-under",
                        "status": "Visible",
                        "name": {"value": "Menos de 9.5"},
                        "price": {"odds": 2.20},
                        "parameters": [{"key": "optionTypes", "value": ["Under"]}],
                    },
                ],
            }
        ],
    }


def _kto_detail(event_id: str, match_name: str, start_iso: str) -> dict:
    home, away = match_name.split(" - ", 1)
    return {
        "events": [
            {
                "id": event_id,
                "name": match_name,
                "homeName": home,
                "awayName": away,
                "start": start_iso,
                "group": "Copa Teste",
                "state": "NOT_STARTED",
            }
        ],
        "betOffers": [
            {
                "id": 70,
                "criterion": {"label": "Total de Escanteios"},
                "betOfferType": {"name": "Mais de/Menos de", "englishName": "Over/Under"},
                "outcomes": [
                    {
                        "id": 701,
                        "label": "Mais",
                        "englishLabel": "Over",
                        "type": "OT_OVER",
                        "line": 9500,
                        "odds": 1800,
                        "status": "OPEN",
                    },
                    {
                        "id": 702,
                        "label": "Menos",
                        "englishLabel": "Under",
                        "type": "OT_UNDER",
                        "line": 9500,
                        "odds": 2200,
                        "status": "OPEN",
                    },
                ],
            }
        ],
    }


def _estrelabet_detail(event_id: str, match_name: str, start_iso: str) -> dict:
    home, away = match_name.split(" - ", 1)
    return {
        "id": event_id,
        "name": match_name.replace(" - ", " vs. "),
        "startDate": start_iso,
        "et": 0,
        "rc": False,
        "champ": {"id": 3146, "name": "Copa Teste"},
        "category": {"id": 1134, "name": "Mundo"},
        "sport": {"id": 66, "name": "Futebol", "iconName": "soccer"},
        "competitors": [
            {"id": 1, "name": home},
            {"id": 2, "name": away},
        ],
        "markets": [
            {
                "id": 80,
                "name": "Total de Escanteios",
                "typeId": 166,
                "sv": "9.5",
                "desktopOddIds": [[801], [802]],
            }
        ],
        "odds": [
            {
                "id": 801,
                "name": "Mais de 9.5",
                "typeId": 12,
                "price": 1.8,
                "oddStatus": 0,
                "sv": "9.5",
            },
            {
                "id": 802,
                "name": "Menos de 9.5",
                "typeId": 13,
                "price": 2.2,
                "oddStatus": 0,
                "sv": "9.5",
            },
        ],
    }


def _novibet_event(match_name: str, start_iso: str) -> dict:
    home, away = match_name.split(" - ", 1)
    return {
        "eventBetContextId": 999,
        "competitionCaption": "Copa Teste",
        "regionCaption": "Mundo",
        "startDateTime": start_iso,
        "isLive": False,
        "path": "matches/egito-ira",
        "additionalCaptions": {
            "competitor1": home,
            "competitor2": away,
        },
        "markets": [
            {
                "marketId": 90,
                "betTypeSysname": "SOCCER_CORNERS_UNDER_OVER",
                "betItems": [
                    {
                        "id": 901,
                        "caption": "Mais de 9,5",
                        "code": "O",
                        "price": 1.80,
                        "isAvailable": True,
                    },
                    {
                        "id": 902,
                        "caption": "Menos de 9,5",
                        "code": "U",
                        "price": 2.20,
                        "isAvailable": True,
                    },
                ],
            }
        ],
    }


def _config(tmp_path: Path, min_arb: str = "0") -> LiveDeepConfig:
    return LiveDeepConfig(
        competition=DeepCompetition(alias="t", betano_url="https://b/", label="T"),
        min_profit_pct=Decimal(min_arb),
        bankroll=Decimal("1000"),
        interval_seconds=300.0,
        audit_path=tmp_path / "audit.jsonl",
    )


def test_cycle_detects_cross_house_arb_with_stakes(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Irã", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=lambda eid: _superbet_detail(111, "Egito · Irã", future_iso)[
            "data"
        ][0],
        now=NOW,
    )
    assert len(report.opportunities) >= 1
    opp = report.opportunities[0]
    # Best cross-house over=Superbet 2.20, under=Betano 1.85 -> implied < 1.
    assert {opp.opportunity.over_leg.bookmaker, opp.opportunity.under_leg.bookmaker} == {
        "betano",
        "superbet",
    }
    assert sum(opp.stakes.values()) == Decimal("1000.00")
    assert config_audit_exists(tmp_path)  # audit file written


def test_cycle_detects_sportingbet_cross_house_arb(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Ira", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {"data": []},
        superbet_detail_fetcher=lambda eid: None,
        sportingbet_list_fetcher=lambda: {
            "fixtures": [{"id": "sb-1", "name": {"value": "Egito - Ira"}}]
        },
        sportingbet_detail_fetcher=lambda fixture_id: _sportingbet_detail(
            fixture_id, "Egito - Ira", future_iso
        ),
        now=NOW,
    )

    assert len(report.opportunities) >= 1
    opp = report.opportunities[0].opportunity
    assert {opp.over_leg.bookmaker, opp.under_leg.bookmaker} == {"betano", "sportingbet"}
    assert report.collected_by_house["sportingbet"] > 0


def test_cycle_detects_kto_cross_house_arb(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Ira", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {"data": []},
        superbet_detail_fetcher=lambda eid: None,
        kto_list_fetcher=lambda: {
            "events": [
                {
                    "event": {
                        "id": 777,
                        "name": "Egito - Ira",
                        "homeName": "Egito",
                        "awayName": "Ira",
                        "group": "Copa Teste",
                    }
                }
            ]
        },
        kto_detail_fetcher=lambda event_id: _kto_detail(event_id, "Egito - Ira", future_iso),
        now=NOW,
    )

    assert len(report.opportunities) >= 1
    opp = report.opportunities[0].opportunity
    assert {opp.over_leg.bookmaker, opp.under_leg.bookmaker} == {"betano", "kto"}
    assert report.collected_by_house["kto"] > 0


def test_cycle_detects_estrelabet_cross_house_arb(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Ira", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {"data": []},
        superbet_detail_fetcher=lambda eid: None,
        estrelabet_list_fetcher=lambda: {
            "events": [
                {
                    "id": 888,
                    "name": "Egito vs. Ira",
                    "sportId": 66,
                    "status": 0,
                    "et": 0,
                    "competitorIds": [1, 2],
                }
            ],
            "competitors": [{"id": 1, "name": "Egito"}, {"id": 2, "name": "Ira"}],
        },
        estrelabet_detail_fetcher=lambda event_id: _estrelabet_detail(
            event_id, "Egito - Ira", future_iso
        ),
        now=NOW,
    )

    assert len(report.opportunities) >= 1
    opp = report.opportunities[0].opportunity
    assert {opp.over_leg.bookmaker, opp.under_leg.bookmaker} == {"betano", "estrelabet"}
    assert report.collected_by_house["estrelabet"] > 0


def test_cycle_detects_novibet_cross_house_arb(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Ira", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {"data": []},
        superbet_detail_fetcher=lambda eid: None,
        novibet_list_fetcher=lambda: [
            {"betViews": [{"items": [_novibet_event("Egito - Ira", future_iso)]}]}
        ],
        now=NOW,
    )

    assert len(report.opportunities) >= 1
    opp = report.opportunities[0].opportunity
    assert {opp.over_leg.bookmaker, opp.under_leg.bookmaker} == {"betano", "novibet"}
    assert report.collected_by_house["novibet"] > 0


def test_cycle_uses_novibet_detail_aliases_when_available(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    calls: list[tuple[str, str]] = []

    def detail_fetcher(event_id: str, alias: str) -> dict:
        calls.append((event_id, alias))
        if alias != "STATS":
            return _novibet_event("Egito - Ira", future_iso) | {"markets": []}
        return _novibet_event("Egito - Ira", future_iso) | {
            "markets": [
                {
                    "marketId": 91,
                    "betTypeSysname": "SOCCER_GOALKICKS_UNDER_OVER",
                    "betItems": [
                        {
                            "id": 911,
                            "caption": "Mais de 17,5",
                            "code": "O",
                            "price": 1.80,
                            "isAvailable": True,
                        },
                        {
                            "id": 912,
                            "caption": "Menos de 17,5",
                            "code": "U",
                            "price": 2.20,
                            "isAvailable": True,
                        },
                    ],
                }
            ]
        }

    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [
            {
                "id": 900,
                "name": "Egito - Ira",
                "startTime": FUTURE_MS,
                "markets": [
                    {
                        "id": 35,
                        "name": "Total de Tiros de Meta",
                        "selections": [
                            {
                                "id": 1,
                                "name": "Mais de 17.5",
                                "handicap": "17.5",
                                "price": "2.10",
                            },
                            {
                                "id": 2,
                                "name": "Menos de 17.5",
                                "handicap": "17.5",
                                "price": "1.85",
                            },
                        ],
                    }
                ],
            }
        ],
        superbet_list_fetcher=lambda s, e: {"data": []},
        superbet_detail_fetcher=lambda eid: None,
        novibet_list_fetcher=lambda: [
            {"betViews": [{"items": [_novibet_event("Egito - Ira", future_iso) | {"markets": []}]}]}
        ],
        novibet_detail_fetcher=detail_fetcher,
        now=NOW,
    )

    assert ("999", "CORNERS") in calls
    assert ("999", "STATS") in calls
    assert report.collected_by_house["novibet"] == 2
    assert len(report.opportunities) >= 1


def config_audit_exists(tmp_path: Path) -> bool:
    return (tmp_path / "audit.jsonl").exists()


def test_cycle_drops_started_event(tmp_path):
    past_iso = (NOW - timedelta(hours=1)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Irã", PAST_MS)],
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=lambda eid: _superbet_detail(111, "Egito · Irã", past_iso)["data"][
            0
        ],
        now=NOW,
    )
    assert report.opportunities == []
    assert report.fresh_odds == 0
    assert report.dropped_started > 0


def test_no_audit_file_when_no_opportunities(tmp_path):
    # A cycle that drops everything (started event) writes no audit file.
    past_iso = (NOW - timedelta(hours=1)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Irã", PAST_MS)],
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=lambda eid: _superbet_detail(111, "Egito · Irã", past_iso)["data"][
            0
        ],
        now=NOW,
    )
    assert report.opportunities == []
    assert not (tmp_path / "audit.jsonl").exists()


def test_cycle_survives_superbet_failure(tmp_path):
    def boom(start, end):
        raise RuntimeError("superbet down")

    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_event("Egito - Irã", FUTURE_MS)],
        superbet_list_fetcher=boom,
        superbet_detail_fetcher=lambda eid: None,
        now=NOW,
    )
    assert report.opportunities == []
    assert report.collected_by_house["betano"] > 0
    assert report.collected_by_house["superbet"] == 0


def test_cycle_survives_betano_failure(tmp_path):
    def boom(comp):
        raise RuntimeError("betano down")

    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=boom,
        superbet_list_fetcher=lambda s, e: {"data": []},
        superbet_detail_fetcher=lambda eid: None,
        now=NOW,
    )
    assert report.opportunities == []
    assert report.collected_by_house["betano"] == 0


def _fixture_fetchers():
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    return {
        "betano_fetcher": lambda comp: [_betano_event("Egito - Irã", FUTURE_MS)],
        "superbet_list_fetcher": lambda s, e: {
            "data": [{"eventId": 111, "matchName": "Egito · Irã"}]
        },
        "superbet_detail_fetcher": lambda eid: _superbet_detail(111, "Egito · Irã", future_iso)[
            "data"
        ][0],
    }


def test_loop_runs_exactly_max_iterations_and_sleeps_between(tmp_path):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    reports = asyncio.run(
        run_live_deep_loop(
            _config(tmp_path),
            **_fixture_fetchers(),
            printer=lambda _msg: None,
            now_fn=lambda: NOW,
            sleep=fake_sleep,
            max_iterations=3,
        )
    )
    assert len(reports) == 3
    assert sleeps == [300.0, 300.0]  # between the 3 cycles, not after the last


def test_loop_isolates_iteration_errors(tmp_path):
    state = {"n": 0}

    def now_fn() -> datetime:
        state["n"] += 1
        if state["n"] == 2:
            raise RuntimeError("transient boom")
        return NOW

    async def fake_sleep(_seconds: float) -> None:
        return None

    reports = asyncio.run(
        run_live_deep_loop(
            _config(tmp_path),
            **_fixture_fetchers(),
            printer=lambda _msg: None,
            now_fn=now_fn,
            sleep=fake_sleep,
            max_iterations=3,
        )
    )
    assert len(reports) == 2  # iteration 2 raised before producing a report


def _betano_shots_event(name: str, start_ms: int) -> dict:
    # A match-total shots market the normalizer accepts (one over/under line).
    return {
        "id": 901,
        "name": name,
        "startTime": start_ms,
        "markets": [
            {
                "id": 4159,
                "name": "Total de chutes",
                "selections": [
                    {"id": 11, "name": "Mais de 25.5", "handicap": "25.5", "price": "1.95"},
                    {"id": 12, "name": "Menos de 25.5", "handicap": "25.5", "price": "2.05"},
                ],
            }
        ],
    }


def _superbet_shots_detail(event_id: int, match_name: str, start_iso: str) -> dict:
    return {
        "eventId": event_id,
        "matchName": match_name,
        "utcDate": start_iso,
        "odds": [
            {
                "marketId": 70,
                "marketName": "Finalizações",
                "name": "Mais de 25.5",
                "price": "2.10",
                "status": "active",
                "uuid": "sb-shots-over",
            },
            {
                "marketId": 70,
                "marketName": "Finalizações",
                "name": "Menos de 25.5",
                "price": "1.90",
                "status": "active",
                "uuid": "sb-shots-under",
            },
        ],
    }


def test_cycle_detects_shots_arb_cross_house(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_shots_event("Equador - Alemanha", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {
            "data": [{"eventId": 222, "matchName": "Equador · Alemanha"}]
        },
        superbet_detail_fetcher=lambda eid: _superbet_shots_detail(
            222, "Equador · Alemanha", future_iso
        ),
        now=NOW,
    )
    assert len(report.opportunities) >= 1
    opp = report.opportunities[0].opportunity
    _match, _period, _family, metric, _subject, line = opp.key
    assert metric == "shots"
    assert line == "25.5"
    # Best cross-house: over=Superbet 2.10, under=Betano 2.05 -> implied < 1.
    assert {opp.over_leg.bookmaker, opp.under_leg.bookmaker} == {"betano", "superbet"}


def test_format_cycle_report_includes_profit_and_stakes(tmp_path):
    report = run_live_deep_cycle(
        _config(tmp_path),
        **_fixture_fetchers(),
        now=NOW,
    )
    comp = DeepCompetition(alias="t", betano_url="https://b/", label="Copa Teste")
    text = format_cycle_report(report, comp)
    assert "Copa Teste" in text
    assert "%" in text
    # Every opportunity line names the match, so match_total markets (cards/
    # corners, subject "partida") are still identifiable in the terminal.
    assert "Egito x" in text


def test_format_cycle_report_uppercases_high_margin(tmp_path):
    # A >=4.5% margin renders the whole line in CAPS, framed with ***.
    future_iso = (NOW + timedelta(hours=3)).isoformat()

    def betano(comp):
        return [
            {
                "id": 900,
                "name": "Egito - Irã",
                "startTime": FUTURE_MS,
                "markets": [
                    {
                        "id": 34,
                        "name": "Total de Escanteios",
                        "selections": [
                            {"id": 1, "name": "Mais de 9.5", "handicap": "9.5", "price": "2.30"},
                            {"id": 2, "name": "Menos de 9.5", "handicap": "9.5", "price": "2.30"},
                        ],
                    }
                ],
            }
        ]

    sb_detail = {
        "eventId": 111,
        "matchName": "Egito · Irã",
        "utcDate": future_iso,
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
                "price": "2.30",
                "status": "active",
                "uuid": "u",
            },
        ],
    }

    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=betano,
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 111, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=lambda eid: sb_detail,
        now=NOW,
    )
    comp = DeepCompetition(alias="t", betano_url="https://b/", label="T")
    text = format_cycle_report(report, comp)
    # ~15% margin (over 2.30 / under 2.30) -> framed and uppercased.
    assert "*** " in text
    assert "EGITO X IRÃ" in text


def _staked_for_metric(metric_market_name: str, subject: str, profit: str):
    from odds_arb.core.deep_markets import (
        DeepArbOpportunity,
        DeepMarketOdd,
        build_deep_market_key,
    )
    from odds_arb.deep_live import StakedOpportunity

    start = NOW + timedelta(hours=3)

    def leg(house: str, side: str) -> DeepMarketOdd:
        return DeepMarketOdd(
            bookmaker=house,
            raw_event_id="e",
            raw_market_id="m",
            raw_selection_id=f"{house}{side}",
            event_name="A - B",
            home_team="França",
            away_team="Noruega",
            start_time=start,
            period="full_time",
            market_family="team_total",
            metric=metric_market_name,
            subject=subject,
            side=side,
            odd=Decimal("2.20"),
            raw_market_name="r",
            raw_selection_name="r",
            is_live=False,
            line=Decimal("9.5"),
            line_source="selection_handicap",
        )

    over, under = leg("superbet", "over"), leg("betano", "under")
    opp = DeepArbOpportunity(
        key=build_deep_market_key(over),
        over_leg=over,
        under_leg=under,
        implied_probability_sum=Decimal("0.90"),
        profit_pct=Decimal(profit),
        detected_at=NOW,
    )
    return StakedOpportunity(
        opportunity=opp, stakes={"over": Decimal("500.00"), "under": Decimal("500.00")}
    )


def test_format_cycle_report_flags_settlement_sensitive_metrics(tmp_path):
    report = LiveDeepCycleReport(
        collected_by_house={"betano": 1, "superbet": 1},
        fresh_odds=4,
        dropped_started=0,
        opportunities=[
            _staked_for_metric("tackles", "França", "2.0"),
            _staked_for_metric("fouls", "Noruega", "2.0"),
        ],
    )
    comp = DeepCompetition(alias="t", betano_url="https://b/", label="T")
    text = format_cycle_report(report, comp)
    lines = text.splitlines()
    tackles_line = next(line for line in lines if "tackles" in line)
    fouls_line = next(line for line in lines if "fouls" in line)
    assert "conferir settlement" in tackles_line
    assert "conferir settlement" not in fouls_line


def test_format_cycle_report_separates_opportunities_with_blank_line(tmp_path):
    report = LiveDeepCycleReport(
        collected_by_house={"betano": 1, "superbet": 1},
        fresh_odds=4,
        dropped_started=0,
        opportunities=[
            _staked_for_metric("fouls", "França", "2.0"),
            _staked_for_metric("fouls", "Noruega", "2.0"),
        ],
    )
    comp = DeepCompetition(alias="t", betano_url="https://b/", label="T")
    text = format_cycle_report(report, comp)
    lines = text.split("\n")
    # Every opportunity line is preceded by a blank line for readability.
    opp_indices = [i for i, ln in enumerate(lines) if ln.lstrip().startswith("*")]
    assert len(opp_indices) == 2
    for i in opp_indices:
        assert lines[i - 1] == ""


def _betano_fouls_event(name: str, start_ms: int) -> dict:
    return {
        "id": 902,
        "name": name,
        "startTime": start_ms,
        "markets": [
            {
                "id": 4629,
                "name": "Total de Faltas",
                "selections": [
                    {"id": 21, "name": "Mais de 22.5", "handicap": "22.5", "price": "1.95"},
                    {"id": 22, "name": "Menos de 22.5", "handicap": "22.5", "price": "2.05"},
                ],
            }
        ],
    }


def _superbet_fouls_detail(event_id: int, match_name: str, start_iso: str) -> dict:
    return {
        "eventId": event_id,
        "matchName": match_name,
        "utcDate": start_iso,
        "odds": [
            {
                "marketId": 80,
                "marketName": "Total de Faltas",
                "name": "Mais de 22.5",
                "price": "2.10",
                "status": "active",
                "uuid": "sb-fouls-over",
            },
            {
                "marketId": 80,
                "marketName": "Total de Faltas",
                "name": "Menos de 22.5",
                "price": "1.90",
                "status": "active",
                "uuid": "sb-fouls-under",
            },
        ],
    }


def test_cycle_detects_fouls_arb_cross_house(tmp_path):
    future_iso = (NOW + timedelta(hours=3)).isoformat()
    report = run_live_deep_cycle(
        _config(tmp_path),
        betano_fetcher=lambda comp: [_betano_fouls_event("Egito - Irã", FUTURE_MS)],
        superbet_list_fetcher=lambda s, e: {"data": [{"eventId": 333, "matchName": "Egito · Irã"}]},
        superbet_detail_fetcher=lambda eid: _superbet_fouls_detail(333, "Egito · Irã", future_iso),
        now=NOW,
    )
    assert len(report.opportunities) >= 1
    opp = report.opportunities[0].opportunity
    _match, _period, _family, metric, _subject, line = opp.key
    assert metric == "fouls"
    assert line == "22.5"
    assert {opp.over_leg.bookmaker, opp.under_leg.bookmaker} == {"betano", "superbet"}


def test_loop_invokes_on_cycle_and_isolates_callback_errors(tmp_path):
    seen: list[int] = []

    def on_cycle(report) -> None:
        seen.append(report.collected_by_house.get("betano", 0))
        raise RuntimeError("callback boom")  # must NOT stop the loop

    async def fake_sleep(_seconds: float) -> None:
        return None

    reports = asyncio.run(
        run_live_deep_loop(
            _config(tmp_path),
            **_fixture_fetchers(),
            printer=lambda _m: None,
            now_fn=lambda: NOW,
            sleep=fake_sleep,
            max_iterations=2,
            on_cycle=on_cycle,
        )
    )
    assert len(reports) == 2  # loop survived the callback raising
    assert len(seen) == 2  # callback was invoked each cycle
