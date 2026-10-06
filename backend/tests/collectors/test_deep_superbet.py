import collections
import json
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.superbet_deep import (
    extract_line_from_selection,
    parse_superbet_deep_event,
)
from odds_arb.core.deep_markets import MarketFamily, Metric, RejectReason, Side

FIXTURE = Path("tests/fixtures/deep_markets/superbet_event_detail_sample.json")


def _event() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"][0]


def test_extract_line_from_selection() -> None:
    assert extract_line_from_selection("Mais de 4.5") == Decimal("4.5")
    assert extract_line_from_selection("Menos de 10.5") == Decimal("10.5")
    assert extract_line_from_selection("Coreia do Sul") is None


def test_extract_line_ignores_leading_integer() -> None:
    assert extract_line_from_selection("1º Tempo - Mais de 4.5") == Decimal("4.5")
    assert extract_line_from_selection("Mais de 10.5") == Decimal("10.5")


def test_parse_accepts_team_and_match_corners() -> None:
    results = parse_superbet_deep_event(_event())
    accepted = [r.odd for r in results if r.accepted and r.odd is not None]
    families = {(o.market_family, o.metric, o.subject, o.side, o.line) for o in accepted}
    match_corners = (
        MarketFamily.MATCH_TOTAL,
        Metric.CORNERS,
        None,
        Side.OVER,
        Decimal("9.5"),
    )
    team_corners = (
        MarketFamily.TEAM_TOTAL,
        Metric.CORNERS,
        "Coreia do Sul",
        Side.OVER,
        Decimal("4.5"),
    )
    team_shots = (
        MarketFamily.TEAM_TOTAL,
        Metric.SHOTS_ON_TARGET,
        "Coreia do Sul",
        Side.OVER,
        Decimal("4.5"),
    )
    assert match_corners in families
    assert team_corners in families
    assert team_shots in families


def test_parse_rejects_noise_and_suspended() -> None:
    results = parse_superbet_deep_event(_event())
    reasons = {r.reject_reason.value for r in results if not r.accepted and r.reject_reason}
    assert "noise_market" in reasons
    assert "suspended" in reasons


_EXTRA_FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "deep_markets"
    / "superbet_event_extra_stats_sample.json"
)


def test_superbet_extra_stats_payload_yields_totals_and_rejects_noise():
    event = json.loads(_EXTRA_FIXTURE.read_text(encoding="utf-8"))
    results = parse_superbet_deep_event(event)
    accepted = [r.odd for r in results if r.accepted]

    breakdown = collections.Counter(
        (o.metric.value, o.market_family.value, o.subject) for o in accepted
    )
    assert breakdown == {
        ("fouls", "match_total", None): 32,
        ("fouls", "team_total", "França"): 24,
        ("fouls", "team_total", "Noruega"): 24,
        ("offsides", "match_total", None): 15,
        ("offsides", "team_total", "França"): 10,
        ("offsides", "team_total", "Noruega"): 9,
        ("tackles", "match_total", None): 32,
        ("tackles", "team_total", "França"): 24,
        ("tackles", "team_total", "Noruega"): 24,
        ("throw_ins", "match_total", None): 40,
        ("throw_ins", "team_total", "França"): 28,
        ("throw_ins", "team_total", "Noruega"): 28,
    }
    assert {o.metric.value for o in accepted} == {"fouls", "offsides", "tackles", "throw_ins"}
    # Every team_total carries a subject; match_total never does.
    for o in accepted:
        if o.market_family.value == "team_total":
            assert o.subject is not None
        else:
            assert o.subject is None
    # The conservative resolver throws out the player props / time windows /
    # "cada equipe" markets bundled in the real payload.
    rejected = [r for r in results if not r.accepted]
    reasons = {r.reject_reason for r in rejected}
    assert RejectReason.SUBJECT_UNRESOLVED in reasons
