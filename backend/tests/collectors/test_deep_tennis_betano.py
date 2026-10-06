import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.betano_tennis import (
    discover_betano_tennis_tournaments,
    fetch_betano_tennis_events,
    parse_betano_tennis_event,
)
from odds_arb.core.deep_markets import Side
from odds_arb.core.tennis_markets import TennisMarket

FIXTURE = Path("tests/fixtures/deep_markets/betano_tennis_event_ssr_sample.json")


def _event() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


LANDING_HTML = (
    '{"url":"/sport/tenis/challenger/brasov/203252/"},'
    '{"url":"/sport/tenis/challenger/brasov-duplas/203254/"},'
    '{"url":"/sport/tenis/competicoes/atp/11307/"},'
    '{"url":"/sport/tenis/wimbledon/wimbledon/18310/"},'
    '{"url":"/sport/tenis/wimbledon/duplas-f/18341/"},'
    '{"url":"/sport/futebol/brasil/brasileirao-serie-b/10017/"}'
)


def test_discover_tournaments_keeps_singles_and_drops_doubles_and_aggregates() -> None:
    assert discover_betano_tennis_tournaments(LANDING_HTML) == [
        "/sport/tenis/challenger/brasov/203252/",
        "/sport/tenis/wimbledon/wimbledon/18310/",
    ]


def test_parse_extracts_winner_and_match_total_games_only() -> None:
    odds = parse_betano_tennis_event(_event())
    winners = [o for o in odds if o.market is TennisMarket.MATCH_WINNER]
    totals = [o for o in odds if o.market is TennisMarket.MATCH_TOTAL_GAMES]

    assert len(odds) == 6
    assert {(o.winner_player, o.odd) for o in winners} == {
        ("Andrej Nedic", Decimal("1.32")),
        ("Keegan Smith", Decimal("3.3")),
    }
    assert {(o.side, o.line, o.odd) for o in totals} == {
        (Side.OVER, Decimal("21.5"), Decimal("1.85")),
        (Side.UNDER, Decimal("21.5"), Decimal("1.85")),
        (Side.OVER, Decimal("20.5"), Decimal("1.52")),
        (Side.UNDER, Decimal("20.5"), Decimal("2.45")),
    }
    # armadilhas nunca podem vazar: set, por jogador, combo, handicap
    banned = {
        "Total de Games no Set (Set 1)",
        "Andrej Nedic Total de Games Ganhos",
        "Vencedor e Total de Games",
        "Handicap de games",
        "Vencedor do Set (Set 1)",
    }
    assert not [o for o in odds if o.raw_market_name in banned]


def test_parse_fills_common_fields() -> None:
    odds = parse_betano_tennis_event(_event())
    sample = odds[0]
    assert sample.bookmaker == "betano"
    assert sample.sport == "tennis"
    assert sample.player_a == "Andrej Nedic"
    assert sample.player_b == "Keegan Smith"
    assert sample.start_time == datetime.fromtimestamp(1783029600, tz=UTC)
    assert sample.competition_name == "Brasov"
    assert sample.source_event_url == (
        "https://www.betano.bet.br/odds/andrej-nedic-keegan-smith/88351976/"
    )
    assert sample.is_live is False


def test_parse_rejects_non_tennis_event() -> None:
    event = _event()
    event["sportId"] = "SOCC"
    assert parse_betano_tennis_event(event) == []


def test_parse_rejects_doubles_participants() -> None:
    event = _event()
    event["participants"] = [
        {"name": "A.Nedic/B.Costa", "id": "1"},
        {"name": "K.Smith/J.Doe", "id": "2"},
    ]
    assert parse_betano_tennis_event(event) == []


def test_fetch_walks_landing_tournaments_and_events() -> None:
    event = _event()
    event_html = 'window.x = {"event":' + json.dumps(event) + "};"
    tournament_html = 'href="/odds/andrej-nedic-keegan-smith/88351976/"'
    pages = {
        "https://www.betano.bet.br/sport/tenis/": LANDING_HTML,
        "https://www.betano.bet.br/sport/tenis/challenger/brasov/203252/": tournament_html,
        "https://www.betano.bet.br/sport/tenis/wimbledon/wimbledon/18310/": "",
        "https://www.betano.bet.br/odds/andrej-nedic-keegan-smith/88351976/": event_html,
    }

    events = fetch_betano_tennis_events(html_getter=pages.__getitem__, json_getter=None)
    assert len(events) == 1
    assert events[0]["id"] == "88351976"


def test_fetch_merges_deep_markets_when_json_getter_provided() -> None:
    event = _event()
    event_html = 'window.x = {"event":' + json.dumps(event) + "};"
    tournament_html = 'href="/odds/andrej-nedic-keegan-smith/88351976/"'
    pages = {
        "https://www.betano.bet.br/sport/tenis/": (
            '{"url":"/sport/tenis/challenger/brasov/203252/"}'
        ),
        "https://www.betano.bet.br/sport/tenis/challenger/brasov/203252/": tournament_html,
        "https://www.betano.bet.br/odds/andrej-nedic-keegan-smith/88351976/": event_html,
    }
    deep_payload = {
        "data": {
            "event": {
                "markets": [
                    {
                        "id": "999000",
                        "type": "FTGO",
                        "name": "Games (alternativas)",
                        "selections": [
                            {
                                "id": "s-999",
                                "name": "Mais de 19.5",
                                "price": 1.30,
                                "handicap": 19.5,
                            }
                        ],
                    }
                ]
            }
        }
    }

    def json_getter(url: str) -> str:
        assert "/api/odds/andrej-nedic-keegan-smith/88351976/" in url
        return json.dumps(deep_payload)

    events = fetch_betano_tennis_events(html_getter=pages.__getitem__, json_getter=json_getter)
    assert len(events) == 1
    market_ids = {str(m["id"]) for m in events[0]["markets"]}
    assert "999000" in market_ids
