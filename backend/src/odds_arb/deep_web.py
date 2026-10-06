from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

import structlog
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from jinja2 import Environment, select_autoescape

from odds_arb.core.deep_markets import DeepArbOpportunity, DeepMarketOdd, detect_deep_market_arbs
from odds_arb.core.stake import calculate_stakes
from odds_arb.core.tennis_markets import (
    TennisArbOpportunity,
    TennisMarket,
    TennisMarketOdd,
    detect_tennis_arbs,
)
from odds_arb.deep_live import (
    HIGHLIGHT_PROFIT_PCT,
    SETTLEMENT_SENSITIVE,
    LiveDeepConfig,
    format_cycle_report,
    run_live_deep_cycle,
)
from odds_arb.tennis_live import TennisLiveConfig, run_tennis_live_cycle

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SignalSnapshot:
    opportunities: tuple[DeepArbOpportunity, ...]
    collected_by_house: Mapping[str, int]
    competition_alias: str
    updated_at: datetime
    # Tenis vive lado a lado com o futebol no mesmo snapshot; default vazio
    # mantem todos os construtores existentes funcionando.
    tennis_opportunities: tuple[TennisArbOpportunity, ...] = ()
    # Candidatas frescas de todas as casas (nao so o par vencedor de cada
    # oportunidade), preservadas para recalculo barato por preferencia de
    # usuario (casas vetadas) sem nova coleta. Default vazio preserva
    # construtores existentes (ex.: dashboard QA, testes).
    raw_odds: tuple[DeepMarketOdd, ...] = ()
    tennis_raw_odds: tuple[TennisMarketOdd, ...] = ()


class SignalStore(Protocol):
    def set(self, snapshot: SignalSnapshot) -> None: ...
    def latest(self) -> SignalSnapshot | None: ...
    def update_competition(
        self,
        alias: str,
        *,
        opportunities: tuple[DeepArbOpportunity, ...],
        raw_odds: tuple[DeepMarketOdd, ...],
        collected_by_house: Mapping[str, int],
        updated_at: datetime,
    ) -> None: ...
    def update_tennis(
        self,
        *,
        opportunities: tuple[TennisArbOpportunity, ...],
        raw_odds: tuple[TennisMarketOdd, ...],
        collected_by_house: Mapping[str, int],
        updated_at: datetime,
    ) -> None: ...


@dataclass(frozen=True)
class _CompetitionSlice:
    opportunities: tuple[DeepArbOpportunity, ...]
    raw_odds: tuple[DeepMarketOdd, ...]
    collected_by_house: Mapping[str, int]
    updated_at: datetime


@dataclass(frozen=True)
class _TennisSlice:
    opportunities: tuple[TennisArbOpportunity, ...]
    raw_odds: tuple[TennisMarketOdd, ...]
    collected_by_house: Mapping[str, int]
    updated_at: datetime


class InMemorySignalStore:
    """Guarda o ultimo snapshot completo, mas tambem aceita atualizacoes
    parciais por competicao (update_competition/update_tennis). Cada
    atualizacao parcial recalcula o snapshot agregado na hora, permitindo que
    o dashboard veja uma competicao assim que ela termina de escanear, em vez
    de esperar o ciclo inteiro (todas as competicoes + tenis) terminar."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: SignalSnapshot | None = None
        self._competitions: dict[str, _CompetitionSlice] = {}
        self._tennis: _TennisSlice | None = None

    def set(self, snapshot: SignalSnapshot) -> None:
        with self._lock:
            self._latest = snapshot
            # set() e uma substituicao manual e total: descarta qualquer
            # estado parcial acumulado por update_competition/update_tennis.
            self._competitions = {}
            self._tennis = None

    def latest(self) -> SignalSnapshot | None:
        with self._lock:
            return self._latest

    def update_competition(
        self,
        alias: str,
        *,
        opportunities: tuple[DeepArbOpportunity, ...],
        raw_odds: tuple[DeepMarketOdd, ...],
        collected_by_house: Mapping[str, int],
        updated_at: datetime,
    ) -> None:
        with self._lock:
            self._competitions[alias] = _CompetitionSlice(
                opportunities=opportunities,
                raw_odds=raw_odds,
                collected_by_house=collected_by_house,
                updated_at=updated_at,
            )
            self._latest = self._merge_locked()

    def update_tennis(
        self,
        *,
        opportunities: tuple[TennisArbOpportunity, ...],
        raw_odds: tuple[TennisMarketOdd, ...],
        collected_by_house: Mapping[str, int],
        updated_at: datetime,
    ) -> None:
        with self._lock:
            self._tennis = _TennisSlice(
                opportunities=opportunities,
                raw_odds=raw_odds,
                collected_by_house=collected_by_house,
                updated_at=updated_at,
            )
            self._latest = self._merge_locked()

    def _merge_locked(self) -> SignalSnapshot:
        opportunities: list[DeepArbOpportunity] = []
        raw_odds: list[DeepMarketOdd] = []
        collected_by_house: dict[str, int] = {}
        aliases: list[str] = []
        latest_updated: datetime | None = None
        # dict preserva ordem de insercao: a ordem das competicoes no alias
        # combinado segue a ordem em que cada uma publicou pela 1a vez.
        for alias, comp in self._competitions.items():
            aliases.append(alias)
            opportunities.extend(comp.opportunities)
            raw_odds.extend(comp.raw_odds)
            for house, count in comp.collected_by_house.items():
                collected_by_house[house] = collected_by_house.get(house, 0) + count
            if latest_updated is None or comp.updated_at > latest_updated:
                latest_updated = comp.updated_at
        tennis_opportunities: tuple[TennisArbOpportunity, ...] = ()
        tennis_raw_odds: tuple[TennisMarketOdd, ...] = ()
        if self._tennis is not None:
            tennis_opportunities = self._tennis.opportunities
            tennis_raw_odds = self._tennis.raw_odds
            for house, count in self._tennis.collected_by_house.items():
                collected_by_house[house] = collected_by_house.get(house, 0) + count
            if latest_updated is None or self._tennis.updated_at > latest_updated:
                latest_updated = self._tennis.updated_at
        return SignalSnapshot(
            opportunities=tuple(opportunities),
            collected_by_house=collected_by_house,
            competition_alias=",".join(aliases),
            updated_at=latest_updated or datetime.now(UTC),
            tennis_opportunities=tennis_opportunities,
            raw_odds=tuple(raw_odds),
            tennis_raw_odds=tennis_raw_odds,
        )


@dataclass(frozen=True)
class SignalRow:
    signal_id: str
    match: str
    metric: str
    metric_label: str
    scope_label: str
    subject: str | None
    period_label: str
    line: str
    kickoff: str
    profit_pct: Decimal
    over_bookmaker: str
    over_odd: Decimal
    over_url: str | None
    over_stake: Decimal
    under_bookmaker: str
    under_odd: Decimal
    under_url: str | None
    under_stake: Decimal
    settlement_warning: bool
    highlight: bool
    # Rotulos das pernas. Futebol mantem over/under; no vencedor de tenis cada
    # perna e um jogador.
    over_label: str = "over"
    under_label: str = "under"


def filter_signals(
    opportunities: Sequence[DeepArbOpportunity],
    *,
    min_margin_pct: Decimal = Decimal("0"),
    market: str | None = None,
) -> list[DeepArbOpportunity]:
    result = []
    for opp in opportunities:
        if opp.profit_pct < min_margin_pct:
            continue
        if market is not None and opp.key[3] != market:
            continue
        result.append(opp)
    return result


# Horário de Brasília (UTC-3, fixo desde 2019 — sem horário de verão), para
# exibir o kickoff no fuso do operador sem depender de tzdata no Windows.
_BRT = timezone(timedelta(hours=-3))

# Humanized labels for the signal "model" shown on each card (no raw enum text).
_METRIC_LABELS: dict[str, str] = {
    "corners": "Escanteios",
    "cards": "Cartões",
    "shots": "Finalizações",
    "shots_on_target": "Chutes no gol",
    "fouls": "Faltas",
    "offsides": "Impedimentos",
    "tackles": "Desarmes",
    "throw_ins": "Laterais",
    "goal_kicks": "Tiros de meta",
}
_SCOPE_LABELS: dict[str, str] = {
    "team_total": "por time",
    "match_total": "na partida",
    "handicap": "handicap",
}
_PERIOD_LABELS: dict[str, str] = {
    "full_time": "Tempo integral",
    "first_half": "1º tempo",
    "second_half": "2º tempo",
}

_TENNIS_METRICS: dict[TennisMarket, tuple[str, str]] = {
    TennisMarket.MATCH_WINNER: ("tennis_winner", "Tênis · Vencedor"),
    TennisMarket.MATCH_TOTAL_GAMES: ("tennis_games", "Tênis · Games"),
}

# (value, label) for the market filter dropdown. Empty value = no filter ("Todos").
# Values must match Metric enum values used in the opportunity key.
MARKET_OPTIONS: list[tuple[str, str]] = [
    ("", "Todos os mercados"),
    *((value, _METRIC_LABELS[value]) for value in _METRIC_LABELS),
    *(_TENNIS_METRICS[market] for market in _TENNIS_METRICS),
]

# Margem a partir da qual o painel dispara notificação do navegador (ajustável).
DEFAULT_NOTIFY_PROFIT_PCT = Decimal("8")

# Casas que participam do pareamento deep/tenis (`DeepMarketOdd.bookmaker` /
# `TennisMarketOdd.bookmaker`), na ordem em que aparecem no ciclo de coleta.
# Fonte da lista de veto de casas nas preferencias do usuario.
BOOKMAKER_OPTIONS: list[tuple[str, str]] = [
    ("betano", "Betano"),
    ("superbet", "Superbet"),
    ("sportingbet", "Sportingbet"),
    ("kto", "KTO"),
    ("estrelabet", "Estrela Bet"),
    ("novibet", "Novibet"),
]


def render_rows(opportunities: Sequence[DeepArbOpportunity], bankroll: Decimal) -> list[SignalRow]:
    rows: list[SignalRow] = []
    for opp in opportunities:
        _match, period, family, metric, _subject_key, line = opp.key
        stakes = calculate_stakes({"over": opp.over_leg.odd, "under": opp.under_leg.odd}, bankroll)
        kickoff = opp.over_leg.start_time.astimezone(_BRT).strftime("%d/%m %H:%M")
        signal_id = "|".join("" if part is None else str(part) for part in opp.key)
        rows.append(
            SignalRow(
                signal_id=signal_id,
                match=f"{opp.over_leg.home_team} x {opp.over_leg.away_team}",
                metric=metric,
                metric_label=_METRIC_LABELS.get(metric, metric),
                scope_label=_SCOPE_LABELS.get(family, family),
                # The key's subject is canonicalized (lowercased/no accents) for
                # grouping; show the leg's original team name for display.
                subject=opp.over_leg.subject,
                period_label=_PERIOD_LABELS.get(period, period),
                line=line,
                kickoff=kickoff,
                profit_pct=opp.profit_pct,
                over_bookmaker=opp.over_leg.bookmaker,
                over_odd=opp.over_leg.odd,
                over_url=opp.over_leg.source_event_url,
                over_stake=stakes["over"],
                under_bookmaker=opp.under_leg.bookmaker,
                under_odd=opp.under_leg.odd,
                under_url=opp.under_leg.source_event_url,
                under_stake=stakes["under"],
                settlement_warning=metric in SETTLEMENT_SENSITIVE,
                highlight=opp.profit_pct >= HIGHLIGHT_PROFIT_PCT,
            )
        )
    return rows


def render_tennis_rows(
    opportunities: Sequence[TennisArbOpportunity], bankroll: Decimal
) -> list[SignalRow]:
    rows: list[SignalRow] = []
    for opp in opportunities:
        metric, metric_label = _TENNIS_METRICS[opp.market]
        stakes = calculate_stakes({"a": opp.leg_a.odd, "b": opp.leg_b.odd}, bankroll)
        kickoff = opp.leg_a.start_time.astimezone(_BRT).strftime("%d/%m %H:%M")
        signal_id = "|".join(str(part) for part in opp.key)
        if opp.market is TennisMarket.MATCH_WINNER:
            # Cada perna e um jogador (leg_a/leg_b ja vem ordenadas pelo detector).
            over_label = str(opp.leg_a.winner_player)
            under_label = str(opp.leg_b.winner_player)
            line = ""
        else:
            over_label = "over"
            under_label = "under"
            line = opp.key[2]
        rows.append(
            SignalRow(
                signal_id=signal_id,
                match=f"{opp.leg_a.player_a} x {opp.leg_a.player_b}",
                metric=metric,
                metric_label=metric_label,
                scope_label="na partida",
                subject=None,
                period_label="Jogo completo",
                line=line,
                kickoff=kickoff,
                profit_pct=opp.profit_pct,
                over_bookmaker=opp.leg_a.bookmaker,
                over_odd=opp.leg_a.odd,
                over_url=opp.leg_a.source_event_url,
                over_stake=stakes["a"],
                under_bookmaker=opp.leg_b.bookmaker,
                under_odd=opp.leg_b.odd,
                under_url=opp.leg_b.source_event_url,
                under_stake=stakes["b"],
                settlement_warning=False,
                highlight=opp.profit_pct >= HIGHLIGHT_PROFIT_PCT,
                over_label=over_label,
                under_label=under_label,
            )
        )
    return rows


def filter_tennis_signals(
    opportunities: Sequence[TennisArbOpportunity],
    *,
    min_margin_pct: Decimal = Decimal("0"),
    market: str | None = None,
) -> list[TennisArbOpportunity]:
    result = []
    for opp in opportunities:
        if opp.profit_pct < min_margin_pct:
            continue
        if market is not None and _TENNIS_METRICS[opp.market][0] != market:
            continue
        result.append(opp)
    return result


def _safe_decimal(value: str, *, default: Decimal) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return default
    return parsed if parsed.is_finite() else default


def _opportunities_for_request(
    snapshot: SignalSnapshot, *, excluded_bookmakers: frozenset[str]
) -> tuple[Sequence[DeepArbOpportunity], Sequence[TennisArbOpportunity]]:
    """Universo de oportunidades elegivel para a request, ja sem as casas vetadas.

    Quando o snapshot carrega odds cruas (todo snapshot vindo do loop de
    producao), sempre recalcula a partir delas: barato (mesma logica de
    pareamento, sobre dados ja coletados) e, com casas vetadas, produz o
    proximo melhor par entre as casas restantes em vez de so esconder a
    oportunidade que usava a casa vetada. Sem odds cruas (snapshots antigos/de
    teste sem esse campo), cai nas oportunidades ja computadas pelo loop —
    exclusao de casa nao e possivel nesse caso.
    """
    deep_opportunities: Sequence[DeepArbOpportunity]
    if snapshot.raw_odds:
        deep_odds = [o for o in snapshot.raw_odds if o.bookmaker not in excluded_bookmakers]
        deep_opportunities = detect_deep_market_arbs(deep_odds)
    else:
        deep_opportunities = snapshot.opportunities

    tennis_opportunities: Sequence[TennisArbOpportunity]
    if snapshot.tennis_raw_odds:
        tennis_odds = [
            o for o in snapshot.tennis_raw_odds if o.bookmaker not in excluded_bookmakers
        ]
        tennis_opportunities = detect_tennis_arbs(tennis_odds)
    else:
        tennis_opportunities = snapshot.tennis_opportunities

    return deep_opportunities, tennis_opportunities


def _rows_for_request(
    snapshot: SignalSnapshot | None,
    *,
    competition: str | None,
    min_arb: str,
    market: str | None,
    bankroll: str,
    excluded_bookmakers: frozenset[str] = frozenset(),
) -> list[SignalRow]:
    if snapshot is None:
        return []
    min_margin = _safe_decimal(min_arb, default=Decimal("0"))
    bankroll_value = _safe_decimal(bankroll, default=Decimal("1000"))
    if bankroll_value <= 0:
        bankroll_value = Decimal("1000")
    opportunities, tennis_opportunities = _opportunities_for_request(
        snapshot, excluded_bookmakers=excluded_bookmakers
    )
    rows: list[SignalRow] = []
    # O filtro de competicao se aplica so ao futebol; tenis e global e continua
    # visivel em qualquer aba.
    if competition is None or competition == snapshot.competition_alias:
        filtered = filter_signals(
            opportunities,
            min_margin_pct=min_margin,
            # An empty string (the form's "Todos" option) means no market filter;
            # passing "" would match no metric and wipe every signal.
            market=market or None,
        )
        rows.extend(render_rows(filtered, bankroll_value))
    tennis_filtered = filter_tennis_signals(
        tennis_opportunities,
        min_margin_pct=min_margin,
        market=market or None,
    )
    rows.extend(render_tennis_rows(tennis_filtered, bankroll_value))
    rows.sort(key=lambda row: row.profit_pct, reverse=True)
    return rows


def _row_to_dict(row: SignalRow) -> dict[str, Any]:
    return {
        "signal_id": row.signal_id,
        "match": row.match,
        "metric": row.metric,
        "metric_label": row.metric_label,
        "scope_label": row.scope_label,
        "subject": row.subject,
        "period_label": row.period_label,
        "line": row.line,
        "kickoff": row.kickoff,
        "profit_pct": str(row.profit_pct),
        "over_bookmaker": row.over_bookmaker,
        "over_odd": str(row.over_odd),
        "over_url": row.over_url,
        "over_stake": str(row.over_stake),
        "under_bookmaker": row.under_bookmaker,
        "under_odd": str(row.under_odd),
        "under_url": row.under_url,
        "under_stake": str(row.under_stake),
        "settlement_warning": row.settlement_warning,
        "highlight": row.highlight,
        "over_label": row.over_label,
        "under_label": row.under_label,
    }


_PAGE = Environment(autoescape=select_autoescape(["html", "xml"])).from_string(
    """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>Sinais Deep{% if competition %} — {{ competition }}{% endif %}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500;600;700&family=Fira+Sans:wght@400;500;600;700&display=swap');
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:          #0a0b0e;
      --surface:     #111318;
      --border:      #1c2029;
      --border-hl:   #3ddc84;
      --green:       #3ddc84;
      --amber:       #f5b740;
      --text:        #e2e5ea;
      --muted:       #6b7280;
      --btn-bg:      #1a2235;
      --btn-hover:   #223050;
      --blue:        #5b9dff;
      --mono:        "Fira Code", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      --sans:        "Fira Sans", ui-sans-serif, system-ui, -apple-system, sans-serif;
    }

    body {
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
      font-size: 14px;
      line-height: 1.5;
      min-height: 100dvh;
    }

    .wrap {
      max-width: 1180px;
      margin: 0 auto;
      padding: 20px 16px 48px;
    }

    /* ── signals grid: 3 per row, responsive ── */
    .grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      align-items: start;
    }
    @media (max-width: 900px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 600px) { .grid { grid-template-columns: 1fr; } }

    /* ── header ── */
    .site-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 6px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 16px;
    }
    .site-header h1 {
      font-size: 15px;
      font-weight: 600;
      letter-spacing: -0.01em;
      color: var(--text);
    }
    .age {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--muted);
    }
    .age.warming { color: var(--amber); }
    .header-right { display: flex; align-items: center; gap: 12px; }
    .notify-btn {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 5px 10px;
      font-family: var(--sans);
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s, background 0.15s;
    }
    .notify-btn:hover { color: var(--text); border-color: #2d3a55; }
    .notify-btn.on {
      color: var(--green);
      border-color: rgba(61,220,132,0.45);
      background: rgba(61,220,132,0.08);
    }
    .notify-btn svg { flex-shrink: 0; }

    /* ── filter bar ── */
    .filters {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-bottom: 20px;
    }
    .filters label {
      font-size: 12px;
      color: var(--muted);
      margin-right: 2px;
    }
    .filters input, .filters select {
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 5px 9px;
      font-size: 13px;
      font-family: var(--mono);
      width: auto;
      min-width: 0;
      outline: none;
      transition: border-color 0.15s;
    }
    .filters input:focus, .filters select:focus { border-color: var(--green); }
    .filters .sep { color: var(--border); user-select: none; }
    .btn-apply {
      background: var(--green);
      color: #0a0b0e;
      border: none;
      border-radius: 6px;
      padding: 6px 14px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      font-family: var(--sans);
      transition: opacity 0.15s;
    }
    .btn-apply:hover { opacity: 0.85; }

    /* ── signal card ── */
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 15px 16px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.25);
      transition: border-color 0.18s, box-shadow 0.18s;
    }
    .card:hover {
      border-color: #2b3345;
      box-shadow: 0 6px 20px rgba(0,0,0,0.38);
    }
    .card.hl {
      border-color: var(--border-hl);
      box-shadow: 0 0 0 1px var(--border-hl) inset, 0 6px 22px rgba(61,220,132,0.12);
    }

    /* accessible focus rings (keyboard nav) */
    a:focus-visible, button:focus-visible,
    select:focus-visible, input:focus-visible {
      outline: 2px solid var(--blue);
      outline-offset: 2px;
    }

    /* card top row */
    .card-top {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 6px;
    }
    .profit-badge {
      font-family: var(--mono);
      font-size: 13px;
      font-weight: 700;
      color: var(--green);
      background: rgba(61,220,132,0.10);
      border: 1px solid rgba(61,220,132,0.20);
      border-radius: 5px;
      padding: 2px 8px;
      white-space: nowrap;
      flex-shrink: 0;
    }
    .match-name {
      font-weight: 600;
      font-size: 14px;
      flex: 1;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .warn-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      font-weight: 600;
      color: var(--amber);
      background: rgba(245,183,64,0.10);
      border: 1px solid rgba(245,183,64,0.22);
      border-radius: 5px;
      padding: 2px 7px;
      white-space: nowrap;
    }
    .warn-badge svg { flex-shrink: 0; }

    /* ── signal model (humanized market) — the prominent identity of the bet ── */
    .model {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin: 2px 0 11px;
      padding-bottom: 11px;
      border-bottom: 1px solid var(--border);
    }
    .model-metric {
      font-size: 15px;
      font-weight: 700;
      color: var(--text);
      letter-spacing: -0.01em;
    }
    .model-scope { font-size: 12.5px; color: var(--muted); }
    .team-chip {
      font-family: var(--sans);
      font-size: 12.5px;
      font-weight: 600;
      color: #cfe3ff;
      background: rgba(91,157,255,0.13);
      border: 1px solid rgba(91,157,255,0.30);
      border-radius: 999px;
      padding: 2px 10px;
    }
    .model-period {
      margin-left: auto;
      font-family: var(--mono);
      font-size: 11px;
      color: var(--muted);
      white-space: nowrap;
    }

    /* prominent bet line + kickoff */
    .bet-row {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin: 4px 0 11px;
    }
    .line-badge {
      font-family: var(--mono);
      font-size: 16px;
      font-weight: 700;
      color: var(--text);
      background: #182030;
      border: 1px solid #2c3a55;
      border-radius: 7px;
      padding: 4px 11px;
      letter-spacing: -0.01em;
    }
    .line-badge b { color: var(--green); }
    .kickoff {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-family: var(--mono);
      font-size: 13.5px;
      font-weight: 600;
      color: #cbd2dc;
    }
    .kickoff svg { opacity: 0.6; }

    /* legs */
    .legs { display: flex; flex-direction: column; gap: 6px; }
    .leg {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      background: rgba(255,255,255,0.025);
      border: 1px solid rgba(255,255,255,0.045);
      border-radius: 7px;
      padding: 8px 10px;
    }
    .leg-info {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .leg-side {
      font-family: var(--mono);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.06em;
      color: var(--muted);
      text-transform: uppercase;
    }
    .leg-numbers {
      font-family: var(--mono);
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
    }
    .leg-house { color: var(--muted); font-size: 12px; margin-top: 1px; }
    .leg-stake {
      font-family: var(--mono);
      font-size: 12px;
      color: var(--muted);
    }
    .leg-bk { color: var(--muted); font-size: 12px; }
    .comp-sep { color: var(--muted); font-weight: 400; }
    .btn-house {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: var(--btn-bg);
      color: var(--text);
      text-decoration: none;
      border: 1px solid var(--border);
      border-radius: 7px;
      padding: 8px 14px;
      font-size: 13px;
      font-weight: 500;
      white-space: nowrap;
      flex-shrink: 0;
      transition: background 0.15s, border-color 0.15s;
      min-height: 40px;
    }
    .btn-house:hover { background: var(--btn-hover); border-color: #2d3a55; }
    .btn-house svg { opacity: 0.5; }

    /* empty / warming */
    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 60px 0;
      gap: 10px;
      color: var(--muted);
      text-align: center;
    }
    .empty-state .label { font-size: 15px; }
    .empty-state .sub { font-size: 13px; }
    .dot-pulse {
      font-family: var(--mono);
      font-size: 22px;
      color: var(--amber);
      letter-spacing: 4px;
    }

    /* responsive */
    @media (max-width: 540px) {
      .leg { flex-wrap: wrap; }
      .btn-house { width: 100%; justify-content: center; }
      .card-top { flex-wrap: wrap; }
      .match-name { font-size: 13px; }
    }

    @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { transition: none !important; }
    }
  </style>
</head>
<body>
<svg width="0" height="0" style="display:none">
  <symbol id="ico-ext" viewBox="0 0 12 12" fill="none">
    <path d="M7 1h4v4M11 1 5.5 6.5M5 2H2a1 1 0 0 0-1 1v7a1 1
      0 0 0 1 1h7a1 1 0 0 0 1-1V7"
      stroke="currentColor" stroke-width="1.5"
      stroke-linecap="round" stroke-linejoin="round"/>
  </symbol>
  <symbol id="ico-clock" viewBox="0 0 14 14" fill="none">
    <circle cx="7" cy="7" r="5.5" stroke="currentColor" stroke-width="1.4"/>
    <path d="M7 4v3l2 1.5" stroke="currentColor" stroke-width="1.4"
      stroke-linecap="round" stroke-linejoin="round"/>
  </symbol>
  <symbol id="ico-warn" viewBox="0 0 14 14" fill="none">
    <path d="M7 1.5 13 12H1L7 1.5Z" stroke="currentColor" stroke-width="1.3"
      stroke-linejoin="round"/>
    <path d="M7 5.5v2.6M7 10h.01" stroke="currentColor" stroke-width="1.4"
      stroke-linecap="round"/>
  </symbol>
  <symbol id="ico-bell" viewBox="0 0 14 14" fill="none">
    <path d="M3.2 6a3.8 3.8 0 0 1 7.6 0c0 2.4.7 3.4 1.2 4H2c.5-.6 1.2-1.6 1.2-4Z"
      stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>
    <path d="M5.7 12a1.5 1.5 0 0 0 2.6 0" stroke="currentColor" stroke-width="1.3"
      stroke-linecap="round"/>
  </symbol>
</svg>
<div class="wrap">

  <header class="site-header">
    <h1>Sinais Deep{% if competition %}
      <span class="comp-sep">·</span> {{ competition }}{% endif %}</h1>
    <div class="header-right">
      <button id="notify-btn" type="button" class="notify-btn"
        aria-label="Ativar alertas de arbitragem alta">
        <svg width="13" height="13" aria-hidden="true"><use href="#ico-bell"/></svg>
        <span id="notify-label">alertas ≥{{ notify_pct }}%</span>
      </button>
      {% if age %}
        <span class="age">atualizado {{ age }}</span>
      {% else %}
        <span class="age warming">aquecendo…</span>
      {% endif %}
    </div>
  </header>

  <form class="filters" method="get">
    <label for="f-min">mín %</label>
    <input id="f-min" name="min_arb" value="{{ min_arb }}"
      size="5" title="Margem mínima de lucro (%)">
    <span class="sep">|</span>
    <label for="f-mkt">mercado</label>
    <select id="f-mkt" name="market">
      {% for value, label in market_options %}
        <option value="{{ value }}"
          {{ 'selected' if (market or '') == value else '' }}>{{ label }}</option>
      {% endfor %}
    </select>
    <span class="sep">|</span>
    <label for="f-bank">banca</label>
    <input id="f-bank" name="bankroll" value="{{ bankroll }}" size="8" title="Banca total em R$">
    <button class="btn-apply" type="submit">aplicar</button>
  </form>

  {% if not rows %}
    <div class="empty-state">
      {% if not has_snapshot %}
        <div class="dot-pulse">···</div>
        <div class="label">aquecendo — primeiro scan em andamento</div>
        <div class="sub">A página atualiza automaticamente a cada 30s.</div>
      {% else %}
        <div class="label">Nenhum sinal para o filtro atual.</div>
        <div class="sub">Reduza a margem mínima ou remova o filtro de mercado.</div>
      {% endif %}
    </div>
  {% endif %}

  {% if rows %}
  <div class="grid">
  {% for r in rows %}
    <div class="card {{ 'hl' if r.highlight else '' }}">

      <div class="card-top">
        <span class="profit-badge">+{{ '%.2f'|format(r.profit_pct) }}%</span>
        <span class="match-name">{{ r.match }}</span>
        {% if r.settlement_warning %}
          <span class="warn-badge">
            <svg width="12" height="12" aria-hidden="true"><use href="#ico-warn"/></svg>
            conferir settlement
          </span>
        {% endif %}
      </div>

      <div class="model">
        <span class="model-metric">{{ r.metric_label }}</span>
        <span class="model-scope">{{ r.scope_label }}</span>
        {% if r.subject %}<span class="team-chip">{{ r.subject }}</span>{% endif %}
        <span class="model-period">{{ r.period_label }}</span>
      </div>

      <div class="bet-row">
        <span class="line-badge">linha <b>{{ r.line }}</b></span>
        <span class="kickoff">
          <svg width="13" height="13" aria-hidden="true"><use href="#ico-clock"/></svg>
          {{ r.kickoff }}
        </span>
      </div>

      <div class="legs">
        <div class="leg">
          <div class="leg-info">
            <span class="leg-side">over</span>
            <span class="leg-numbers">
              {{ r.over_odd }}&nbsp;<span class="leg-bk">{{ r.over_bookmaker }}</span>
            </span>
            <span class="leg-stake">R$&nbsp;{{ r.over_stake }}</span>
          </div>
          {% if r.over_url %}
            <a class="btn-house" href="{{ r.over_url }}" target="_blank" rel="noopener noreferrer">
              <svg width="12" height="12" aria-hidden="true">
                <use href="#ico-ext"/>
              </svg>
              {{ r.over_bookmaker }}
            </a>
          {% endif %}
        </div>

        <div class="leg">
          <div class="leg-info">
            <span class="leg-side">under</span>
            <span class="leg-numbers">
              {{ r.under_odd }}&nbsp;<span class="leg-bk">{{ r.under_bookmaker }}</span>
            </span>
            <span class="leg-stake">R$&nbsp;{{ r.under_stake }}</span>
          </div>
          {% if r.under_url %}
            <a class="btn-house" href="{{ r.under_url }}" target="_blank" rel="noopener noreferrer">
              <svg width="12" height="12" aria-hidden="true">
                <use href="#ico-ext"/>
              </svg>
              {{ r.under_bookmaker }}
            </a>
          {% endif %}
        </div>
      </div>

    </div>
  {% endfor %}
  </div>
  {% endif %}

</div>
<script>
(function () {
  var NOTIFY_PCT = "{{ notify_pct }}";
  var btn = document.getElementById('notify-btn');
  var label = document.getElementById('notify-label');
  function enabled() { return localStorage.getItem('deepNotifyOn') === '1'; }
  function seen() {
    try { return new Set(JSON.parse(localStorage.getItem('deepNotifySeen') || '[]')); }
    catch (e) { return new Set(); }
  }
  function saveSeen(s) {
    localStorage.setItem('deepNotifySeen', JSON.stringify(Array.from(s).slice(-500)));
  }
  function refreshBtn() {
    var on = enabled() && ('Notification' in window) && Notification.permission === 'granted';
    if (btn) btn.classList.toggle('on', on);
    if (label) label.textContent = 'alertas ≥' + NOTIFY_PCT + '%' + (on ? ' on' : '');
  }
  async function check() {
    if (!enabled() || !('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      var r = await fetch('/api/signals?min_arb=' + encodeURIComponent(NOTIFY_PCT));
      var data = await r.json();
      var s = seen();
      (data.signals || []).forEach(function (sig) {
        if (s.has(sig.signal_id)) return;
        s.add(sig.signal_id);
        var body = sig.metric_label + ' ' + sig.scope_label +
          (sig.subject ? (' ' + sig.subject) : '') + ' · linha ' + sig.line;
        new Notification('Arb ' + sig.profit_pct + '% — ' + sig.match,
          { body: body, tag: sig.signal_id });
      });
      saveSeen(s);
    } catch (e) { /* network hiccup: ignore, try again next tick */ }
  }
  if (btn) {
    btn.addEventListener('click', function () {
      if (!('Notification' in window)) { alert('Navegador sem suporte a notificacoes.'); return; }
      Notification.requestPermission().then(function (p) {
        localStorage.setItem('deepNotifyOn', p === 'granted' ? '1' : '0');
        refreshBtn();
        if (p === 'granted') check();
      });
    });
  }
  refreshBtn();
  check();
  setInterval(check, 25000);
})();
</script>
</body>
</html>
"""
)


def _age_text(snapshot: SignalSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    secs = int((datetime.now(UTC) - snapshot.updated_at).total_seconds())
    return f"há {secs}s"


def create_app(
    store: SignalStore,
    *,
    loop_runner: Callable[[], Awaitable[None]] | None = None,
    default_min_arb: str = "0",
    notify_profit_pct: Decimal = DEFAULT_NOTIFY_PROFIT_PCT,
) -> FastAPI:
    app = FastAPI(title="odds-arb-br deep signals")
    _background_tasks: set[asyncio.Task[None]] = set()

    if loop_runner is not None:

        @app.on_event("startup")
        async def _start_loop() -> None:
            task = asyncio.create_task(cast(Coroutine[Any, Any, None], loop_runner()))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

    @app.get("/api/signals")
    def api_signals(
        competition: str | None = Query(default=None),
        min_arb: str | None = Query(default=None),
        market: str | None = Query(default=None),
        bankroll: str = Query(default="1000"),
    ) -> dict[str, Any]:
        snapshot = store.latest()
        rows = _rows_for_request(
            snapshot,
            competition=competition,
            min_arb=default_min_arb if min_arb is None else min_arb,
            market=market,
            bankroll=bankroll,
        )
        return {
            "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
            "competition": snapshot.competition_alias if snapshot else None,
            "collected_by_house": dict(snapshot.collected_by_house) if snapshot else {},
            "signals": [_row_to_dict(r) for r in rows],
        }

    @app.get("/", response_class=HTMLResponse)
    def index(
        competition: str | None = Query(default=None),
        min_arb: str | None = Query(default=None),
        market: str | None = Query(default=None),
        bankroll: str = Query(default="1000"),
    ) -> HTMLResponse:
        snapshot = store.latest()
        effective_min_arb = default_min_arb if min_arb is None else min_arb
        rows = _rows_for_request(
            snapshot,
            competition=competition,
            min_arb=effective_min_arb,
            market=market,
            bankroll=bankroll,
        )
        html = _PAGE.render(
            rows=rows,
            has_snapshot=snapshot is not None,
            competition=snapshot.competition_alias if snapshot else None,
            age=_age_text(snapshot),
            min_arb=effective_min_arb,
            market=market,
            market_options=MARKET_OPTIONS,
            bankroll=bankroll,
            notify_pct=f"{notify_profit_pct.normalize():f}",
        )
        return HTMLResponse(html)

    @app.get("/health")
    def health() -> dict[str, Any]:
        snapshot = store.latest()
        return {
            "status": "ok",
            "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
            "signals": len(snapshot.opportunities) if snapshot else 0,
        }

    return app


def _run_tennis_cycle_for_snapshot(
    bankroll: Decimal,
    interval_seconds: float,
    tennis_betano_fetcher: Callable[..., list[Any]],
    tennis_superbet_list_fetcher: Callable[..., Any],
    tennis_superbet_detail_fetcher: Callable[..., Any],
) -> tuple[tuple[TennisArbOpportunity, ...], dict[str, int], tuple[TennisMarketOdd, ...]]:
    """Roda um ciclo de tenis e devolve (oportunidades, contagens por casa, odds cruas).

    Coleta com min_profit 0: o filtro da pagina/API e autoritativo, como no
    futebol. As contagens ganham prefixo ``tenis_`` para nao colidir com as
    casas de futebol no mesmo dict.
    """
    tennis_config = TennisLiveConfig(
        min_profit_pct=Decimal("0"),
        bankroll=bankroll,
        interval_seconds=interval_seconds,
    )
    report = run_tennis_live_cycle(
        tennis_config,
        betano_fetcher=tennis_betano_fetcher,
        superbet_list_fetcher=tennis_superbet_list_fetcher,
        superbet_detail_fetcher=tennis_superbet_detail_fetcher,
        now=datetime.now(UTC),
    )
    counts = {f"tenis_{house}": count for house, count in report.collected_by_house.items()}
    return (
        tuple(staked.opportunity for staked in report.opportunities),
        counts,
        report.fresh_odds_list,
    )


def build_loop_runner(
    store: SignalStore,
    config: LiveDeepConfig,
    *,
    betano_fetcher: Callable[..., list[Any]],
    superbet_list_fetcher: Callable[..., Any],
    superbet_detail_fetcher: Callable[..., Any],
    sportingbet_list_fetcher: Callable[..., Any] | None = None,
    sportingbet_detail_fetcher: Callable[..., Any] | None = None,
    kto_list_fetcher: Callable[..., Any] | None = None,
    kto_detail_fetcher: Callable[..., Any] | None = None,
    estrelabet_list_fetcher: Callable[..., Any] | None = None,
    estrelabet_detail_fetcher: Callable[..., Any] | None = None,
    novibet_list_fetcher: Callable[..., Any] | None = None,
    novibet_detail_fetcher: Callable[..., Any] | None = None,
    tennis_betano_fetcher: Callable[..., list[Any]] | None = None,
    tennis_superbet_list_fetcher: Callable[..., Any] | None = None,
    tennis_superbet_detail_fetcher: Callable[..., Any] | None = None,
    max_iterations: int | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    printer: Callable[[str], None] = print,
) -> Callable[[], Awaitable[None]]:
    # Mesmo caminho do runner combinado (com uma unica competicao): coleta em
    # thread para nao bloquear o event loop da API durante o ciclo.
    return build_combined_loop_runner(
        store,
        [config],
        betano_fetcher=betano_fetcher,
        superbet_list_fetcher=superbet_list_fetcher,
        superbet_detail_fetcher=superbet_detail_fetcher,
        sportingbet_list_fetcher=sportingbet_list_fetcher,
        sportingbet_detail_fetcher=sportingbet_detail_fetcher,
        kto_list_fetcher=kto_list_fetcher,
        kto_detail_fetcher=kto_detail_fetcher,
        estrelabet_list_fetcher=estrelabet_list_fetcher,
        estrelabet_detail_fetcher=estrelabet_detail_fetcher,
        novibet_list_fetcher=novibet_list_fetcher,
        novibet_detail_fetcher=novibet_detail_fetcher,
        tennis_betano_fetcher=tennis_betano_fetcher,
        tennis_superbet_list_fetcher=tennis_superbet_list_fetcher,
        tennis_superbet_detail_fetcher=tennis_superbet_detail_fetcher,
        max_iterations=max_iterations,
        sleep=sleep,
        printer=printer,
    )


def build_combined_loop_runner(
    store: SignalStore,
    configs: Sequence[LiveDeepConfig],
    *,
    betano_fetcher: Callable[..., list[Any]],
    superbet_list_fetcher: Callable[..., Any],
    superbet_detail_fetcher: Callable[..., Any],
    sportingbet_list_fetcher: Callable[..., Any] | None = None,
    sportingbet_detail_fetcher: Callable[..., Any] | None = None,
    kto_list_fetcher: Callable[..., Any] | None = None,
    kto_detail_fetcher: Callable[..., Any] | None = None,
    estrelabet_list_fetcher: Callable[..., Any] | None = None,
    estrelabet_detail_fetcher: Callable[..., Any] | None = None,
    novibet_list_fetcher: Callable[..., Any] | None = None,
    novibet_detail_fetcher: Callable[..., Any] | None = None,
    tennis_betano_fetcher: Callable[..., list[Any]] | None = None,
    tennis_superbet_list_fetcher: Callable[..., Any] | None = None,
    tennis_superbet_detail_fetcher: Callable[..., Any] | None = None,
    max_iterations: int | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    printer: Callable[[str], None] = print,
) -> Callable[[], Awaitable[None]]:
    if not configs:
        raise ValueError("at least one deep competition config is required")

    async def _iteration() -> None:
        for config in configs:
            # Coleta em thread: run_live_deep_cycle e sincrono/bloqueante e a API
            # precisa continuar respondendo durante o ciclo.
            report = await asyncio.to_thread(
                run_live_deep_cycle,
                config,
                betano_fetcher=betano_fetcher,
                superbet_list_fetcher=superbet_list_fetcher,
                superbet_detail_fetcher=superbet_detail_fetcher,
                sportingbet_list_fetcher=sportingbet_list_fetcher,
                sportingbet_detail_fetcher=sportingbet_detail_fetcher,
                kto_list_fetcher=kto_list_fetcher,
                kto_detail_fetcher=kto_detail_fetcher,
                estrelabet_list_fetcher=estrelabet_list_fetcher,
                estrelabet_detail_fetcher=estrelabet_detail_fetcher,
                novibet_list_fetcher=novibet_list_fetcher,
                novibet_detail_fetcher=novibet_detail_fetcher,
                now=datetime.now(UTC),
            )
            printer(format_cycle_report(report, config.competition))
            # Publica assim que ESSA competicao termina — nao espera as
            # demais, entao o dashboard mostra Copa do Mundo na hora que ela
            # fecha o ciclo, sem depender de quanto tempo a Serie B ainda leva.
            store.update_competition(
                config.competition.alias,
                opportunities=tuple(staked.opportunity for staked in report.opportunities),
                raw_odds=tuple(report.fresh_odds_list),
                collected_by_house=dict(report.collected_by_house),
                updated_at=datetime.now(UTC),
            )
        # Tenis roda uma vez por iteracao (nao por competicao de futebol),
        # mas tambem publica assim que termina em vez de esperar a proxima
        # iteracao do loop.
        if (
            tennis_betano_fetcher is not None
            and tennis_superbet_list_fetcher is not None
            and tennis_superbet_detail_fetcher is not None
        ):
            tennis_opportunities, tennis_counts, tennis_raw_odds = await asyncio.to_thread(
                _run_tennis_cycle_for_snapshot,
                configs[0].bankroll,
                configs[0].interval_seconds,
                tennis_betano_fetcher,
                tennis_superbet_list_fetcher,
                tennis_superbet_detail_fetcher,
            )
            store.update_tennis(
                opportunities=tennis_opportunities,
                raw_odds=tennis_raw_odds,
                collected_by_house=tennis_counts,
                updated_at=datetime.now(UTC),
            )

    async def runner() -> None:
        sleeper = sleep or asyncio.sleep
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            try:
                await _iteration()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # um ciclo ruim nao mata o loop da API
                logger.warning("deep_web.cycle_failed", error=str(exc))
            if max_iterations is not None and iteration >= max_iterations:
                break
            await sleeper(configs[0].interval_seconds)

    return runner
