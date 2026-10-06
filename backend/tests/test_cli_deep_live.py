import odds_arb.cli as cli


def test_deep_scan_loop_disabled_without_flag(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_enabled", lambda: False)
    rc = cli.main(["deep-scan", "--loop"])
    assert rc == 0
    assert "desabilitado" in capsys.readouterr().out


def test_deep_scan_loop_wires_config(monkeypatch):
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    captured = {}

    async def fake_loop(config, **kwargs):
        captured["config"] = config
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr(cli, "run_live_deep_loop", fake_loop)
    rc = cli.main(
        [
            "deep-scan",
            "--loop",
            "--competition",
            "copa-do-mundo",
            "--interval",
            "120",
            "--min-arb",
            "2.5",
            "--bankroll",
            "500",
        ]
    )
    assert rc == 0
    cfg = captured["config"]
    assert cfg.competition.alias == "copa-do-mundo"
    assert float(cfg.interval_seconds) == 120.0
    assert str(cfg.min_profit_pct) == "2.5"
    assert str(cfg.bankroll) == "500"
    # production fetchers are wired
    assert "betano_fetcher" in captured["kwargs"]
    assert "superbet_list_fetcher" in captured["kwargs"]
    assert "superbet_detail_fetcher" in captured["kwargs"]
    assert "sportingbet_list_fetcher" in captured["kwargs"]
    assert "sportingbet_detail_fetcher" in captured["kwargs"]
    assert "kto_list_fetcher" in captured["kwargs"]
    assert "kto_detail_fetcher" in captured["kwargs"]
    assert "estrelabet_list_fetcher" in captured["kwargs"]
    assert "estrelabet_detail_fetcher" in captured["kwargs"]
    assert "novibet_list_fetcher" in captured["kwargs"]
    assert "novibet_detail_fetcher" in captured["kwargs"]


def test_deep_scan_unknown_competition_errors(monkeypatch, capsys):
    monkeypatch.setenv("ENABLE_DEEP_MARKETS", "true")
    rc = cli.main(["deep-scan", "--loop", "--competition", "nao-existe"])
    assert rc == 1
    assert "desconhecida" in capsys.readouterr().out
