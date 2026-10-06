import json
from pathlib import Path

from odds_arb.collectors.deep.kto_deep import parse_kto_deep_event
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
        Path("tests/fixtures/deep_markets/kto_event_detail_sample.json").read_text(encoding="utf-8")
    )


def test_parse_kto_deep_event_accepts_match_and_team_totals() -> None:
    results = parse_kto_deep_event(_fixture())
    odds = [r.odd for r in results if r.accepted and r.odd is not None]

    assert odds
    assert all(odd.bookmaker == "kto" for odd in odds)
    assert all(odd.line_source == LineSource.SELECTION_HANDICAP for odd in odds)
    assert all(odd.source_event_url == "https://www.kto.bet.br/app/esportes" for odd in odds)

    keys = {
        (odd.metric, odd.market_family, odd.subject, odd.side, str(odd.line), odd.period)
        for odd in odds
    }
    assert (
        Metric.CORNERS,
        MarketFamily.MATCH_TOTAL,
        None,
        Side.OVER,
        "10.5",
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
        MarketFamily.MATCH_TOTAL,
        None,
        Side.UNDER,
        "21.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.SHOTS_ON_TARGET,
        MarketFamily.TEAM_TOTAL,
        "Congo",
        Side.OVER,
        "2.5",
        Period.FULL_TIME,
    ) in keys
    assert (
        Metric.CARDS,
        MarketFamily.TEAM_TOTAL,
        "Congo",
        Side.OVER,
        "1.5",
        Period.FULL_TIME,
    ) in keys


def test_parse_kto_deep_event_rejects_player_and_red_card_noise() -> None:
    results = parse_kto_deep_event(_fixture())
    rejected = [r for r in results if not r.accepted]
    reasons = {r.reject_reason for r in rejected}

    assert RejectReason.NOISE_MARKET in reasons
    assert any("jogador" in str(r.evidence.get("market_norm")) for r in rejected)
    assert any("vermelho" in str(r.evidence.get("market_norm")) for r in rejected)
