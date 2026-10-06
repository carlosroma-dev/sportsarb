from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from odds_arb.collectors.deep.betano_deep import parse_betano_deep_event
from odds_arb.collectors.deep.superbet_deep import parse_superbet_deep_event
from odds_arb.core.deep_markets import (
    Confidence,
    DeepArbOpportunity,
    DeepMarketNormalizationResult,
    detect_deep_market_arbs,
)


class DeepScanReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    collected_by_house: dict[str, int] = Field(default_factory=dict)
    normalized: int = 0
    medium_confidence: int = 0
    rejected: int = 0
    reject_reasons: dict[str, int] = Field(default_factory=dict)
    opportunities: list[DeepArbOpportunity] = Field(default_factory=list)


def _audit_opportunity(opp: DeepArbOpportunity) -> dict[str, Any]:
    def leg(o: Any) -> dict[str, Any]:
        return {
            "bookmaker": o.bookmaker,
            "raw_market_name": o.raw_market_name,
            "raw_selection_name": o.raw_selection_name,
            "raw_event_id": o.raw_event_id,
            "raw_market_id": o.raw_market_id,
            "raw_selection_id": o.raw_selection_id,
            "odd": str(o.odd),
            "line": str(o.line),
            "raw_line_value": o.raw_line_value,
            "line_source": o.line_source.value,
            "metric": o.metric.value,
            "subject": o.subject,
            "period": o.period.value,
            "market_family": o.market_family.value,
            "competition_name": o.competition_name,
            "source_event_url": o.source_event_url,
        }

    return {
        "record_type": "opportunity",
        "detected_at": opp.detected_at.isoformat(),
        "profit_pct": str(opp.profit_pct),
        "implied_probability_sum": str(opp.implied_probability_sum),
        "key": list(opp.key),
        "over_leg": leg(opp.over_leg),
        "under_leg": leg(opp.under_leg),
    }


def append_opportunity_audit(
    audit_path: Path,
    opportunities: Sequence[DeepArbOpportunity],
) -> None:
    if not opportunities:
        return
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        for opp in opportunities:
            handle.write(json.dumps(_audit_opportunity(opp), ensure_ascii=False) + "\n")


def _audit_rejection(result: DeepMarketNormalizationResult) -> dict[str, Any]:
    return {
        "record_type": "rejection",
        "reject_reason": result.reject_reason.value if result.reject_reason else None,
        "evidence": result.evidence,
    }


def run_deep_scan(
    *,
    superbet_event: Mapping[str, Any] | None,
    betano_event: Mapping[str, Any] | None,
    audit_path: Path,
    min_profit_pct: Decimal = Decimal("0"),
) -> DeepScanReport:
    results: list[DeepMarketNormalizationResult] = []
    collected: dict[str, int] = {}
    if superbet_event is not None:
        sb = parse_superbet_deep_event(superbet_event)
        collected["superbet"] = len(sb)
        results.extend(sb)
    if betano_event is not None:
        bet = parse_betano_deep_event(betano_event)
        collected["betano"] = len(bet)
        results.extend(bet)

    accepted = [r.odd for r in results if r.accepted and r.odd is not None]
    medium = sum(1 for r in results if r.accepted and r.confidence is Confidence.MEDIUM)
    reasons: Counter[str] = Counter(
        r.reject_reason.value for r in results if not r.accepted and r.reject_reason
    )
    opportunities = detect_deep_market_arbs(accepted, min_profit_pct=min_profit_pct)

    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        for opp in opportunities:
            handle.write(json.dumps(_audit_opportunity(opp), ensure_ascii=False) + "\n")
        for result in results:
            if not result.accepted:
                handle.write(json.dumps(_audit_rejection(result), ensure_ascii=False) + "\n")

    return DeepScanReport(
        collected_by_house=collected,
        normalized=len(accepted),
        medium_confidence=medium,
        rejected=len(results) - len(accepted),
        reject_reasons=dict(reasons),
        opportunities=opportunities,
    )


def format_report(report: DeepScanReport) -> str:
    lines = ["Deep markets — relatório"]
    for house, count in sorted(report.collected_by_house.items()):
        lines.append(f"  coletados[{house}]: {count}")
    med = report.medium_confidence
    lines.append(f"  normalizados: {report.normalized} (medium: {med})")
    lines.append(f"  ignorados: {report.rejected}")
    for reason, count in sorted(report.reject_reasons.items(), key=lambda kv: kv[1], reverse=True):
        lines.append(f"    - {reason}: {count}")
    lines.append(f"  oportunidades: {len(report.opportunities)}")
    for opp in report.opportunities:
        over_info = f"{opp.over_leg.bookmaker} {opp.over_leg.odd}"
        under_info = f"{opp.under_leg.bookmaker} {opp.under_leg.odd}"
        line_str = (
            f"    * {opp.key[2]}/{opp.key[3]} subj={opp.key[4]} linha={opp.key[5]} "
            f"lucro={opp.profit_pct:.2f}% "
            f"[over {over_info} | under {under_info}]"
        )
        lines.append(line_str)
    return "\n".join(lines)
