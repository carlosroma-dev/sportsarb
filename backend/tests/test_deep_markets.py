from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from odds_arb.core.deep_markets import (
    Confidence,
    DeepMarketNormalizationResult,
    DeepMarketOdd,
    LineSource,
    MarketFamily,
    Metric,
    Period,
    RejectReason,
    Side,
    build_deep_market_key,
    detect_deep_market_arbs,
    normalize_deep_market,
)


def _odd(**overrides: object) -> DeepMarketOdd:
    base: dict[str, object] = {
        "bookmaker": "superbet",
        "raw_event_id": "11499832",
        "raw_market_id": "733",
        "raw_selection_id": "733:over:4.5",
        "event_name": "África do Sul - Coreia do Sul",
        "home_team": "África do Sul",
        "away_team": "Coreia do Sul",
        "start_time": datetime(2026, 6, 27, 21, 0, tzinfo=UTC),
        "period": Period.FULL_TIME,
        "market_family": MarketFamily.TEAM_TOTAL,
        "metric": Metric.CORNERS,
        "subject": "Coreia do Sul",
        "side": Side.OVER,
        "odd": Decimal("1.65"),
        "raw_market_name": "Coreia do Sul - Total de Escanteios",
        "raw_selection_name": "Mais de 4.5",
        "is_live": False,
        "is_available": True,
        "line": Decimal("4.5"),
        "raw_line_value": "Mais de 4.5",
        "line_source": LineSource.SELECTION_NAME_REGEX,
    }
    base.update(overrides)
    return DeepMarketOdd(**base)


def test_deep_market_odd_accepts_valid_payload() -> None:
    odd = _odd()
    assert odd.metric is Metric.CORNERS
    assert odd.subject == "Coreia do Sul"
    assert odd.line == Decimal("4.5")


def test_team_total_requires_subject() -> None:
    with pytest.raises(ValidationError):
        _odd(market_family=MarketFamily.TEAM_TOTAL, subject=None)


def test_match_total_forbids_subject() -> None:
    with pytest.raises(ValidationError):
        _odd(market_family=MarketFamily.MATCH_TOTAL, subject="Coreia do Sul")


def test_odd_must_be_above_one() -> None:
    with pytest.raises(ValidationError):
        _odd(odd=Decimal("1.0"))


def test_start_time_must_be_timezone_aware() -> None:
    with pytest.raises(ValidationError):
        _odd(start_time=datetime(2026, 6, 27, 21, 0))


_KWARGS = {
    "raw_event_id": "e1",
    "raw_market_id": "m1",
    "raw_selection_id": "s1",
    "event_name": "África do Sul - Coreia do Sul",
    "home_team": "África do Sul",
    "away_team": "Coreia do Sul",
    "start_time": datetime(2026, 6, 27, 21, 0, tzinfo=UTC),
    "odd": Decimal("2.05"),
    "is_live": False,
}


def _norm_call(**over: object) -> DeepMarketNormalizationResult:
    kwargs = dict(_KWARGS)
    kwargs.update(
        bookmaker="betano",
        raw_market_name="Coreia do Sul Chutes no gol",
        raw_selection_name="Menos de 4.5",
        line=Decimal("4.5"),
        raw_line_value="4.5",
        line_source=LineSource.SELECTION_HANDICAP,
    )
    kwargs.update(over)
    return normalize_deep_market(**kwargs)  # type: ignore[arg-type]


def test_betano_team_shots_on_target_under() -> None:
    result = _norm_call()
    assert result.accepted
    assert result.odd is not None
    assert result.odd.market_family is MarketFamily.TEAM_TOTAL
    assert result.odd.metric is Metric.SHOTS_ON_TARGET
    assert result.odd.subject == "Coreia do Sul"
    assert result.odd.line == Decimal("4.5")
    assert result.odd.side is Side.UNDER


def test_superbet_team_shots_on_target_over() -> None:
    result = _norm_call(
        bookmaker="superbet",
        raw_market_name="Coreia do Sul - Chutes no Gol",
        raw_selection_name="Mais de 4.5",
        line_source=LineSource.SELECTION_NAME_REGEX,
        raw_line_value="Mais de 4.5",
        odd=Decimal("1.95"),
    )
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.SHOTS_ON_TARGET
    assert result.odd.side is Side.OVER


def test_shots_on_target_not_confused_with_shots() -> None:
    # "Total de chutes" is SHOTS, never SHOTS_ON_TARGET (guarded by "no gol").
    result = _norm_call(raw_market_name="Coreia do Sul Total de chutes")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.SHOTS
    # And Superbet's word for shots ("Finalizações") also maps to SHOTS.
    result2 = _norm_call(bookmaker="superbet", raw_market_name="Coreia do Sul Finalizações")
    assert result2.odd is not None
    assert result2.odd.metric is Metric.SHOTS


def test_match_total_has_no_subject() -> None:
    result = _norm_call(raw_market_name="Total de Escanteios")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.market_family is MarketFamily.MATCH_TOTAL
    assert result.odd.metric is Metric.CORNERS
    assert result.odd.subject is None


def test_first_half_period_detected() -> None:
    result = _norm_call(raw_market_name="1º Tempo - Total de Escanteios")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.period is Period.FIRST_HALF


def test_ambiguous_market_rejected() -> None:
    result = _norm_call(raw_market_name="Faixa de Escanteios", raw_selection_name="5-6")
    assert not result.accepted
    assert result.odd is None
    assert result.confidence is Confidence.REJECT


def test_noise_red_cards_rejected() -> None:
    result = _norm_call(raw_market_name="Coreia do Sul - Total de Cartões Vermelhos")
    assert not result.accepted
    assert result.reject_reason is RejectReason.NOISE_MARKET


def test_missing_line_rejected() -> None:
    result = _norm_call(line=None)
    assert not result.accepted
    assert result.reject_reason is RejectReason.NO_LINE


def test_subject_not_matching_teams_rejected() -> None:
    result = _norm_call(raw_market_name="Barcelona Chutes no gol")
    assert not result.accepted
    assert result.reject_reason is RejectReason.SUBJECT_UNRESOLVED


def test_live_odd_rejected() -> None:
    result = _norm_call(is_live=True)
    assert not result.accepted
    assert result.reject_reason is RejectReason.LIVE


def test_primeiro_tempo_is_first_half_not_noise() -> None:
    result = _norm_call(
        raw_market_name="Primeiro Tempo - Total de Escanteios",
        raw_selection_name="Mais de 4.5",
    )
    assert result.accepted
    assert result.odd is not None
    assert result.odd.period is Period.FIRST_HALF
    assert result.odd.metric is Metric.CORNERS
    assert result.odd.market_family is MarketFamily.MATCH_TOTAL


def test_intervalo_period_rejected() -> None:
    result = _norm_call(
        raw_market_name="Intervalo Total de Escanteios",
        raw_selection_name="Mais de 4.5",
    )
    assert not result.accepted
    assert result.reject_reason is RejectReason.AMBIGUOUS_PERIOD


def test_both_teams_in_name_rejected() -> None:
    result = _norm_call(
        raw_market_name="África do Sul Coreia do Sul Total de Escanteios",
        raw_selection_name="Mais de 9.5",
    )
    assert not result.accepted
    assert result.reject_reason is RejectReason.SUBJECT_UNRESOLVED


def test_no_side_rejected() -> None:
    result = _norm_call(raw_selection_name="Coreia do Sul")
    assert not result.accepted
    assert result.reject_reason is RejectReason.NO_SIDE


def test_suspended_rejected() -> None:
    result = _norm_call(is_available=False)
    assert not result.accepted
    assert result.reject_reason is RejectReason.SUSPENDED


# Task 3: Key and 2-way detector


def _match_total(bookmaker: str, side: Side, line: str, odd: str) -> DeepMarketOdd:
    return _odd(
        bookmaker=bookmaker,
        market_family=MarketFamily.MATCH_TOTAL,
        metric=Metric.CORNERS,
        subject=None,
        side=side,
        line=Decimal(line),
        odd=Decimal(odd),
        raw_market_name="Total de Escanteios",
        raw_selection_name=("Mais de " if side is Side.OVER else "Menos de ") + line,
        raw_selection_id=f"{bookmaker}:{side.value}:{line}",
    )


def test_corners_match_total_cross_house_arb() -> None:
    odds = [
        _match_total("betano", Side.OVER, "9.5", "2.10"),
        _match_total("superbet", Side.UNDER, "9.5", "2.10"),
    ]
    opportunities = detect_deep_market_arbs(odds, min_profit_pct=Decimal("0"))
    assert len(opportunities) == 1
    arb = opportunities[0]
    assert arb.implied_probability_sum == Decimal("1") / Decimal("2.10") * 2
    assert arb.profit_pct > Decimal("0")


def test_different_lines_do_not_arb() -> None:
    odds = [
        _match_total("betano", Side.OVER, "9.5", "2.10"),
        _match_total("superbet", Side.UNDER, "10.5", "2.10"),
    ]
    assert detect_deep_market_arbs(odds) == []


def test_same_house_pair_does_not_arb() -> None:
    odds = [
        _match_total("betano", Side.OVER, "9.5", "2.10"),
        _match_total("betano", Side.UNDER, "9.5", "2.10"),
    ]
    assert detect_deep_market_arbs(odds) == []


def test_team_total_not_matched_with_match_total_in_key() -> None:
    team = _odd(
        market_family=MarketFamily.TEAM_TOTAL,
        subject="Coreia do Sul",
        side=Side.OVER,
        line=Decimal("4.5"),
    )
    match = _match_total("betano", Side.UNDER, "4.5", "2.10")
    assert build_deep_market_key(team) != build_deep_market_key(match)


def test_same_bookmaker_best_falls_back_to_cross_house() -> None:
    odds = [
        _match_total("betano", Side.OVER, "9.5", "2.20"),
        _match_total("superbet", Side.OVER, "9.5", "2.10"),
        _match_total("betano", Side.UNDER, "9.5", "2.10"),
    ]
    opps = detect_deep_market_arbs(odds, min_profit_pct=Decimal("0"))
    assert len(opps) == 1
    assert opps[0].over_leg.bookmaker == "superbet"
    assert opps[0].under_leg.bookmaker == "betano"


# Task 8 — Review pass 1: false-positive matching regression


def test_shots_market_with_no_gol_substring_is_sot_not_shots() -> None:
    result = _norm_call(
        raw_market_name="Coreia do Sul Total de Chutes no Gol",
        raw_selection_name="Mais de 4.5",
    )
    assert result.odd is not None
    assert result.odd.metric is Metric.SHOTS_ON_TARGET


def test_fouls_match_total() -> None:
    result = _norm_call(raw_market_name="Total de Faltas")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.FOULS
    assert result.odd.market_family is MarketFamily.MATCH_TOTAL
    assert result.odd.subject is None


def test_fouls_team_total_resolves_subject() -> None:
    result = _norm_call(raw_market_name="Coreia do Sul Total de Faltas")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.FOULS
    assert result.odd.market_family is MarketFamily.TEAM_TOTAL
    assert result.odd.subject == "Coreia do Sul"


def test_offsides_match_total() -> None:
    result = _norm_call(raw_market_name="Total de Impedimentos")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.OFFSIDES


def test_tackles_match_total() -> None:
    result = _norm_call(raw_market_name="Total de Desarmes")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.TACKLES


def test_throw_ins_betano_name() -> None:
    result = _norm_call(raw_market_name="Total de laterais")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.THROW_INS


def test_throw_ins_superbet_name_divergence() -> None:
    # Superbet calls throw-ins "Arremessos Laterais"; "later" must match both.
    result = _norm_call(raw_market_name="Total de Arremessos Laterais")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.THROW_INS


def test_goal_kicks_match_total() -> None:
    result = _norm_call(raw_market_name="Total de Tiros de Meta")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.GOAL_KICKS
    assert result.odd.market_family is MarketFamily.MATCH_TOTAL
    assert result.odd.subject is None


def test_goal_kicks_team_total_resolves_subject() -> None:
    result = _norm_call(raw_market_name="Coreia do Sul Total de Tiros de Meta")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.GOAL_KICKS
    assert result.odd.market_family is MarketFamily.TEAM_TOTAL
    assert result.odd.subject == "Coreia do Sul"


def test_goal_kicks_english_name() -> None:
    result = _norm_call(raw_market_name="Total Goal Kicks")
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.GOAL_KICKS


def test_gols_market_not_misclassified_as_goal_kicks() -> None:
    result = _norm_call(raw_market_name="Total de Gols")
    assert not result.accepted
    assert result.reject_reason is RejectReason.NO_METRIC


def test_player_prop_desarmes_is_rejected() -> None:
    # "Jogador - Total de Desarmes" references no known team -> residual non-empty.
    result = _norm_call(raw_market_name="Jogador - Total de Desarmes")
    assert not result.accepted
    assert result.reject_reason is RejectReason.SUBJECT_UNRESOLVED


def test_time_window_throw_ins_is_rejected() -> None:
    result = _norm_call(
        raw_market_name="Próximo Minuto - Total de Arremessos Laterais de 0:00 a 9:59"
    )
    assert not result.accepted
    assert result.reject_reason is RejectReason.SUBJECT_UNRESOLVED


def test_england_gols_market_not_misclassified_as_throw_ins() -> None:
    # "Inglaterra" contains the substring "later" (ing-LATER-ra). A non-throw-in
    # England market must NOT be tagged throw_ins (regression: phantom arbs where
    # England's Gols/passes/goal-kicks crossed real throw-in lines).
    result = _norm_call(
        raw_market_name="Inglaterra - Total de Gols Mais/Menos",
        home_team="Inglaterra",
        away_team="Panamá",
    )
    assert not result.accepted
    assert result.reject_reason is RejectReason.NO_METRIC


def test_england_real_throw_ins_still_detected() -> None:
    # The fix must still recognize England's genuine throw-ins market.
    result = _norm_call(
        raw_market_name="Inglaterra Total de laterais",
        home_team="Inglaterra",
        away_team="Panamá",
    )
    assert result.accepted
    assert result.odd is not None
    assert result.odd.metric is Metric.THROW_INS
    assert result.odd.subject == "Inglaterra"
