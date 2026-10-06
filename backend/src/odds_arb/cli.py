from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from odds_arb.collectors.base import AdapterCollector, Collector
from odds_arb.collectors.deep.altenar_live import (
    fetch_estrelabet_detail,
    fetch_estrelabet_list,
)
from odds_arb.collectors.deep.betano_live import (
    fetch_betano_events_deep as fetch_betano_events,
)
from odds_arb.collectors.deep.betano_tennis import fetch_betano_tennis_events
from odds_arb.collectors.deep.competitions import resolve_competition
from odds_arb.collectors.deep.kto_live import fetch_kto_detail, fetch_kto_events_deep
from odds_arb.collectors.deep.novibet_live import (
    fetch_novibet_event_detail,
    fetch_novibet_list,
)
from odds_arb.collectors.deep.sportingbet_live import (
    fetch_sportingbet_detail,
    fetch_sportingbet_list,
)
from odds_arb.collectors.deep.superbet_live import fetch_superbet_detail, fetch_superbet_list
from odds_arb.collectors.deep.superbet_tennis import (
    fetch_superbet_tennis_detail,
    fetch_superbet_tennis_list,
)
from odds_arb.collectors.registry import REGISTRY
from odds_arb.deep_live import LiveDeepConfig, run_live_deep_loop
from odds_arb.deep_scanner import format_report, run_deep_scan
from odds_arb.deep_web import (
    DEFAULT_NOTIFY_PROFIT_PCT,
    InMemorySignalStore,
    build_loop_runner,
)
from odds_arb.deep_web import create_app as create_deep_app
from odds_arb.notifier import (
    DEFAULT_MARGIN_CHANGE_PCT,
    DEFAULT_MIN_PROFIT_PCT_NOTIFY,
    TelegramNotifier,
)
from odds_arb.scheduler import Scanner, run_daemon, run_scan_once
from odds_arb.server import create_app
from odds_arb.store import (
    DEFAULT_ARB_LEG_MAX_SKEW,
    DEFAULT_MISSING_GRACE_SCANS,
    DEFAULT_ODD_FRESHNESS,
)
from odds_arb.tennis_live import TennisLiveConfig, run_tennis_live_loop


def _load_json(path: str | None) -> dict[str, object] | None:
    if not path:
        return None
    import json as json_module

    content = json_module.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(content, dict):
        return content
    return None


def _enabled() -> bool:
    return os.getenv("ENABLE_DEEP_MARKETS", "false").strip().lower() in {"1", "true", "yes"}


def _run_deep_scan(args: argparse.Namespace) -> int:
    if not _enabled():
        print("deep-scan desabilitado: defina ENABLE_DEEP_MARKETS=true para rodar.")
        return 0
    if getattr(args, "loop", False):
        return _run_deep_loop(args)
    betano_event = _load_json(args.betano_fixture)
    superbet_payload = _load_json(args.superbet_fixture)
    superbet_event = None
    if isinstance(superbet_payload, dict):
        data = superbet_payload.get("data")
        superbet_event = data[0] if isinstance(data, list) and data else superbet_payload
    report = run_deep_scan(
        superbet_event=superbet_event,
        betano_event=betano_event,
        audit_path=Path(args.audit),
        min_profit_pct=Decimal(str(args.min_arb)),
    )
    print(format_report(report))
    return 0


def _run_deep_loop(args: argparse.Namespace) -> int:
    try:
        competition = resolve_competition(args.competition)
    except KeyError as exc:
        print(str(exc).strip("'"))
        return 1
    interval = (
        args.interval
        if args.interval is not None
        else float(os.getenv("DEEP_SCAN_INTERVAL_SECONDS", str(DEFAULT_DEEP_INTERVAL_SECONDS)))
    )
    min_arb = _decimal_setting(
        args.min_arb if args.min_arb != "0" else None,
        env_name="DEEP_MIN_ARB_PCT",
        default=DEFAULT_DEEP_MIN_ARB_PCT,
    )
    bankroll = _decimal_setting(
        args.bankroll, env_name="BANKROLL_DEFAULT", default=DEFAULT_BANKROLL
    )
    config = LiveDeepConfig(
        competition=competition,
        min_profit_pct=min_arb,
        bankroll=bankroll,
        interval_seconds=interval,
        audit_path=Path(args.audit),
    )
    with_tennis = bool(getattr(args, "tennis", False))
    print(
        f"deep-scan loop iniciado competition={competition.alias} "
        f"tenis={'sim' if with_tennis else 'nao'} "
        f"interval={interval}s min_arb={min_arb}% bankroll=R${bankroll} (Ctrl+C para parar)"
    )

    async def _run_loops() -> None:
        football = run_live_deep_loop(
            config,
            betano_fetcher=fetch_betano_events,
            superbet_list_fetcher=fetch_superbet_list,
            superbet_detail_fetcher=fetch_superbet_detail,
            sportingbet_list_fetcher=fetch_sportingbet_list,
            sportingbet_detail_fetcher=fetch_sportingbet_detail,
            kto_list_fetcher=fetch_kto_events_deep,
            kto_detail_fetcher=fetch_kto_detail,
            estrelabet_list_fetcher=fetch_estrelabet_list,
            estrelabet_detail_fetcher=fetch_estrelabet_detail,
            novibet_list_fetcher=fetch_novibet_list,
            novibet_detail_fetcher=fetch_novibet_event_detail,
        )
        if not with_tennis:
            await football
            return
        tennis_config = TennisLiveConfig(
            min_profit_pct=min_arb,
            bankroll=bankroll,
            interval_seconds=interval,
        )
        tennis = run_tennis_live_loop(
            tennis_config,
            betano_fetcher=fetch_betano_tennis_events,
            superbet_list_fetcher=fetch_superbet_tennis_list,
            superbet_detail_fetcher=fetch_superbet_tennis_detail,
        )
        await asyncio.gather(football, tennis)

    try:
        asyncio.run(_run_loops())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("deep-scan loop encerrado")
    return 0


def _run_tennis_loop(args: argparse.Namespace) -> int:
    if not _enabled():
        print("tennis-scan desabilitado: defina ENABLE_DEEP_MARKETS=true para rodar.")
        return 0
    interval = (
        args.interval
        if args.interval is not None
        else float(os.getenv("DEEP_SCAN_INTERVAL_SECONDS", str(DEFAULT_DEEP_INTERVAL_SECONDS)))
    )
    min_arb = _decimal_setting(
        args.min_arb if args.min_arb != "0" else None,
        env_name="DEEP_MIN_ARB_PCT",
        default=DEFAULT_DEEP_MIN_ARB_PCT,
    )
    bankroll = _decimal_setting(
        args.bankroll, env_name="BANKROLL_DEFAULT", default=DEFAULT_BANKROLL
    )
    config = TennisLiveConfig(
        min_profit_pct=min_arb,
        bankroll=bankroll,
        interval_seconds=interval,
    )
    print(
        f"tennis-scan loop iniciado interval={interval}s "
        f"min_arb={min_arb}% bankroll=R${bankroll} (Ctrl+C para parar)"
    )
    try:
        asyncio.run(
            run_tennis_live_loop(
                config,
                betano_fetcher=fetch_betano_tennis_events,
                superbet_list_fetcher=fetch_superbet_tennis_list,
                superbet_detail_fetcher=fetch_superbet_tennis_detail,
                max_iterations=args.once or None,
            )
        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("tennis-scan loop encerrado")
    return 0


def _run_deep_serve(args: argparse.Namespace) -> int:
    if not _enabled():
        print("deep-serve desabilitado: defina ENABLE_DEEP_MARKETS=true para rodar.")
        return 0
    try:
        competition = resolve_competition(args.competition)
    except KeyError as exc:
        print(str(exc).strip("'"))
        return 1
    interval = (
        args.interval
        if args.interval is not None
        else float(os.getenv("DEEP_SCAN_INTERVAL_SECONDS", str(DEFAULT_DEEP_INTERVAL_SECONDS)))
    )
    min_arb = _decimal_setting(
        args.min_arb if args.min_arb != "0" else None,
        env_name="DEEP_MIN_ARB_PCT",
        default=DEFAULT_DEEP_MIN_ARB_PCT,
    )
    bankroll = _decimal_setting(
        args.bankroll, env_name="BANKROLL_DEFAULT", default=DEFAULT_BANKROLL
    )
    # The loop collects ALL positive arbs (min 0) so the page filter is
    # authoritative — lowering the on-page "mín %" below the CLI value actually
    # reveals more, instead of being capped by a detection-time pre-filter.
    config = LiveDeepConfig(
        competition=competition,
        min_profit_pct=Decimal("0"),
        bankroll=bankroll,
        interval_seconds=interval,
        audit_path=Path("deep_markets_audit.jsonl"),
    )
    notify_arb = _decimal_setting(
        args.notify_arb, env_name="DEEP_NOTIFY_PCT", default=DEFAULT_NOTIFY_PROFIT_PCT
    )
    store = InMemorySignalStore()
    loop_runner = build_loop_runner(
        store,
        config,
        betano_fetcher=fetch_betano_events,
        superbet_list_fetcher=fetch_superbet_list,
        superbet_detail_fetcher=fetch_superbet_detail,
        sportingbet_list_fetcher=fetch_sportingbet_list,
        sportingbet_detail_fetcher=fetch_sportingbet_detail,
        kto_list_fetcher=fetch_kto_events_deep,
        kto_detail_fetcher=fetch_kto_detail,
        estrelabet_list_fetcher=fetch_estrelabet_list,
        estrelabet_detail_fetcher=fetch_estrelabet_detail,
        novibet_list_fetcher=fetch_novibet_list,
        novibet_detail_fetcher=fetch_novibet_event_detail,
    )
    app = create_deep_app(
        store,
        loop_runner=loop_runner,
        default_min_arb=str(min_arb),
        notify_profit_pct=notify_arb,
    )
    print(
        f"deep-serve em http://{args.host}:{args.port} competition={competition.alias} "
        f"interval={interval}s min_arb={min_arb}% notify={notify_arb}% bankroll=R${bankroll}"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


DEFAULT_SCAN_INTERVAL_SECONDS = 120.0
DEFAULT_BANKROLL = Decimal("1000")
DEFAULT_MIN_ARB_PCT = Decimal("1.5")
DEFAULT_DEEP_INTERVAL_SECONDS = 300.0
DEFAULT_DEEP_MIN_ARB_PCT = Decimal("2.0")


def main(argv: Sequence[str] | None = None) -> int:
    _load_env()
    parser = argparse.ArgumentParser(prog="odds-arb")
    subparsers = parser.add_subparsers(dest="command", required=True)

    test_collector_parser = subparsers.add_parser("test-collector")
    test_collector_parser.add_argument("collector", choices=sorted(_collectors()))

    scan_parser = subparsers.add_parser("scan")
    scan_parser.add_argument("--db", default="odds_arb.db")
    scan_parser.add_argument("--bankroll", default=None)
    scan_parser.add_argument("--min-arb", default=None)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--db", default="odds_arb.db")
    run_parser.add_argument("--bankroll", default=None)
    run_parser.add_argument("--min-arb", default=None)
    run_parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="Segundos entre scans. Default le SCAN_INTERVAL_SECONDS do .env (senao 120).",
    )

    serve_parser = subparsers.add_parser("serve")
    serve_parser.add_argument("--db", default="odds_arb.db")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)

    subparsers.add_parser("alert-test")

    deep_parser = subparsers.add_parser("deep-scan")
    deep_parser.add_argument("--betano-fixture", default=None)
    deep_parser.add_argument("--superbet-fixture", default=None)
    deep_parser.add_argument("--audit", default="deep_markets_audit.jsonl")
    deep_parser.add_argument("--min-arb", default="0")
    deep_parser.add_argument("--loop", action="store_true")
    deep_parser.add_argument("--competition", default="copa-do-mundo")
    deep_parser.add_argument("--interval", type=float, default=None)
    deep_parser.add_argument("--bankroll", default=None)
    deep_parser.add_argument(
        "--tennis",
        action="store_true",
        help="Roda o ciclo de tenis (Betano+Superbet) em paralelo ao loop de futebol.",
    )

    tennis_parser = subparsers.add_parser("tennis-scan")
    tennis_parser.add_argument("--min-arb", default="0")
    tennis_parser.add_argument("--interval", type=float, default=None)
    tennis_parser.add_argument("--bankroll", default=None)
    tennis_parser.add_argument(
        "--once",
        type=int,
        default=0,
        help="Roda N ciclos e sai (0 = loop continuo).",
    )

    deep_serve_parser = subparsers.add_parser("deep-serve")
    deep_serve_parser.add_argument("--host", default="127.0.0.1")
    deep_serve_parser.add_argument("--port", type=int, default=8800)
    deep_serve_parser.add_argument("--competition", default="copa-do-mundo")
    deep_serve_parser.add_argument("--interval", type=float, default=None)
    deep_serve_parser.add_argument("--min-arb", default="0")
    deep_serve_parser.add_argument("--bankroll", default=None)
    deep_serve_parser.add_argument("--notify-arb", default=None)

    args = parser.parse_args(argv)
    if args.command == "test-collector":
        collector = _collectors()[args.collector]
        return asyncio.run(_test_collector(collector))
    if args.command == "scan":
        return asyncio.run(
            _scan_once(
                db_path=Path(args.db),
                bankroll=_decimal_setting(
                    args.bankroll,
                    env_name="BANKROLL_DEFAULT",
                    default=DEFAULT_BANKROLL,
                ),
                min_profit_pct=_decimal_setting(
                    args.min_arb,
                    env_name="MIN_ARB_PCT",
                    default=DEFAULT_MIN_ARB_PCT,
                ),
            )
        )
    if args.command == "run":
        return asyncio.run(
            _run_daemon(
                db_path=Path(args.db),
                bankroll=_decimal_setting(
                    args.bankroll,
                    env_name="BANKROLL_DEFAULT",
                    default=DEFAULT_BANKROLL,
                ),
                min_profit_pct=_decimal_setting(
                    args.min_arb,
                    env_name="MIN_ARB_PCT",
                    default=DEFAULT_MIN_ARB_PCT,
                ),
                interval=args.interval,
            )
        )
    if args.command == "serve":
        odd_freshness, arb_leg_max_skew, _missing_grace_scans = _lifecycle_settings()
        app = create_app(
            db_path=Path(args.db),
            odd_freshness=odd_freshness,
            arb_leg_max_skew=arb_leg_max_skew,
        )
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0
    if args.command == "alert-test":
        return asyncio.run(_alert_test())
    if args.command == "deep-scan":
        return _run_deep_scan(args)
    if args.command == "tennis-scan":
        return _run_tennis_loop(args)
    if args.command == "deep-serve":
        return _run_deep_serve(args)
    return 1


async def _test_collector(collector: Collector) -> int:
    events = list(await collector.fetch())
    odds_count = sum(len(event.odds()) for event in events)
    market_keys = sorted({odd.market_key for event in events for odd in event.odds()})
    print(f"collector={collector.name} events={len(events)} odds={odds_count}")
    print(f"markets={', '.join(market_keys) if market_keys else '-'}")
    if not events:
        print("no events returned")
        return 1

    print("bookmaker | match | starts_at | markets | odds")
    print("-" * 88)
    for event in events[:15]:
        match_label = f"{event.match.home_team} x {event.match.away_team}"
        print(
            f"{event.bookmaker} | {match_label} | {event.match.starts_at.isoformat()} | "
            f"{len(event.markets)} | {len(event.odds())}"
        )
    return 0


def _collectors() -> dict[str, Collector]:
    return {name: AdapterCollector(adapter) for name, adapter in REGISTRY.items()}


async def _scan_once(*, db_path: Path, bankroll: Decimal, min_profit_pct: Decimal) -> int:
    _odd_freshness, _arb_leg_max_skew, missing_grace_scans = _lifecycle_settings()
    notifier = _telegram_notifier(db_path=db_path, bankroll=bankroll)
    result = await run_scan_once(
        db_path=db_path,
        bankroll=bankroll,
        min_profit_pct=_detector_threshold(min_profit_pct, notifier),
        missing_grace_scans=missing_grace_scans,
        notifier=notifier,
    )
    print(
        "scan complete "
        f"events={len(result.events)} odds={len(result.odds)} "
        f"raw_matches={result.raw_matches} unified_matches={result.unified_matches} "
        f"opportunities={len(result.opportunities)}"
    )
    if result.opportunities:
        print("match_id | market | profit_pct | outcomes")
        print("-" * 88)
        for opportunity in result.opportunities[:20]:
            outcomes = ", ".join(
                f"{outcome}@{odd.bookmaker}:{odd.price}"
                for outcome, odd in sorted(opportunity.best_odds.items())
            )
            print(
                f"{opportunity.match_id} | {opportunity.market_key} | "
                f"{opportunity.profit_pct.quantize(Decimal('0.01'))}% | {outcomes}"
            )
    return 0


async def _run_daemon(
    *,
    db_path: Path,
    bankroll: Decimal,
    min_profit_pct: Decimal,
    interval: float | None,
) -> int:
    interval_seconds = (
        interval
        if interval is not None
        else float(os.getenv("SCAN_INTERVAL_SECONDS", str(DEFAULT_SCAN_INTERVAL_SECONDS)))
    )
    notifier = _telegram_notifier(db_path=db_path, bankroll=bankroll)
    scanner = Scanner(
        db_path=db_path,
        bankroll=bankroll,
        min_profit_pct=_detector_threshold(min_profit_pct, notifier),
        missing_grace_scans=_lifecycle_settings()[2],
        notifier=notifier,
    )
    print(f"daemon iniciado interval={interval_seconds}s db={db_path} (Ctrl+C para parar)")
    try:
        await run_daemon(scanner, interval_seconds=interval_seconds)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("daemon encerrado")
    return 0


async def _alert_test() -> int:
    notifier = _telegram_notifier(db_path=Path("odds_arb.db"), bankroll=DEFAULT_BANKROLL)
    if not notifier.enabled:
        print("telegram not configured: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        return 1
    if not await notifier.send_startup_message(len(REGISTRY)):
        print("telegram test message failed")
        return 1
    print("telegram test message sent")
    return 0


def _telegram_notifier(*, db_path: Path, bankroll: Decimal) -> TelegramNotifier:
    return TelegramNotifier(
        bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        db_path=db_path,
        min_profit_pct=_decimal_setting(
            None,
            env_name="MIN_PROFIT_PCT_NOTIFY",
            default=DEFAULT_MIN_PROFIT_PCT_NOTIFY,
        ),
        margin_change_pct=_decimal_setting(
            None,
            env_name="MARGIN_CHANGE_PCT_NOTIFY",
            default=DEFAULT_MARGIN_CHANGE_PCT,
        ),
        bankroll=bankroll,
    )


def _detector_threshold(
    configured_min_profit_pct: Decimal,
    notifier: TelegramNotifier,
) -> Decimal:
    if not notifier.enabled:
        return configured_min_profit_pct
    return min(configured_min_profit_pct, notifier.min_profit_pct)


def _decimal_setting(
    cli_value: str | None,
    *,
    env_name: str,
    default: Decimal,
) -> Decimal:
    raw_value = cli_value if cli_value is not None else os.getenv(env_name, str(default))
    return Decimal(raw_value)


def _load_env() -> None:
    env_path = Path.cwd() / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path)


def _lifecycle_settings() -> tuple[timedelta, timedelta, int]:
    odd_freshness_seconds = float(
        os.getenv(
            "ODD_FRESHNESS_SECONDS",
            str(DEFAULT_ODD_FRESHNESS.total_seconds()),
        )
    )
    arb_leg_max_skew_seconds = float(
        os.getenv(
            "ARB_LEG_MAX_SKEW_SECONDS",
            str(DEFAULT_ARB_LEG_MAX_SKEW.total_seconds()),
        )
    )
    missing_grace_scans = int(
        os.getenv("ODD_MISSING_GRACE_SCANS", str(DEFAULT_MISSING_GRACE_SCANS))
    )
    if odd_freshness_seconds <= 0 or arb_leg_max_skew_seconds < 0:
        msg = "lifecycle time windows must be non-negative and freshness must be positive"
        raise ValueError(msg)
    if missing_grace_scans < 1:
        msg = "ODD_MISSING_GRACE_SCANS must be at least 1"
        raise ValueError(msg)
    return (
        timedelta(seconds=odd_freshness_seconds),
        timedelta(seconds=arb_leg_max_skew_seconds),
        missing_grace_scans,
    )


if __name__ == "__main__":
    raise SystemExit(main())
