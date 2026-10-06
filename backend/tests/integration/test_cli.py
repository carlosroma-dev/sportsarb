from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from odds_arb import cli
from odds_arb.collectors.base import Collector, RawEvent, RawMarket
from odds_arb.core.models import Match, Odd
from odds_arb.scheduler import ScanResult


def _event() -> RawEvent:
    match = Match(
        match_id="flamengo-vasco",
        home_team="Flamengo",
        away_team="Vasco",
        starts_at=datetime(2026, 6, 15, 21, 0, tzinfo=UTC),
    )
    odd = Odd(
        match_id=match.match_id,
        market_key="1x2",
        outcome_key="home",
        price=Decimal("2.10"),
        bookmaker="fake",
    )
    return RawEvent(
        event_id="fake:event",
        bookmaker="fake",
        match=match,
        markets=[RawMarket(market_id="fake:market", label="Resultado", selections=[odd])],
    )


class FakeCollector(Collector):
    name = "fake"

    def __init__(self, events: Sequence[RawEvent]) -> None:
        self._events = list(events)

    async def fetch(self, sport: str = "soccer") -> Sequence[RawEvent]:
        return self._events


def test_cli_help_exits_cleanly() -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--help"])

    assert exc_info.value.code == 0


def test_cli_test_collector_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_collectors", lambda: {"fake": FakeCollector([_event()])})

    assert cli.main(["test-collector", "fake"]) == 0

    output = capsys.readouterr().out
    assert "collector=fake events=1 odds=1" in output
    assert "markets=1x2" in output
    assert "Flamengo x Vasco" in output


def test_cli_test_collector_empty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "_collectors", lambda: {"fake": FakeCollector([])})

    assert cli.main(["test-collector", "fake"]) == 1

    assert "no events returned" in capsys.readouterr().out


def test_cli_scan_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_run_scan_once(**kwargs: object) -> ScanResult:
        now = datetime.now(UTC)
        return ScanResult(
            events=[],
            odds=[],
            opportunities=[],
            raw_matches=0,
            unified_matches=0,
            started_at=now,
            finished_at=now,
        )

    monkeypatch.setattr(cli, "run_scan_once", fake_run_scan_once)

    assert (
        cli.main(
            [
                "scan",
                "--db",
                str(tmp_path / "cli.db"),
                "--bankroll",
                "100",
                "--min-arb",
                "0",
            ]
        )
        == 0
    )

    assert "scan complete events=0 odds=0" in capsys.readouterr().out


def test_cli_run_daemon_reads_interval_from_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_daemon(scanner: object, *, interval_seconds: float, **_: object) -> int:
        captured["interval"] = interval_seconds
        return 0

    monkeypatch.setattr(cli, "run_daemon", fake_run_daemon)
    monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "90")

    assert cli.main(["run", "--db", str(tmp_path / "daemon.db")]) == 0
    assert captured["interval"] == 90.0


def test_cli_run_daemon_interval_flag_overrides_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    captured: dict[str, object] = {}

    async def fake_run_daemon(scanner: object, *, interval_seconds: float, **_: object) -> int:
        captured["interval"] = interval_seconds
        return 0

    monkeypatch.setattr(cli, "run_daemon", fake_run_daemon)
    monkeypatch.setenv("SCAN_INTERVAL_SECONDS", "90")

    assert cli.main(["run", "--db", str(tmp_path / "daemon.db"), "--interval", "30"]) == 0
    assert captured["interval"] == 30.0


def test_cli_serve_invokes_uvicorn(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    def fake_uvicorn_run(app: object, *, host: str, port: int, log_level: str) -> None:
        calls.append({"app": app, "host": host, "port": port, "log_level": log_level})

    monkeypatch.setattr(cli.uvicorn, "run", fake_uvicorn_run)

    assert (
        cli.main(
            [
                "serve",
                "--db",
                str(tmp_path / "serve.db"),
                "--host",
                "127.0.0.1",
                "--port",
                "8123",
            ]
        )
        == 0
    )

    assert calls == [
        {
            "app": calls[0]["app"],
            "host": "127.0.0.1",
            "port": 8123,
            "log_level": "info",
        }
    ]


def test_cli_alert_test_reports_missing_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    assert cli.main(["alert-test"]) == 1

    assert "telegram not configured" in capsys.readouterr().out


def test_deep_scan_disabled_without_flag(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ENABLE_DEEP_MARKETS", raising=False)
    code = cli.main(
        [
            "deep-scan",
            "--betano-fixture",
            "tests/fixtures/deep_markets/betano_event_detail_sample.json",
            "--superbet-fixture",
            "tests/fixtures/deep_markets/superbet_event_detail_sample.json",
            "--audit",
            "ignored.jsonl",
        ]
    )
    assert code == 0
    assert "ENABLE_DEEP_MARKETS" in capsys.readouterr().out


def test_deep_scan_runs_with_fixtures(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    audit = tmp_path / "audit.jsonl"
    code = cli.main(
        [
            "deep-scan",
            "--betano-fixture",
            "tests/fixtures/deep_markets/betano_event_detail_sample.json",
            "--superbet-fixture",
            "tests/fixtures/deep_markets/superbet_event_detail_sample.json",
            "--audit",
            str(audit),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "Deep markets" in out
    assert audit.exists()
