from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from odds_arb.core.models import ArbitrageOpportunity, Odd
from odds_arb.reporting import BOOKMAKER_LABELS, format_opportunity_report, report_opportunities


def _opportunity() -> ArbitrageOpportunity:
    best_odds = {
        "home": Odd(
            match_id="soccer:flamengo:palmeiras",
            market_key="1x2",
            outcome_key="home",
            price=Decimal("2.20"),
            bookmaker="betano",
        ),
        "draw": Odd(
            match_id="soccer:flamengo:palmeiras",
            market_key="1x2",
            outcome_key="draw",
            price=Decimal("3.60"),
            bookmaker="kto",
        ),
        "away": Odd(
            match_id="soccer:flamengo:palmeiras",
            market_key="1x2",
            outcome_key="away",
            price=Decimal("4.20"),
            bookmaker="superbet",
        ),
    }
    return ArbitrageOpportunity(
        match_id="soccer:flamengo:palmeiras",
        market_key="1x2",
        best_odds=best_odds,
        implied_probability_sum=Decimal("0.9704184704184704184704184704"),
        profit_pct=Decimal("3.048327137546468401486988800"),
        stakes={
            "home": Decimal("468.40"),
            "draw": Decimal("286.25"),
            "away": Decimal("245.35"),
        },
        detected_at=datetime(2026, 6, 16, 21, 3, 44, tzinfo=UTC),
    )


def test_format_opportunity_report_matches_expected_block() -> None:
    report = format_opportunity_report(
        _opportunity(),
        home_team="Flamengo",
        away_team="Palmeiras",
        bankroll=Decimal("1000"),
    )

    assert report == (
        "[2026-06-16 21:03:44] ARB ENCONTRADO\n"
        "  Match: Flamengo vs Palmeiras\n"
        "  Casa 1: Betano | Odd: 2.20 | Outcome: Casa\n"
        "  Casa 2: KTO | Odd: 3.60 | Outcome: Empate\n"
        "  Casa 3: Superbet | Odd: 4.20 | Outcome: Fora\n"
        "  Margem: 3.0%\n"
        "  Stakes sugeridos (banca R$1000): R$468 / R$286 / R$245"
    )


def test_report_opportunities_writes_to_terminal_and_file(tmp_path, capsys) -> None:
    log_path = tmp_path / "logs" / "opportunities.log"

    reports = report_opportunities(
        [_opportunity()],
        match_names={"soccer:flamengo:palmeiras": ("Flamengo", "Palmeiras")},
        bankroll=Decimal("1000"),
        log_path=log_path,
    )

    assert len(reports) == 1
    captured = capsys.readouterr().out
    assert "ARB ENCONTRADO" in captured
    assert "Flamengo vs Palmeiras" in captured

    contents = log_path.read_text(encoding="utf-8")
    assert "ARB ENCONTRADO" in contents
    assert contents.endswith("\n")


def test_report_opportunities_without_opportunities_does_not_create_file(tmp_path) -> None:
    log_path = tmp_path / "logs" / "opportunities.log"

    reports = report_opportunities(
        [],
        match_names={},
        bankroll=Decimal("1000"),
        log_path=log_path,
    )

    assert reports == []
    assert not log_path.exists()


def test_bookmaker_labels_include_all_supported_collectors() -> None:
    assert BOOKMAKER_LABELS["novibet"] == "Novibet"
    assert BOOKMAKER_LABELS["pixbet"] == "Pixbet"
