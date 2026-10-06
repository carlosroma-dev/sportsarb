from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from odds_arb.core.deep_markets import Side
from odds_arb.core.tennis_markets import (
    TennisMarket,
    TennisMarketOdd,
    build_tennis_market_key,
    canonical_player_key,
    detect_tennis_arbs,
)

START = datetime(2026, 7, 2, 22, 0, tzinfo=UTC)


def _winner(
    bookmaker: str,
    player: str,
    odd: str,
    *,
    player_a: str = "Andrej Nedic",
    player_b: str = "Keegan Smith",
    start: datetime = START,
) -> TennisMarketOdd:
    return TennisMarketOdd(
        bookmaker=bookmaker,
        raw_event_id="e1",
        raw_market_id="m-winner",
        raw_selection_id=f"{bookmaker}:{player}",
        event_name=f"{player_a} - {player_b}",
        player_a=player_a,
        player_b=player_b,
        start_time=start,
        market=TennisMarket.MATCH_WINNER,
        winner_player=player,
        odd=Decimal(odd),
        raw_market_name="Vencedor",
        raw_selection_name=player,
    )


def _total(
    bookmaker: str,
    side: Side,
    line: str,
    odd: str,
    *,
    player_a: str = "Andrej Nedic",
    player_b: str = "Keegan Smith",
    start: datetime = START,
) -> TennisMarketOdd:
    return TennisMarketOdd(
        bookmaker=bookmaker,
        raw_event_id="e1",
        raw_market_id="m-total",
        raw_selection_id=f"{bookmaker}:{side.value}:{line}",
        event_name=f"{player_a} - {player_b}",
        player_a=player_a,
        player_b=player_b,
        start_time=start,
        market=TennisMarket.MATCH_TOTAL_GAMES,
        side=side,
        line=Decimal(line),
        odd=Decimal(odd),
        raw_market_name="Total de Games",
        raw_selection_name=f"{'Mais' if side is Side.OVER else 'Menos'} de {line}",
    )


def test_canonical_player_key_matches_full_and_abbreviated_names() -> None:
    assert canonical_player_key("Andrej Nedic") == canonical_player_key("A.Nedic")
    assert canonical_player_key("Keegan Smith") == canonical_player_key("K. Smith")
    assert canonical_player_key("A.C.L.Obregon") == "a obregon"
    assert canonical_player_key("Sascha Gueymard Wayenburg") == canonical_player_key(
        "S.Gueymard Wayenburg"
    )


def test_canonical_player_key_single_token() -> None:
    assert canonical_player_key("Nadal") == "nadal"


def test_winner_requires_winner_player() -> None:
    with pytest.raises(ValidationError):
        TennisMarketOdd(
            bookmaker="betano",
            raw_event_id="e1",
            raw_market_id="m",
            raw_selection_id="s",
            event_name="A - B",
            player_a="Andrej Nedic",
            player_b="Keegan Smith",
            start_time=START,
            market=TennisMarket.MATCH_WINNER,
            odd=Decimal("1.5"),
            raw_market_name="Vencedor",
            raw_selection_name="Andrej Nedic",
        )


def test_total_requires_side_and_line() -> None:
    with pytest.raises(ValidationError):
        TennisMarketOdd(
            bookmaker="betano",
            raw_event_id="e1",
            raw_market_id="m",
            raw_selection_id="s",
            event_name="A - B",
            player_a="Andrej Nedic",
            player_b="Keegan Smith",
            start_time=START,
            market=TennisMarket.MATCH_TOTAL_GAMES,
            odd=Decimal("1.9"),
            raw_market_name="Total de Games",
            raw_selection_name="Mais de 21.5",
        )


def test_winner_player_must_be_one_of_the_players() -> None:
    with pytest.raises(ValidationError):
        _winner("betano", "Rafael Nadal", "2.0")


def test_sport_is_always_tennis() -> None:
    odd = _winner("betano", "Andrej Nedic", "1.32")
    assert odd.sport == "tennis"


def test_market_key_is_order_independent_across_houses() -> None:
    betano = _winner("betano", "Andrej Nedic", "1.32")
    superbet = _winner("superbet", "A.Nedic", "1.30", player_a="K.Smith", player_b="A.Nedic")
    assert build_tennis_market_key(betano) == build_tennis_market_key(superbet)


def test_detect_winner_arb_cross_house() -> None:
    odds = [
        _winner("betano", "Andrej Nedic", "2.10"),
        _winner("betano", "Keegan Smith", "1.70"),
        _winner("superbet", "K.Smith", "2.05", player_a="A.Nedic", player_b="K.Smith"),
    ]
    opportunities = detect_tennis_arbs(odds)
    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp.market is TennisMarket.MATCH_WINNER
    assert {opp.leg_a.bookmaker, opp.leg_b.bookmaker} == {"betano", "superbet"}
    assert opp.implied_probability_sum < Decimal("1")
    assert opp.profit_pct > Decimal("0")


def test_winner_same_bookmaker_is_not_arb() -> None:
    odds = [
        _winner("betano", "Andrej Nedic", "2.10"),
        _winner("betano", "Keegan Smith", "2.05"),
    ]
    assert detect_tennis_arbs(odds) == []


def test_winner_same_player_both_houses_is_not_arb() -> None:
    odds = [
        _winner("betano", "Andrej Nedic", "2.10"),
        _winner("superbet", "A.Nedic", "2.30", player_a="A.Nedic", player_b="K.Smith"),
    ]
    assert detect_tennis_arbs(odds) == []


def test_detect_total_games_arb_same_line() -> None:
    odds = [
        _total("betano", Side.OVER, "21.5", "2.10"),
        _total("superbet", Side.UNDER, "21.5", "2.05"),
    ]
    opportunities = detect_tennis_arbs(odds)
    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp.market is TennisMarket.MATCH_TOTAL_GAMES
    assert opp.leg_a.side is Side.OVER
    assert opp.leg_b.side is Side.UNDER


def test_total_games_different_lines_do_not_pair() -> None:
    odds = [
        _total("betano", Side.OVER, "21.5", "2.10"),
        _total("superbet", Side.UNDER, "22.5", "2.05"),
    ]
    assert detect_tennis_arbs(odds) == []


def test_no_arb_when_implied_sum_at_least_one() -> None:
    odds = [
        _winner("betano", "Andrej Nedic", "1.90"),
        _winner("superbet", "K.Smith", "2.00", player_a="A.Nedic", player_b="K.Smith"),
    ]
    assert detect_tennis_arbs(odds) == []


def test_min_profit_filter() -> None:
    odds = [
        _winner("betano", "Andrej Nedic", "2.10"),
        _winner("superbet", "K.Smith", "2.05", player_a="A.Nedic", player_b="K.Smith"),
    ]
    assert detect_tennis_arbs(odds, min_profit_pct=Decimal("50")) == []
