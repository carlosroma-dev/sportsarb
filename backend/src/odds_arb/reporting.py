from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from odds_arb.core.models import ArbitrageOpportunity, MarketKey

DEFAULT_OPPORTUNITY_LOG_PATH = Path("logs/opportunities.log")

OUTCOME_LABELS_PT = {
    "home": "Casa",
    "draw": "Empate",
    "away": "Fora",
    "over": "Mais de 2.5",
    "under": "Menos de 2.5",
    "yes": "Sim",
    "no": "Nao",
    "home_draw": "Casa ou Empate",
    "home_away": "Casa ou Fora",
    "draw_away": "Empate ou Fora",
}
BOOKMAKER_LABELS = {
    "betano": "Betano",
    "kto": "KTO",
    "novibet": "Novibet",
    "pixbet": "Pixbet",
    "superbet": "Superbet",
}
OUTCOME_ORDER: dict[MarketKey, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "over_under_2_5": ("over", "under"),
    "both_teams_score": ("yes", "no"),
    "double_chance": ("home_draw", "home_away", "draw_away"),
}


def format_opportunity_report(
    opportunity: ArbitrageOpportunity,
    *,
    home_team: str,
    away_team: str,
    bankroll: Decimal,
) -> str:
    timestamp = opportunity.detected_at.strftime("%Y-%m-%d %H:%M:%S")
    ordered = _ordered_outcomes(opportunity)

    lines = [
        f"[{timestamp}] ARB ENCONTRADO",
        f"  Match: {home_team} vs {away_team}",
    ]
    for index, outcome_key in enumerate(ordered, start=1):
        odd = opportunity.best_odds[outcome_key]
        casa = BOOKMAKER_LABELS.get(odd.bookmaker, odd.bookmaker.title())
        label = OUTCOME_LABELS_PT.get(outcome_key, outcome_key)
        price = odd.price.quantize(Decimal("0.01"))
        lines.append(f"  Casa {index}: {casa} | Odd: {price} | Outcome: {label}")

    margin = opportunity.profit_pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    lines.append(f"  Margem: {margin}%")

    banca = bankroll.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    stakes_text = " / ".join(
        f"R${opportunity.stakes[outcome_key].quantize(Decimal('1'), rounding=ROUND_HALF_UP)}"
        for outcome_key in ordered
        if outcome_key in opportunity.stakes
    )
    lines.append(f"  Stakes sugeridos (banca R${banca}): {stakes_text}")
    return "\n".join(lines)


def report_opportunities(
    opportunities: Sequence[ArbitrageOpportunity],
    *,
    match_names: Mapping[str, tuple[str, str]],
    bankroll: Decimal,
    log_path: Path = DEFAULT_OPPORTUNITY_LOG_PATH,
) -> list[str]:
    if not opportunities:
        return []

    reports: list[str] = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for opportunity in opportunities:
            home_team, away_team = match_names.get(opportunity.match_id, (opportunity.match_id, ""))
            report = format_opportunity_report(
                opportunity,
                home_team=home_team,
                away_team=away_team,
                bankroll=bankroll,
            )
            print(report)
            handle.write(report + "\n")
            reports.append(report)
    return reports


def _ordered_outcomes(opportunity: ArbitrageOpportunity) -> list[str]:
    order = OUTCOME_ORDER.get(opportunity.market_key, ())
    present = set(opportunity.best_odds)
    ordered = [outcome for outcome in order if outcome in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered
