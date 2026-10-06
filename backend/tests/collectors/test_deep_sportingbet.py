import json
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.sportingbet_deep import parse_sportingbet_deep_event
from odds_arb.core.deep_markets import (
    LineSource,
    MarketFamily,
    Metric,
    Period,
    RejectReason,
    Side,
)

FIXTURE = Path("tests/fixtures/deep_markets/sportingbet_event_detail_sample.json")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["fixtures"][0]


def test_parse_accepts_sportingbet_match_and_team_totals() -> None:
    results = parse_sportingbet_deep_event(_fixture())
    accepted = [r.odd for r in results if r.accepted and r.odd is not None]

    families = {(o.market_family, o.metric, o.subject, o.side, o.line, o.period) for o in accepted}

    assert (
        MarketFamily.MATCH_TOTAL,
        Metric.CORNERS,
        None,
        Side.OVER,
        Decimal("9.5"),
        Period.FULL_TIME,
    ) in families
    assert (
        MarketFamily.TEAM_TOTAL,
        Metric.CORNERS,
        "Inglaterra",
        Side.UNDER,
        Decimal("6.5"),
        Period.FULL_TIME,
    ) in families
    assert (
        MarketFamily.MATCH_TOTAL,
        Metric.SHOTS_ON_TARGET,
        None,
        Side.OVER,
        Decimal("8.5"),
        Period.FULL_TIME,
    ) in families
    assert (
        MarketFamily.MATCH_TOTAL,
        Metric.SHOTS,
        None,
        Side.UNDER,
        Decimal("23.5"),
        Period.FULL_TIME,
    ) in families
    assert (
        MarketFamily.TEAM_TOTAL,
        Metric.CARDS,
        "Inglaterra",
        Side.OVER,
        Decimal("1.5"),
        Period.FULL_TIME,
    ) in families

    assert all(o.bookmaker == "sportingbet" for o in accepted)
    assert all(o.line_source is LineSource.MARKET_PARAMETER for o in accepted)
    assert all(
        o.source_event_url == "https://www.sportingbet.bet.br/pt-br/sports" for o in accepted
    )


def test_parse_rejects_sportingbet_noise_and_time_window_markets() -> None:
    results = parse_sportingbet_deep_event(_fixture())
    rejected = [r for r in results if not r.accepted]
    reasons = {r.reject_reason for r in rejected}

    assert RejectReason.NOISE_MARKET in reasons
    assert RejectReason.NO_SIDE in reasons
