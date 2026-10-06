import odds_arb.cli as cli


def test_deep_scan_loop_with_tennis_wires_both_loops(monkeypatch):
    # _load_env vira no-op para o .env do operador nao interferir no gate.
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    captured = {}

    async def fake_football(config, **kwargs):
        captured["football"] = config
        return []

    async def fake_tennis(config, **kwargs):
        captured["tennis"] = config
        captured["tennis_kwargs"] = kwargs
        return []

    monkeypatch.setattr(cli, "run_live_deep_loop", fake_football)
    monkeypatch.setattr(cli, "run_tennis_live_loop", fake_tennis)

    rc = cli.main(
        [
            "deep-scan",
            "--loop",
            "--tennis",
            "--competition",
            "serie-b",
            "--interval",
            "60",
            "--min-arb",
            "1.5",
            "--bankroll",
            "500",
        ]
    )
    assert rc == 0
    assert captured["football"].competition.alias == "serie-b"
    tennis_config = captured["tennis"]
    assert float(tennis_config.interval_seconds) == 60.0
    assert str(tennis_config.min_profit_pct) == "1.5"
    assert str(tennis_config.bankroll) == "500"
    assert "betano_fetcher" in captured["tennis_kwargs"]
    assert "superbet_list_fetcher" in captured["tennis_kwargs"]
    assert "superbet_detail_fetcher" in captured["tennis_kwargs"]


def test_deep_scan_loop_without_tennis_flag_skips_tennis(monkeypatch):
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    captured = {}

    async def fake_football(config, **kwargs):
        captured["football"] = config
        return []

    async def fake_tennis(config, **kwargs):
        captured["tennis"] = config
        return []

    monkeypatch.setattr(cli, "run_live_deep_loop", fake_football)
    monkeypatch.setattr(cli, "run_tennis_live_loop", fake_tennis)

    rc = cli.main(["deep-scan", "--loop", "--competition", "serie-b", "--interval", "60"])
    assert rc == 0
    assert "football" in captured
    assert "tennis" not in captured


def test_tennis_scan_disabled_without_flag(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.delenv("ENABLE_DEEP_MARKETS", raising=False)
    rc = cli.main(["tennis-scan"])
    assert rc == 0
    assert "desabilitado" in capsys.readouterr().out


def test_tennis_scan_wires_config_and_fetchers(monkeypatch):
    monkeypatch.setattr(cli, "_load_env", lambda: None)
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    captured = {}

    async def fake_tennis(config, **kwargs):
        captured["config"] = config
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(cli, "run_tennis_live_loop", fake_tennis)
    rc = cli.main(["tennis-scan", "--interval", "90", "--min-arb", "2", "--bankroll", "800"])
    assert rc == 0
    config = captured["config"]
    assert float(config.interval_seconds) == 90.0
    assert str(config.min_profit_pct) == "2"
    assert str(config.bankroll) == "800"
    assert "betano_fetcher" in captured["kwargs"]
    assert "superbet_list_fetcher" in captured["kwargs"]
    assert "superbet_detail_fetcher" in captured["kwargs"]
