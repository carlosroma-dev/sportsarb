import json
from decimal import Decimal
from pathlib import Path

from odds_arb.deep_scanner import format_report, run_deep_scan

SB = Path("tests/fixtures/deep_markets/superbet_event_detail_sample.json")
BET = Path("tests/fixtures/deep_markets/betano_event_detail_sample.json")


def test_run_deep_scan_finds_cross_house_corners_arb(tmp_path: Path) -> None:
    superbet_event = json.loads(SB.read_text(encoding="utf-8"))["data"][0]
    betano_event = json.loads(BET.read_text(encoding="utf-8"))
    audit = tmp_path / "audit.jsonl"
    report = run_deep_scan(
        superbet_event=superbet_event,
        betano_event=betano_event,
        audit_path=audit,
        min_profit_pct=Decimal("0"),
    )
    # match-total corners 9.5: betano over 2.10 vs superbet under 1.80 -> implied < 1
    corner_arbs = [
        o for o in report.opportunities if o.key[3] == "corners" and o.key[2] == "match_total"
    ]
    assert corner_arbs
    assert report.collected_by_house["superbet"] > 0
    assert report.collected_by_house["betano"] > 0
    assert report.reject_reasons.get("noise_market", 0) >= 1
    assert audit.exists()
    lines = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert any(rec["record_type"] == "opportunity" for rec in lines)


def test_format_report_is_text(tmp_path: Path) -> None:
    audit_path = tmp_path / "unused.jsonl"
    report = run_deep_scan(superbet_event=None, betano_event=None, audit_path=audit_path)
    text = format_report(report)
    assert "Deep markets" in text


# Task 8 — Review pass 3: edge cases


def test_run_deep_scan_handles_empty_events(tmp_path: Path) -> None:
    report = run_deep_scan(
        superbet_event={"odds": []},
        betano_event={"markets": []},
        audit_path=tmp_path / "a.jsonl",
    )
    assert report.opportunities == []
    assert report.normalized == 0
