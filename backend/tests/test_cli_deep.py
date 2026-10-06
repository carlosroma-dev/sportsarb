import odds_arb.cli as cli
from odds_arb.collectors.deep.betano_live import fetch_betano_events_deep


def test_cli_loop_wires_deep_betano_fetcher():
    # The production loop must use the deep (shots/SOT) fetcher, not SSR-only.
    assert cli.fetch_betano_events is fetch_betano_events_deep


def test_deep_serve_builds_app_with_running_loop(monkeypatch):
    import odds_arb.cli as cli

    captured = {}

    def fake_uvicorn_run(app, host, port, log_level="info"):
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(cli.uvicorn, "run", fake_uvicorn_run)
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")

    rc = cli.main(
        ["deep-serve", "--host", "127.0.0.1", "--port", "8899", "--competition", "copa-do-mundo"]
    )
    assert rc == 0
    assert captured["port"] == 8899
    # the served object is a FastAPI app
    from fastapi import FastAPI

    assert isinstance(captured["app"], FastAPI)


def test_deep_serve_disabled_without_flag(monkeypatch, capsys):
    import odds_arb.cli as cli

    monkeypatch.delenv("ENABLE_DEEP_MARKETS", raising=False)
    rc = cli.main(["deep-serve", "--port", "8899"])
    assert rc == 0
    assert "ENABLE_DEEP_MARKETS" in capsys.readouterr().out


def test_deep_serve_loop_collects_all_and_page_default(monkeypatch):
    from decimal import Decimal

    import odds_arb.cli as cli

    captured = {}

    def fake_build(store, config, **kw):
        captured["min_profit_pct"] = config.min_profit_pct

        async def runner() -> None:
            return None

        return runner

    def fake_create(store, *, loop_runner=None, default_min_arb="0", notify_profit_pct=None):
        captured["default_min_arb"] = default_min_arb
        from fastapi import FastAPI

        return FastAPI()

    monkeypatch.setattr(cli, "build_loop_runner", fake_build)
    monkeypatch.setattr(cli, "create_deep_app", fake_create)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    rc = cli.main(["deep-serve", "--min-arb", "2.0", "--port", "8899"])
    assert rc == 0
    # loop collects all positive arbs; page default carries the CLI --min-arb
    assert captured["min_profit_pct"] == Decimal("0")
    assert captured["default_min_arb"] == "2.0"


def test_deep_serve_passes_notify_threshold(monkeypatch):
    from decimal import Decimal

    import odds_arb.cli as cli

    captured = {}

    def fake_create(store, *, loop_runner=None, default_min_arb="0", notify_profit_pct=None):
        captured["notify"] = notify_profit_pct
        from fastapi import FastAPI

        return FastAPI()

    monkeypatch.setattr(cli, "create_deep_app", fake_create)
    monkeypatch.setattr(cli, "build_loop_runner", lambda *a, **k: lambda: None)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    monkeypatch.delenv("DEEP_NOTIFY_PCT", raising=False)
    rc = cli.main(["deep-serve", "--notify-arb", "6", "--port", "8899"])
    assert rc == 0
    assert captured["notify"] == Decimal("6")
