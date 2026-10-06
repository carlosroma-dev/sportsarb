import collections
import json
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.betano_deep import parse_betano_deep_event
from odds_arb.core.deep_markets import LineSource, MarketFamily, Metric, Side

FIXTURE = Path("tests/fixtures/deep_markets/betano_event_detail_sample.json")


def _event() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_parse_flat_match_and_team_markets() -> None:
    accepted = [r.odd for r in parse_betano_deep_event(_event()) if r.accepted and r.odd]
    fams = {(o.market_family, o.metric, o.subject, o.side, o.line) for o in accepted}
    assert (MarketFamily.MATCH_TOTAL, Metric.CORNERS, None, Side.OVER, Decimal("9.5")) in fams
    assert (
        MarketFamily.TEAM_TOTAL,
        Metric.CORNERS,
        "Coréia do Sul",
        Side.UNDER,
        Decimal("4.5"),
    ) in fams
    assert (MarketFamily.MATCH_TOTAL, Metric.CARDS, None, Side.OVER, Decimal("4.5")) in fams


def test_flat_line_source_is_handicap() -> None:
    corners = [
        r.odd
        for r in parse_betano_deep_event(_event())
        if r.accepted
        and r.odd
        and r.odd.metric is Metric.CORNERS
        and r.odd.market_family is MarketFamily.MATCH_TOTAL
    ]
    assert corners
    assert all(o.line_source is LineSource.SELECTION_HANDICAP for o in corners)


def test_parse_grouped_table_layout_team_shots_on_target() -> None:
    accepted = [r.odd for r in parse_betano_deep_event(_event()) if r.accepted and r.odd]
    sot = [o for o in accepted if o.metric is Metric.SHOTS_ON_TARGET]
    assert {o.side for o in sot} == {Side.OVER, Side.UNDER}
    assert all(o.subject == "Coréia do Sul" and o.line == Decimal("4.5") for o in sot)
    assert all(o.line_source is LineSource.TABLE_LAYOUT_ROW for o in sot)


def test_real_betano_deep_payload_yields_shots_and_sot_totals() -> None:
    _fixture = (
        Path(__file__).parent.parent
        / "fixtures"
        / "deep_markets"
        / "betano_event_deep_shots_sample.json"
    )
    event = json.loads(_fixture.read_text(encoding="utf-8"))
    accepted = [r.odd for r in parse_betano_deep_event(event) if r.accepted]

    breakdown = collections.Counter(
        (o.metric.value, o.market_family.value, o.subject) for o in accepted
    )
    assert breakdown == {
        ("shots", "match_total", None): 30,
        ("shots", "team_total", "Alemanha"): 22,
        ("shots", "team_total", "Equador"): 22,
        ("shots_on_target", "match_total", None): 26,
        ("shots_on_target", "team_total", "Alemanha"): 22,
        ("shots_on_target", "team_total", "Equador"): 18,
        ("fouls", "match_total", None): 26,
        ("fouls", "team_total", "Alemanha"): 22,
        ("fouls", "team_total", "Equador"): 22,
        ("offsides", "match_total", None): 14,
        ("offsides", "team_total", "Alemanha"): 10,
        ("offsides", "team_total", "Equador"): 10,
        ("tackles", "match_total", None): 26,
        ("tackles", "team_total", "Alemanha"): 22,
        ("tackles", "team_total", "Equador"): 22,
        ("throw_ins", "match_total", None): 28,
        ("throw_ins", "team_total", "Alemanha"): 22,
        ("throw_ins", "team_total", "Equador"): 22,
        ("goal_kicks", "match_total", None): 28,
        ("goal_kicks", "team_total", "Alemanha"): 22,
        ("goal_kicks", "team_total", "Equador"): 22,
    }
    assert {o.metric.value for o in accepted} == {
        "shots",
        "shots_on_target",
        "fouls",
        "offsides",
        "tackles",
        "throw_ins",
        "goal_kicks",
    }
    # Team-totals always carry a subject; match-totals never do.
    for o in accepted:
        if o.market_family.value == "team_total":
            assert o.subject is not None
        else:
            assert o.subject is None


def test_betano_source_url_prefers_slug_from_event_url() -> None:
    # The id-only URL "/odds/{id}/" 404s; the slug form from event["url"] resolves.
    event = {
        "id": 83964528,
        "name": "Panamá - Inglaterra",
        "url": "/odds/panama-inglaterra/83964528/",
        "startTime": 1782417600000,
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
    accepted = [r.odd for r in parse_betano_deep_event(event) if r.accepted]
    assert accepted
    assert all(
        o.source_event_url == "https://www.betano.bet.br/odds/panama-inglaterra/83964528/"
        for o in accepted
    )


def test_betano_source_url_falls_back_to_id_when_url_absent() -> None:
    event = {
        "id": 999,
        "name": "A - B",
        "startTime": 1782417600000,
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
    accepted = [r.odd for r in parse_betano_deep_event(event) if r.accepted]
    assert accepted
    assert all(o.source_event_url == "https://www.betano.bet.br/odds/999/" for o in accepted)
