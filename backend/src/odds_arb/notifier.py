from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path

import httpx
import structlog

from odds_arb.core.models import ArbitrageOpportunity, MarketKey
from odds_arb.core.stake import calculate_stakes
from odds_arb.store import (
    DEFAULT_DB_PATH,
    get_opportunity_alert_state,
    opportunity_fingerprint,
    record_opportunity_alert,
)

DEFAULT_MIN_PROFIT_PCT_NOTIFY = Decimal("1.5")
DEFAULT_MARGIN_CHANGE_PCT = Decimal("0.1")
DEFAULT_BANKROLL = Decimal("1000")
DEFAULT_RETRY_DELAY_SECONDS = 2.0
TELEGRAM_TIMEOUT_SECONDS = 10.0
LOCAL_TIMEZONE = timezone(timedelta(hours=-3), name="America/Sao_Paulo")

BOOKMAKER_URLS: dict[str, str] = {
    "esportesdasorte": "https://esportesdasorte.bet.br/ptb/bet/main",
    "sportingbet": "https://www.sportingbet.bet.br/pt-br/sports?popup=betfinder",
    "estrelabet": "https://www.estrelabet.bet.br/aposta-esportiva",
    "kto": "https://www.kto.bet.br/app/esportes/todos-os-esportes/a-z",
    "novibet": "https://www.novibet.bet.br/apostas-esportivas",
    "betnacional": "https://betnacional.bet.br/",
    "superbet": "https://superbet.bet.br/busca",
    "bateubet": "https://bateu.bet.br/sports",
    "pixbet": "https://pix.bet.br/sports?fs=soccer",
}

OUTCOME_ORDER: dict[MarketKey, tuple[str, ...]] = {
    "1x2": ("home", "draw", "away"),
    "over_under_2_5": ("over", "under"),
    "both_teams_score": ("yes", "no"),
    "double_chance": ("home_draw", "home_away", "draw_away"),
}
WEEKDAYS_PT = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OpportunityMessageContext:
    home_team: str
    away_team: str
    starts_at: datetime
    league: str | None = None


class TelegramNotifier:
    def __init__(
        self,
        *,
        bot_token: str | None,
        chat_id: str | None,
        db_path: Path = DEFAULT_DB_PATH,
        min_profit_pct: Decimal = DEFAULT_MIN_PROFIT_PCT_NOTIFY,
        margin_change_pct: Decimal = DEFAULT_MARGIN_CHANGE_PCT,
        bankroll: Decimal = DEFAULT_BANKROLL,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not min_profit_pct.is_finite() or min_profit_pct < Decimal("0"):
            msg = "min_profit_pct must be a non-negative finite Decimal"
            raise ValueError(msg)
        if not margin_change_pct.is_finite() or margin_change_pct < Decimal("0"):
            msg = "margin_change_pct must be a non-negative finite Decimal"
            raise ValueError(msg)
        if not bankroll.is_finite() or bankroll <= Decimal("0"):
            msg = "bankroll must be a positive finite Decimal"
            raise ValueError(msg)

        self.bot_token = (bot_token or "").strip()
        self.chat_id = (chat_id or "").strip()
        self.db_path = db_path
        self.min_profit_pct = min_profit_pct
        self.margin_change_pct = margin_change_pct
        self.bankroll = bankroll
        self._client = client
        self._sleep = sleep
        self._now = now
        if not self.enabled:
            logger.warning(
                "telegram.disabled",
                reason="TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing",
            )

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    async def send_opportunities(
        self,
        opportunities: Sequence[ArbitrageOpportunity],
        *,
        contexts: Mapping[str, OpportunityMessageContext],
    ) -> int:
        sent = 0
        for opportunity in opportunities:
            context = contexts.get(opportunity.match_id)
            if context is None:
                logger.warning(
                    "telegram.alert.skipped_missing_match_context",
                    match_id=opportunity.match_id,
                    market_key=opportunity.market_key,
                )
                continue
            if await self.send_opportunity(opportunity, context=context):
                sent += 1
        return sent

    async def send_opportunity(
        self,
        opportunity: ArbitrageOpportunity,
        *,
        context: OpportunityMessageContext,
    ) -> bool:
        if not self.enabled or opportunity.profit_pct < self.min_profit_pct:
            return False

        fingerprint = opportunity_fingerprint(opportunity)
        try:
            alert_state = await get_opportunity_alert_state(
                fingerprint,
                db_path=self.db_path,
            )
            if alert_state is None:
                logger.info(
                    "telegram.alert.skipped_inactive",
                    match_id=opportunity.match_id,
                    market_key=opportunity.market_key,
                )
                return False

            first_seen_at, last_margin_pct = alert_state
            previous_margin_pct: Decimal | None = None
            if last_margin_pct is not None:
                if abs(opportunity.profit_pct - last_margin_pct) < self.margin_change_pct:
                    logger.info(
                        "telegram.alert.skipped_identical_margin",
                        match_id=opportunity.match_id,
                        market_key=opportunity.market_key,
                        margin_pct=str(opportunity.profit_pct),
                        last_margin_pct=str(last_margin_pct),
                    )
                    return False
                previous_margin_pct = last_margin_pct

            now = self._now()
            message = format_opportunity_message(
                opportunity,
                context=context,
                bankroll=self.bankroll,
                detected_at=first_seen_at,
                now=now,
                previous_margin_pct=previous_margin_pct,
            )
            if not await self._send_text(message):
                return False

            await record_opportunity_alert(
                fingerprint,
                opportunity.profit_pct,
                db_path=self.db_path,
                now=now,
            )
        except Exception as exc:  # Telegram must never break a scan.
            logger.error(
                "telegram.alert.failed",
                match_id=opportunity.match_id,
                market_key=opportunity.market_key,
                error_type=type(exc).__name__,
            )
            return False

        logger.info(
            "telegram.alert.sent",
            match_id=opportunity.match_id,
            market_key=opportunity.market_key,
            margin_pct=str(opportunity.profit_pct),
        )
        return True

    async def send_startup_message(self, bookmaker_count: int) -> bool:
        if not self.enabled:
            return False
        suffix = "casa" if bookmaker_count == 1 else "casas"
        return await self._send_text(
            f"🤖 Scanner iniciado — monitorando {bookmaker_count} {suffix}"
        )

    async def _send_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        if self._client is not None:
            return await self._send_with_retry(self._client, text)
        async with httpx.AsyncClient(timeout=TELEGRAM_TIMEOUT_SECONDS) as client:
            return await self._send_with_retry(client, text)

    async def _send_with_retry(self, client: httpx.AsyncClient, text: str) -> bool:
        for attempt in range(2):
            try:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    raise RuntimeError("Telegram returned an invalid response")
                return True
            except Exception as exc:
                status_code = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                if attempt == 0:
                    logger.warning(
                        "telegram.send.retrying",
                        error_type=type(exc).__name__,
                        status_code=status_code,
                        retry_in_seconds=DEFAULT_RETRY_DELAY_SECONDS,
                    )
                    await self._sleep(DEFAULT_RETRY_DELAY_SECONDS)
                    continue
                logger.error(
                    "telegram.send.failed",
                    error_type=type(exc).__name__,
                    status_code=status_code,
                )
        return False


def format_opportunity_message(
    opportunity: ArbitrageOpportunity,
    *,
    context: OpportunityMessageContext,
    bankroll: Decimal,
    detected_at: datetime,
    now: datetime,
    previous_margin_pct: Decimal | None = None,
) -> str:
    ordered_outcomes = _ordered_outcomes(opportunity)
    stakes = calculate_stakes(
        {outcome: opportunity.best_odds[outcome].price for outcome in ordered_outcomes},
        bankroll,
    )
    guaranteed_return = min(
        stakes[outcome] * opportunity.best_odds[outcome].price for outcome in ordered_outcomes
    )
    guaranteed_profit = guaranteed_return - bankroll
    is_real_arb = opportunity.implied_probability_sum < Decimal("1")
    title = "🟢 ARB REAL" if is_real_arb else "🟡 QUASE-ARB"
    kickoff_status = "PRÉ-JOGO" if context.starts_at > now else "⚠️ AO VIVO"

    odds_lines = []
    for outcome in ordered_outcomes:
        odd = opportunity.best_odds[outcome]
        bookmaker = _format_bookmaker(odd.bookmaker)
        odds_lines.append(f"{escape(outcome):<6} {_format_price(odd.price)} @ {bookmaker}")
    stake_values = " + ".join(f"R${_format_money(stakes[outcome])}" for outcome in ordered_outcomes)
    home_team = escape(context.home_team)
    away_team = escape(context.away_team)
    league = escape(context.league or "Liga não informada")
    market_key = escape(opportunity.market_key)
    change_lines: list[str] = []
    if previous_margin_pct is not None:
        arrow = "📈" if opportunity.profit_pct >= previous_margin_pct else "📉"
        change_lines.append(
            f"{arrow} margem atualizada: "
            f"{_format_percent(abs(previous_margin_pct))}% → "
            f"{_format_percent(abs(opportunity.profit_pct))}%"
        )

    return "\n".join(
        [
            f"{title} +{_format_percent(abs(opportunity.profit_pct))}%",
            f"{home_team} vs {away_team}",
            *change_lines,
            "",
            f"{league} · {market_key} · {kickoff_status}",
            *odds_lines,
            f"Banca R${_format_money(bankroll)} → apostar {stake_values}",
            "",
            (
                f"Retorno garantido: R${_format_money(guaranteed_return)} "
                f"({_format_signed_money(guaranteed_profit)})"
            ),
            f"⏰ Jogo: {_format_datetime(context.starts_at)}",
            "",
            f"🕐 Detectada: {_format_clock(detected_at)} ({_format_age(detected_at, now)})",
        ]
    )


def _format_bookmaker(bookmaker: str) -> str:
    escaped_name = escape(bookmaker)
    url = BOOKMAKER_URLS.get(bookmaker.lower())
    if url is None:
        return escaped_name
    return f'<a href="{escape(url, quote=True)}">{escaped_name}</a>'


def _ordered_outcomes(opportunity: ArbitrageOpportunity) -> list[str]:
    preferred = OUTCOME_ORDER.get(opportunity.market_key, ())
    present = set(opportunity.best_odds)
    ordered = [outcome for outcome in preferred if outcome in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _format_percent(value: Decimal) -> str:
    return (
        format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f").rstrip("0").rstrip(".")
    )


def _format_price(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def _format_money(value: Decimal) -> str:
    rounded = int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return f"{rounded:,}".replace(",", ".")


def _format_signed_money(value: Decimal) -> str:
    sign = "+" if value >= Decimal("0") else "-"
    return f"{sign}R${_format_money(abs(value))}"


def _format_datetime(value: datetime) -> str:
    local = value.astimezone(LOCAL_TIMEZONE)
    return f"{WEEKDAYS_PT[local.weekday()]} {local:%d/%m %H:%M}"


def _format_clock(value: datetime) -> str:
    return value.astimezone(LOCAL_TIMEZONE).strftime("%H:%M")


def _format_age(detected_at: datetime, now: datetime) -> str:
    elapsed_seconds = max(0, int((now - detected_at).total_seconds()))
    if elapsed_seconds < 60:
        return "agora"
    elapsed_minutes = elapsed_seconds // 60
    if elapsed_minutes < 60:
        return f"há {elapsed_minutes}min"
    elapsed_hours = elapsed_minutes // 60
    return f"há {elapsed_hours}h"
