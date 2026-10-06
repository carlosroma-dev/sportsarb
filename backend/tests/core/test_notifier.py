from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import aiosqlite
import httpx

from odds_arb.core.arbitrage import find_arbitrage_opportunities
from odds_arb.core.models import ArbitrageOpportunity, Odd
from odds_arb.notifier import (
    OpportunityMessageContext,
    TelegramNotifier,
    format_opportunity_message,
)
from odds_arb.store import save_scan

DETECTED_AT = datetime(2026, 6, 20, 5, 0, tzinfo=UTC)
NOW = DETECTED_AT + timedelta(minutes=3)
CONTEXT = OpportunityMessageContext(
    home_team="Detroit City",
    away_team="Louisville City FC",
    league="USL Cup",
    starts_at=datetime(2026, 6, 20, 21, 0, tzinfo=UTC),
)


def _opportunity(*, under_price: str = "2.25") -> ArbitrageOpportunity:
    opportunities = find_arbitrage_opportunities(
        [
            Odd(
                match_id="detroit-louisville",
                market_key="over_under_2_5",
                outcome_key="over",
                price=Decimal("1.89"),
                bookmaker="kto",
                captured_at=DETECTED_AT,
            ),
            Odd(
                match_id="detroit-louisville",
                market_key="over_under_2_5",
                outcome_key="under",
                price=Decimal(under_price),
                bookmaker="novibet",
                captured_at=DETECTED_AT,
            ),
        ],
        bankroll=Decimal("1000"),
    )
    return opportunities[0].model_copy(update={"detected_at": DETECTED_AT})


async def _activate(db_path, opportunity: ArbitrageOpportunity, *, seen_at: datetime) -> None:
    await save_scan(
        [],
        [opportunity],
        db_path=db_path,
        started_at=seen_at,
        ended_at=seen_at,
    )


def test_format_opportunity_message_is_actionable() -> None:
    message = format_opportunity_message(
        _opportunity(),
        context=CONTEXT,
        bankroll=Decimal("1000"),
        detected_at=DETECTED_AT,
        now=NOW,
    )

    assert "🟢 ARB REAL +2.72%" in message
    assert "Detroit City vs Louisville City FC" in message
    assert "USL Cup · over_under_2_5 · PRÉ-JOGO" in message
    assert (
        'over   1.89 @ <a href="https://www.kto.bet.br/app/esportes/todos-os-esportes/a-z">kto</a>'
    ) in message
    assert (
        'under  2.25 @ <a href="https://www.novibet.bet.br/apostas-esportivas">novibet</a>'
    ) in message
    assert "Banca R$1.000 → apostar R$543 + R$457" in message
    assert "Retorno garantido: R$1.027 (+R$27)" in message
    assert "⏰ Jogo: sáb 20/06 18:00" in message
    assert "🕐 Detectada: 02:00 (há 3min)" in message


def test_format_opportunity_message_keeps_bookmaker_without_url_as_text() -> None:
    opportunity = _opportunity()
    betano_odd = opportunity.best_odds["under"].model_copy(update={"bookmaker": "betano"})
    opportunity = opportunity.model_copy(
        update={"best_odds": {**opportunity.best_odds, "under": betano_odd}}
    )

    message = format_opportunity_message(
        opportunity,
        context=CONTEXT,
        bankroll=Decimal("1000"),
        detected_at=DETECTED_AT,
        now=NOW,
    )

    assert "under  2.25 @ betano" in message
    assert ">betano</a>" not in message


def test_format_opportunity_message_escapes_dynamic_html_text() -> None:
    context = OpportunityMessageContext(
        home_team="A&B <FC>",
        away_team="C&D",
        league="Cup & League <Final>",
        starts_at=CONTEXT.starts_at,
    )

    message = format_opportunity_message(
        _opportunity(),
        context=context,
        bankroll=Decimal("1000"),
        detected_at=DETECTED_AT,
        now=NOW,
    )

    assert "A&amp;B &lt;FC&gt; vs C&amp;D" in message
    assert "Cup &amp; League &lt;Final&gt; · over_under_2_5" in message


def test_format_marks_near_arb_and_live_event() -> None:
    opportunity = _opportunity().model_copy(
        update={
            "implied_probability_sum": Decimal("1.008"),
            "profit_pct": Decimal("0.8"),
        }
    )
    live_context = OpportunityMessageContext(
        home_team=CONTEXT.home_team,
        away_team=CONTEXT.away_team,
        league=CONTEXT.league,
        starts_at=NOW - timedelta(minutes=5),
    )

    message = format_opportunity_message(
        opportunity,
        context=live_context,
        bankroll=Decimal("1000"),
        detected_at=DETECTED_AT,
        now=NOW,
    )

    assert "🟡 QUASE-ARB +0.8%" in message
    assert "· ⚠️ AO VIVO" in message


async def test_send_uses_formatted_message_and_skips_identical_margin(tmp_path) -> None:
    db_path = tmp_path / "notifier.db"
    opportunity = _opportunity()
    await _activate(db_path, opportunity, seen_at=DETECTED_AT)
    payloads: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            now=lambda: NOW,
        )
        assert await notifier.send_opportunity(opportunity, context=CONTEXT)
        assert not await notifier.send_opportunity(opportunity, context=CONTEXT)

    assert len(payloads) == 1
    assert payloads[0]["chat_id"] == "chat"
    assert payloads[0]["parse_mode"] == "HTML"
    assert "🟢 ARB REAL +2.72%" in payloads[0]["text"]


async def test_margin_change_triggers_new_alert(tmp_path) -> None:
    db_path = tmp_path / "margin-change.db"
    first = _opportunity()
    second = _opportunity(under_price="2.30")
    await _activate(db_path, first, seen_at=DETECTED_AT)
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            now=lambda: NOW,
        )
        assert await notifier.send_opportunity(first, context=CONTEXT)
        await _activate(db_path, second, seen_at=DETECTED_AT + timedelta(minutes=2))
        assert await notifier.send_opportunity(second, context=CONTEXT)

    assert requests == 2
    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall(
            "SELECT COUNT(*), MIN(first_seen_at), MAX(last_seen_at) FROM opportunities"
        )
    assert rows == [(1, DETECTED_AT.isoformat(), (DETECTED_AT + timedelta(minutes=2)).isoformat())]


async def test_stable_margin_does_not_renotify_after_restart(tmp_path) -> None:
    db_path = tmp_path / "restart.db"
    opportunity = _opportunity()
    await _activate(db_path, opportunity, seen_at=DETECTED_AT)
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first_run = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            now=lambda: NOW,
        )
        assert await first_run.send_opportunity(opportunity, context=CONTEXT)

        # Simulate a daemon restart: brand new notifier, same persisted state.
        after_restart = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            now=lambda: NOW + timedelta(minutes=27),
        )
        assert not await after_restart.send_opportunity(opportunity, context=CONTEXT)

    assert requests == 1


async def test_margin_change_below_threshold_is_silenced(tmp_path) -> None:
    db_path = tmp_path / "below-threshold.db"
    opportunity = _opportunity().model_copy(update={"profit_pct": Decimal("2.97")})
    await _activate(db_path, opportunity, seen_at=DETECTED_AT)
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            now=lambda: NOW,
        )
        assert await notifier.send_opportunity(opportunity, context=CONTEXT)
        # 2.97 -> 2.99 is below the 0.1pp change threshold.
        wobble = opportunity.model_copy(update={"profit_pct": Decimal("2.99")})
        assert not await notifier.send_opportunity(wobble, context=CONTEXT)

    assert requests == 1


async def test_margin_change_above_threshold_includes_change_indicator(tmp_path) -> None:
    db_path = tmp_path / "above-threshold.db"
    opportunity = _opportunity().model_copy(update={"profit_pct": Decimal("2.63")})
    await _activate(db_path, opportunity, seen_at=DETECTED_AT)
    payloads: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            now=lambda: NOW,
        )
        assert await notifier.send_opportunity(opportunity, context=CONTEXT)
        updated = opportunity.model_copy(update={"profit_pct": Decimal("2.97")})
        assert await notifier.send_opportunity(updated, context=CONTEXT)

    assert len(payloads) == 2
    assert "margem atualizada" in payloads[1]["text"]
    assert "2.63% → 2.97%" in payloads[1]["text"]
    assert "📈" in payloads[1]["text"]
    assert "margem atualizada" not in payloads[0]["text"]


async def test_notification_threshold_requires_at_least_1_5_percent(tmp_path) -> None:
    db_path = tmp_path / "threshold.db"
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    below_threshold = _opportunity().model_copy(update={"profit_pct": Decimal("1.2")})
    above_threshold = _opportunity().model_copy(update={"profit_pct": Decimal("1.6")})
    await _activate(db_path, above_threshold, seen_at=DETECTED_AT)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            min_profit_pct=Decimal("1.5"),
        )
        assert not await notifier.send_opportunity(below_threshold, context=CONTEXT)
        assert await notifier.send_opportunity(above_threshold, context=CONTEXT)

    assert requests == 1


async def test_inactive_opportunity_does_not_send(tmp_path) -> None:
    db_path = tmp_path / "inactive.db"
    opportunity = _opportunity()
    await _activate(db_path, opportunity, seen_at=DETECTED_AT)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("UPDATE opportunities SET active = 0")
        await db.commit()
    requests = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
        )
        assert not await notifier.send_opportunity(opportunity, context=CONTEXT)

    assert requests == 0


async def test_telegram_failure_retries_once_and_does_not_propagate(tmp_path) -> None:
    db_path = tmp_path / "failure.db"
    opportunity = _opportunity()
    await _activate(db_path, opportunity, seen_at=DETECTED_AT)
    requests = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise httpx.ConnectError("offline", request=request)

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            db_path=db_path,
            client=client,
            sleep=fake_sleep,
        )
        assert not await notifier.send_opportunity(opportunity, context=CONTEXT)

    assert requests == 2
    assert sleeps == [2.0]


async def test_startup_message_reports_monitored_bookmakers() -> None:
    payloads: list[dict[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = TelegramNotifier(
            bot_token="token",
            chat_id="chat",
            client=client,
        )
        assert await notifier.send_startup_message(12)

    assert payloads[0]["text"] == "🤖 Scanner iniciado — monitorando 12 casas"
    assert payloads[0]["parse_mode"] == "HTML"
