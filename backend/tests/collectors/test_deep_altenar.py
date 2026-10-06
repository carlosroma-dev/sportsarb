import json
from pathlib import Path

from odds_arb.collectors.deep.altenar_deep import parse_estrelabet_deep_event
from odds_arb.core.deep_markets import (
    LineSource,
    MarketFamily,
    Metric,
    Period,
    RejectReason,
    Side,
)


def _fixture() -> dict:
    return json.loads(
        Path("tests/fixtures/deep_markets/altenar_event_detail_sample.json").read_text(
            encoding="utf-8"
        )
    )


def test_parse_estrelabet_deep_event_accepts_match_and_team_totals() -> None:
    results = parse_estrelabet_deep_event(_fixture())
    odds = [r.odd for r in results if r.accepted and r.odd is not None]

    assert odds
    assert all(odd.bookmaker == "estrelabet" for odd in odds)
    assert all(odd.line_source == LineSource.SELECTION_HANDICAP for odd in odds)
    assert all(
        odd.source_event_url == "https://www.estrelabet.bet.br/aposta-esportiva" for odd in odds
    )

    keys = {
        (odd.metric, odd.market_family, odd.subject, odd.side, str(odd.line), odd.period)
        for odd in odds
    }
    assert (
        Metric.CORNERS,
        MarketFamily.MATCH_TOTAL,
        None,
        Side.OVER,
        "9.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.CORNERS,
        MarketFamily.TEAM_TOTAL,
        "Inglaterra",
        Side.UNDER,
        "6.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.SHOTS,
        MarketFamily.TEAM_TOTAL,
        "Inglaterra",
        Side.UNDER,
        "15.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.SHOTS_ON_TARGET,
        MarketFamily.TEAM_TOTAL,
        "RD Congo",
        Side.OVER,
        "2.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.CARDS,
        MarketFamily.MATCH_TOTAL,
        None,
        Side.OVER,
        "3.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.CORNERS,
        MarketFamily.MATCH_TOTAL,
        None,
        Side.OVER,
        "5.5",
        Period.FIRST_HALF,
    ) in keys


def test_parse_estrelabet_deep_event_rejects_player_and_handicap_noise() -> None:
    results = parse_estrelabet_deep_event(_fixture())
    rejected = [r for r in results if not r.accepted]
    reasons = {r.reject_reason for r in rejected}

    assert RejectReason.NOISE_MARKET in reasons
    assert any("jogador" in str(r.evidence.get("market_norm")) for r in rejected)
    assert any("handicap" in str(r.evidence.get("market_norm")) for r in rejected)
