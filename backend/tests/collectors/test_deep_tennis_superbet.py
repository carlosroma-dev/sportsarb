import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from odds_arb.collectors.deep.superbet_tennis import (
    SuperbetTennisListEvent,
    build_tennis_by_date_params,
    discover_superbet_tennis_events,
    fetch_superbet_tennis_detail,
    fetch_superbet_tennis_list,
    pair_tennis_to_betano_universe,
    parse_superbet_tennis_event,
    superbet_tennis_player_set,
)
from odds_arb.core.deep_markets import Side
from odds_arb.core.tennis_markets import TennisMarket, canonical_player_key

LIST_FIXTURE = Path("tests/fixtures/deep_markets/superbet_tennis_list_sample.json")
DETAIL_FIXTURE = Path("tests/fixtures/deep_markets/superbet_tennis_event_detail_sample.json")


def _list_payload() -> dict:
    return json.loads(LIST_FIXTURE.read_text(encoding="utf-8"))


def _detail_event() -> dict:
    return json.loads(DETAIL_FIXTURE.read_text(encoding="utf-8"))["data"][0]


def test_discover_skips_doubles_and_missing_ids() -> None:
    events = discover_superbet_tennis_events(_list_payload())
    assert events == [SuperbetTennisListEvent(event_id="13841825", match_name="A.Nedic·K.Smith")]


def test_player_set_matches_betano_full_names() -> None:
    superbet = superbet_tennis_player_set("A.Nedic·K.Smith")
    betano = frozenset({canonical_player_key("Andrej Nedic"), canonical_player_key("Keegan Smith")})
    assert superbet == betano


def test_pairing_keeps_only_universe_matches() -> None:
    events = [
        SuperbetTennisListEvent(event_id="13841825", match_name="A.Nedic·K.Smith"),
        SuperbetTennisListEvent(event_id="999", match_name="J.Doe·R.Roe"),
    ]
    universe = {
        frozenset({canonical_player_key("Keegan Smith"), canonical_player_key("Andrej Nedic")})
    }
    assert pair_tennis_to_betano_universe(events, universe) == ["13841825"]


def test_build_params_use_tennis_sport_id() -> None:
    start = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    end = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    params = build_tennis_by_date_params(start, end)
    assert params["sportId"] == "2"
    assert params["offerState"] == "prematch"
    assert params["startDate"] == "2026-07-02 12:00:00"


def test_parse_accepts_winner_and_match_total_games_only() -> None:
    odds = parse_superbet_tennis_event(_detail_event())
    winners = [o for o in odds if o.market is TennisMarket.MATCH_WINNER]
    totals = [o for o in odds if o.market is TennisMarket.MATCH_TOTAL_GAMES]

    assert len(odds) == 4
    assert {(o.winner_player, o.odd) for o in winners} == {
        ("A.Nedic", Decimal("1.3")),
        ("K.Smith", Decimal("3.4")),
    }
    assert {(o.side, o.line, o.odd) for o in totals} == {
        (Side.OVER, Decimal("21.5"), Decimal("1.95")),
        (Side.UNDER, Decimal("21.5"), Decimal("1.87")),
    }


def test_parse_fills_common_fields() -> None:
    odds = parse_superbet_tennis_event(_detail_event())
    sample = odds[0]
    assert sample.bookmaker == "superbet"
    assert sample.sport == "tennis"
    assert sample.player_a == "A.Nedic"
    assert sample.player_b == "K.Smith"
    assert sample.start_time == datetime(2026, 7, 2, 22, 0, tzinfo=UTC)
    assert sample.source_event_url == "https://superbet.bet.br/eventos/13841825"


def test_parse_rejects_doubles_event() -> None:
    event = _detail_event()
    event["matchName"] = "J.J.Bianchi/C.Harper·D.Milavsky/J.Sheehy"
    assert parse_superbet_tennis_event(event) == []


def test_fetch_list_and_detail_use_injected_getter() -> None:
    start = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    end = datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
    seen: list[tuple[str, dict | None]] = []

    def getter(url, params):
        seen.append((url, dict(params) if params else None))
        if "by-date" in url:
            return _list_payload()
        return json.loads(DETAIL_FIXTURE.read_text(encoding="utf-8"))

    payload = fetch_superbet_tennis_list(start, end, json_getter=getter)
    assert payload["data"][0]["eventId"] == 13841825
    detail = fetch_superbet_tennis_detail("13841825", json_getter=getter)
    assert detail is not None
    assert detail["eventId"] == 13841825
    assert seen[0][1] is not None
    assert seen[0][1]["sportId"] == "2"
    assert seen[1][0].endswith("/13841825")
