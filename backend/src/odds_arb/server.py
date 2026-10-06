from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from jinja2 import Environment, select_autoescape

from odds_arb.store import (
    DEFAULT_ARB_LEG_MAX_SKEW,
    DEFAULT_DB_PATH,
    DEFAULT_ODD_FRESHNESS,
    get_arb_hall_of_fame,
    get_qa_bookmakers,
    get_qa_matches,
    get_qa_summary,
    get_qa_top_candidates,
    get_recent_odds,
    get_recent_opportunities,
)

MARKET_OPTIONS = ["1x2", "over_under_2_5", "both_teams_score", "double_chance"]

_jinja_env = Environment(autoescape=select_autoescape(["html", "xml"]))

PAGE_TEMPLATE = _jinja_env.from_string(
    """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>odds-arb-br</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #121417;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --danger: #b42318;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }
    header {
      padding: 18px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    main { padding: 20px 24px 32px; }
    h1 { margin: 0; font-size: 22px; line-height: 1.2; }
    h2 { margin: 28px 0 12px; font-size: 16px; }
    nav { margin-top: 8px; display: flex; gap: 12px; }
    nav a { color: var(--accent); font-weight: 700; text-decoration: none; }
    form {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: end;
      padding: 14px 0 4px;
    }
    label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
    input, select, button {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 0 10px;
      font: inherit;
    }
    button {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 700;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }
    th, td {
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      font-size: 12px;
      color: var(--muted);
      background: #fbfcfe;
      position: sticky;
      top: 0;
    }
    .num { font-variant-numeric: tabular-nums; white-space: nowrap; }
    .profit { color: var(--accent); font-weight: 700; }
    .empty {
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 16px;
      color: var(--muted);
    }
    .odds-list { display: grid; gap: 4px; }
    .stake-list { display: grid; gap: 4px; }
    @media (max-width: 760px) {
      main, header { padding-left: 12px; padding-right: 12px; }
      table { display: block; overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>odds-arb-br</h1>
    <nav>
      <a href="/">Oportunidades</a>
      <a href="/qa">QA / Odds por casa</a>
    </nav>
    <form method="get">
      <label>Mercado
        <select name="market">
          <option value="" {% if not market %}selected{% endif %}>Todos</option>
          {% for option in market_options %}
          <option value="{{ option }}" {% if market == option %}selected{% endif %}>
            {{ option }}
          </option>
          {% endfor %}
        </select>
      </label>
      <label>Arb minima %
        <input name="min_arb" value="{{ min_arb }}" inputmode="decimal">
      </label>
      <button type="submit">Filtrar</button>
    </form>
  </header>
  <main>
    <h2>Oportunidades</h2>
    {% if opportunities %}
    <table>
      <thead>
        <tr>
          <th>Jogo</th>
          <th>Mercado</th>
          <th>Margem</th>
          <th>Melhores odds</th>
          <th>Stakes R$1000</th>
          <th>Detectado</th>
        </tr>
      </thead>
      <tbody>
      {% for opportunity in opportunities %}
        <tr>
          <td>{{ opportunity.match_id }}</td>
          <td>{{ opportunity.market_key }}</td>
          <td class="num profit">{{ opportunity.profit_pct }}%</td>
          <td>
            <div class="odds-list">
              {% for odd in opportunity.best_odds %}
              <span>{{ odd.outcome_key }}: {{ odd.price }} @ {{ odd.bookmaker }}</span>
              {% endfor %}
            </div>
          </td>
          <td>
            <div class="stake-list">
              {% for key, value in opportunity.stakes.items() %}
              <span>{{ key }}: R$ {{ value }}</span>
              {% endfor %}
            </div>
          </td>
          <td class="num">{{ opportunity.detected_at }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty">Nenhuma oportunidade acima do filtro atual.</div>
    {% endif %}

    <h2>Odds recentes</h2>
    {% if odds %}
    <table>
      <thead>
        <tr>
          <th>Casa</th>
          <th>Jogo</th>
          <th>Mercado</th>
          <th>Outcome</th>
          <th>Odd</th>
          <th>Capturado</th>
        </tr>
      </thead>
      <tbody>
      {% for odd in odds %}
        <tr>
          <td>{{ odd.bookmaker }}</td>
          <td>{{ odd.match_id }}</td>
          <td>{{ odd.market_key }}</td>
          <td>{{ odd.outcome_key }}</td>
          <td class="num">{{ odd.price }}</td>
          <td class="num">{{ odd.captured_at }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty">Nenhuma odd recente salva.</div>
    {% endif %}
  </main>
</body>
</html>
    """
)

QA_TEMPLATE = _jinja_env.from_string(
    """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>QA / Odds por casa - odds-arb-br</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #121417;
      --muted: #667085;
      --line: #d9dee7;
      --accent: #0f766e;
      --warn: #b54708;
      --ok: #067647;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: var(--bg);
      color: var(--text);
      font-size: 13px;
    }
    header {
      padding: 16px 24px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }
    main { padding: 18px 24px 32px; }
    h1 { margin: 0; font-size: 22px; line-height: 1.2; }
    h2 { margin: 22px 0 10px; font-size: 16px; }
    nav { margin-top: 8px; display: flex; gap: 12px; }
    nav a { color: var(--accent); font-weight: 700; text-decoration: none; }
    form {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: end;
      padding-top: 14px;
    }
    label { display: grid; gap: 4px; color: var(--muted); font-size: 12px; }
    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      height: 36px;
      color: var(--text);
    }
    input, select, button {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: #fff;
      padding: 0 10px;
      font: inherit;
    }
    input[type="checkbox"] { height: 16px; width: 16px; padding: 0; }
    button {
      background: var(--accent);
      border-color: var(--accent);
      color: white;
      cursor: pointer;
      font-weight: 700;
    }
    .summary {
      display: grid;
      grid-template-columns: repeat(6, minmax(120px, 1fr));
      gap: 8px;
    }
    .metric, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px 12px;
    }
    .metric b { display: block; margin-bottom: 5px; color: var(--muted); font-size: 12px; }
    .metric span { font-size: 17px; font-weight: 700; }
    .bookmakers {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 10px;
    }
    .top-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 10px;
    }
    .hall-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 12px;
    }
    .record-card {
      position: relative;
      overflow: hidden;
      border-radius: 10px;
      padding: 14px;
      background: linear-gradient(145deg, #ffffff 0%, #f8fffd 100%);
      box-shadow:
        0 1px 2px rgba(16, 24, 40, 0.06),
        0 6px 18px rgba(15, 118, 110, 0.08);
    }
    .record-card::before {
      content: "";
      position: absolute;
      inset: 0 auto 0 0;
      width: 4px;
      background: var(--accent);
    }
    .record-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }
    .record-rank {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
    }
    .record-match {
      display: block;
      margin-top: 3px;
      font-size: 15px;
      text-wrap: balance;
    }
    .record-margin {
      color: var(--ok);
      font-size: 23px;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .record-legs {
      display: grid;
      gap: 5px;
      margin-top: 12px;
    }
    .record-leg {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      border-radius: 6px;
      padding: 6px 8px;
      background: rgba(15, 118, 110, 0.06);
      font-variant-numeric: tabular-nums;
    }
    .record-meta {
      display: grid;
      gap: 3px;
      margin-top: 11px;
      color: var(--muted);
      font-size: 12px;
    }
    .edge-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-left: 4px solid var(--warn);
      border-radius: 6px;
      padding: 10px 12px;
      display: grid;
      gap: 8px;
    }
    .edge-card.arb { border-left-color: var(--ok); background: #f6fef9; }
    .edge-head {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: start;
    }
    .edge-title { display: grid; gap: 2px; }
    .edge-title b { font-size: 14px; text-wrap: balance; }
    .edge-odds { display: flex; flex-wrap: wrap; gap: 4px 8px; }
    .panel h3 { margin: 0 0 8px; font-size: 15px; }
    .meta { display: grid; gap: 4px; color: var(--muted); }
    .examples { margin-top: 8px; display: grid; gap: 5px; }
    .examples div { border-top: 1px solid var(--line); padding-top: 6px; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
    }
    th, td {
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }
    th {
      font-size: 12px;
      color: var(--muted);
      background: #fbfcfe;
      position: sticky;
      top: 0;
    }
    .num { font-variant-numeric: tabular-nums; white-space: nowrap; }
    .tag {
      display: inline-block;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 2px 7px;
      margin: 0 4px 4px 0;
      background: #fff;
      white-space: nowrap;
    }
    .tag.ok { border-color: #abefc6; color: var(--ok); background: #ecfdf3; }
    .tag.warn { border-color: #fedf89; color: var(--warn); background: #fffaeb; }
    .bookmaker-cell { display: grid; gap: 6px; }
    .bookmaker-block {
      display: grid;
      gap: 4px;
      padding-bottom: 6px;
      border-bottom: 1px solid var(--line);
    }
    .bookmaker-block:last-child { border-bottom: 0; padding-bottom: 0; }
    .market-list { display: grid; gap: 3px; }
    .market-line {
      display: grid;
      grid-template-columns: minmax(118px, 160px) 1fr;
      gap: 8px;
    }
    .market-name { color: var(--muted); }
    .odd-chip {
      display: inline-block;
      margin-right: 8px;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .missing { color: var(--warn); }
    .db-source { margin: 0 0 8px; color: var(--muted); }
    .db-source code {
      background: #fbfcfe;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 1px 6px;
    }
    .empty {
      background: var(--panel);
      border: 1px solid var(--line);
      padding: 14px;
      color: var(--muted);
    }
    @media (max-width: 980px) {
      .summary { grid-template-columns: repeat(2, minmax(140px, 1fr)); }
    }
    @media (max-width: 760px) {
      main, header { padding-left: 12px; padding-right: 12px; }
      table { display: block; overflow-x: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>QA / Odds por casa</h1>
    <nav>
      <a href="/">Oportunidades</a>
      <a href="/qa">QA / Odds por casa</a>
    </nav>
    <form method="get" action="/qa">
      <label>Bookmaker
        <select name="bookmaker">
          <option value="" {% if not bookmaker %}selected{% endif %}>Todas</option>
          {% for option in bookmaker_options %}
          <option value="{{ option }}" {% if bookmaker == option %}selected{% endif %}>
            {{ option }}
          </option>
          {% endfor %}
        </select>
      </label>
      <label>Mercado
        <select name="market">
          <option value="" {% if not market %}selected{% endif %}>Todos</option>
          {% for option in market_options %}
          <option value="{{ option }}" {% if market == option %}selected{% endif %}>
            {{ option }}
          </option>
          {% endfor %}
        </select>
      </label>
      <label>Time ou match
        <input name="q" value="{{ q or '' }}">
      </label>
      <label>Limit
        <input name="limit" value="{{ limit }}" inputmode="numeric">
      </label>
      <label class="check">
        <input type="checkbox" name="multi" value="true" {% if only_multi %}checked{% endif %}>
        Somente 2+ casas
      </label>
      <button type="submit">Filtrar</button>
    </form>
  </header>
  <main>
    <h2>Top 3 Arbitragens All-Time</h2>
    {% if hall_of_fame %}
    <section class="hall-grid">
      {% for record in hall_of_fame %}
      <article class="record-card">
        <div class="record-head">
          <div>
            <span class="record-rank">Recorde #{{ record.rank }}</span>
            <b class="record-match">{{ record.home_team }} vs {{ record.away_team }}</b>
          </div>
          <span class="record-margin">+{{ record.profit_pct }}%</span>
        </div>
        <div class="record-legs">
          {% for odd in record.best_odds %}
          <div class="record-leg">
            <b>{{ odd.outcome_key }}</b>
            <span>{{ odd.price }} @ {{ odd.bookmaker }}</span>
          </div>
          {% endfor %}
        </div>
        <div class="record-meta">
          <span>{{ record.league or 'Liga não informada' }} · {{ record.market_key }}</span>
          <span>Jogo: {{ record.starts_at }}</span>
          <span>Detectada em: {{ record.detected_at }}</span>
          <span>Soma 1/odd: {{ record.implied_probability_sum }}</span>
        </div>
      </article>
      {% endfor %}
    </section>
    {% else %}
    <div class="empty">O hall da fama será preenchido quando uma arbitragem for detectada.</div>
    {% endif %}

    <h2>Top 3 quase arbs</h2>
    {% if top_candidates %}
    <section class="top-grid">
      {% for candidate in top_candidates %}
      <article class="edge-card {% if candidate.is_arbitrage %}arb{% endif %}">
        <div class="edge-head">
          <div class="edge-title">
            <b>{{ candidate.home_team }} vs {{ candidate.away_team }}</b>
            <span class="num">
              {{ candidate.market_key }} | soma = {{ candidate.implied_probability_sum }}
            </span>
          </div>
          <span class="tag {% if candidate.is_arbitrage %}ok{% else %}warn{% endif %}">
            {{ candidate.status_label }}
          </span>
        </div>
        <div class="edge-odds">
          {% for odd in candidate.best_odds %}
          <span class="odd-chip">{{ odd.outcome_key }} {{ odd.price }} @ {{ odd.bookmaker }}</span>
          {% endfor %}
        </div>
      </article>
      {% endfor %}
    </section>
    {% else %}
    <div class="empty">Sem candidatos completos nos mercados 1x2, BTTS ou over/under 2.5.</div>
    {% endif %}

    <h2>Resumo do banco</h2>
    <p class="db-source">Fonte: <code>{{ db_label }}</code></p>
    <section class="summary">
      <div class="metric"><b>Odds salvas</b><span>{{ summary.total_odds }}</span></div>
      <div class="metric"><b>Partidas</b><span>{{ summary.total_matches }}</span></div>
      <div class="metric"><b>Oportunidades</b><span>{{ summary.total_opportunities }}</span></div>
      <div class="metric">
        <b>Odd mais recente</b><span>{{ summary.latest_odd_at or '-' }}</span>
      </div>
      <div class="metric">
        <b>Bookmakers</b><span>{{ summary.bookmakers|join(', ') or '-' }}</span>
      </div>
      <div class="metric"><b>Mercados</b><span>{{ summary.markets|join(', ') or '-' }}</span></div>
    </section>

    <h2>Por casa</h2>
    {% if bookmakers %}
    <section class="bookmakers">
      {% for item in bookmakers %}
      <article class="panel">
        <h3>{{ item.bookmaker }}</h3>
        <div class="meta">
          <span><b>{{ item.odds_count }}</b> odds</span>
          <span><b>{{ item.match_count }}</b> partidas</span>
          <span>Mercados: {{ item.markets|join(', ') or '-' }}</span>
          <span>Mais recente: {{ item.latest_odd_at or '-' }}</span>
        </div>
        <div class="examples">
          {% for match in item.recent_matches %}
          <div>
            <b>{{ match.home_team }} vs {{ match.away_team }}</b><br>
            <span class="num">{{ match.starts_at }}</span><br>
            <span>{{ match.markets|join(', ') }}</span>
          </div>
          {% endfor %}
        </div>
      </article>
      {% endfor %}
    </section>
    {% else %}
    <div class="empty">Nenhuma casa encontrada para os filtros atuais.</div>
    {% endif %}

    <h2>Partidas unificadas</h2>
    {% if matches %}
    <table>
      <thead>
        <tr>
          <th>Jogo</th>
          <th>Starts at</th>
          <th>Casas</th>
          <th>Mercados e odds por casa</th>
          <th>Sinal</th>
          <th>Odd mais recente</th>
        </tr>
      </thead>
      <tbody>
      {% for match in matches %}
        <tr>
          <td>
            <b>{{ match.home_team }} vs {{ match.away_team }}</b><br>
            <span class="num">{{ match.match_id }}</span>
          </td>
          <td class="num">{{ match.starts_at }}</td>
          <td>
            {% for bookmaker in match.bookmakers %}
            <span class="tag">{{ bookmaker.bookmaker }}</span>
            {% endfor %}
          </td>
          <td>
            <div class="bookmaker-cell">
              {% for bookmaker in match.bookmakers %}
              <div class="bookmaker-block">
                <div>
                  <b>{{ bookmaker.bookmaker }}</b>
                  <span class="num">{{ bookmaker.odds_count }} odds</span>
                </div>
                {% if bookmaker.market_odds %}
                <div class="market-list">
                  {% for market in bookmaker.market_odds %}
                  <div class="market-line">
                    <span class="market-name">{{ market.market_key }}:</span>
                    <span>
                      {% for selection in market.selections %}
                      <span class="odd-chip">
                        {{ selection.outcome_key }} {{ selection.price }}
                      </span>
                      {% endfor %}
                    </span>
                  </div>
                  {% endfor %}
                </div>
                {% else %}
                <span class="market-name">{{ bookmaker.markets|join(', ') or '-' }}</span>
                {% endif %}
                {% if bookmaker.missing_markets %}
                <span class="missing">
                  mercados faltando: {{ bookmaker.missing_markets|join(', ') }}
                </span>
                {% endif %}
              </div>
              {% endfor %}
            </div>
          </td>
          <td>
            <span class="tag {% if match.bookmaker_count > 1 %}ok{% else %}warn{% endif %}">
              {{ match.status }}
            </span>
          </td>
          <td class="num">{{ match.latest_odd_at or '-' }}</td>
        </tr>
      {% endfor %}
      </tbody>
    </table>
    {% else %}
    <div class="empty">Nenhuma partida encontrada para os filtros atuais.</div>
    {% endif %}
  </main>
</body>
</html>
    """
)


def create_app(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    odd_freshness: timedelta = DEFAULT_ODD_FRESHNESS,
    arb_leg_max_skew: timedelta = DEFAULT_ARB_LEG_MAX_SKEW,
) -> FastAPI:
    app = FastAPI(title="odds-arb-br")

    @app.get("/", response_class=HTMLResponse)
    async def index(
        market: str | None = Query(default=None),
        min_arb: str = Query(default="1.5"),
    ) -> HTMLResponse:
        min_arb_decimal = _parse_decimal(min_arb, Decimal("1.5"))
        opportunities = await get_recent_opportunities(
            db_path=db_path,
            market=market or None,
            min_arb_pct=min_arb_decimal,
            freshness=odd_freshness,
        )
        odds = await get_recent_odds(
            db_path=db_path,
            limit=100,
            freshness=odd_freshness,
        )
        html = PAGE_TEMPLATE.render(
            market=market,
            min_arb=str(min_arb_decimal),
            market_options=MARKET_OPTIONS,
            opportunities=[_view_opportunity(row) for row in opportunities],
            odds=odds,
        )
        return HTMLResponse(html)

    @app.get("/qa", response_class=HTMLResponse)
    async def qa(
        bookmaker: str | None = Query(default=None),
        market: str | None = Query(default=None),
        q: str | None = Query(default=None),
        multi: bool = Query(default=False),
        limit: int = Query(default=50, ge=1, le=500),
    ) -> HTMLResponse:
        selected_bookmaker = _clean_filter(bookmaker)
        selected_market = _clean_filter(market)
        search = _clean_filter(q)
        summary = await get_qa_summary(db_path=db_path, freshness=odd_freshness)
        hall_of_fame = await get_arb_hall_of_fame(db_path=db_path, limit=3)
        top_candidates = await get_qa_top_candidates(
            db_path=db_path,
            freshness=odd_freshness,
            max_leg_skew=arb_leg_max_skew,
        )
        bookmakers = await get_qa_bookmakers(
            db_path=db_path,
            bookmaker=selected_bookmaker,
            market=selected_market,
            q=search,
            freshness=odd_freshness,
        )
        matches = await get_qa_matches(
            db_path=db_path,
            bookmaker=selected_bookmaker,
            market=selected_market,
            q=search,
            only_multi=multi,
            limit=limit,
            freshness=odd_freshness,
        )
        html = QA_TEMPLATE.render(
            summary=summary,
            hall_of_fame=hall_of_fame,
            top_candidates=top_candidates,
            bookmakers=bookmakers,
            matches=matches,
            bookmaker=selected_bookmaker,
            market=selected_market,
            q=search,
            only_multi=multi,
            limit=limit,
            bookmaker_options=summary["bookmakers"],
            market_options=summary["markets"] or MARKET_OPTIONS,
            db_label=str(db_path),
        )
        return HTMLResponse(html)

    return app


app = create_app()


def _parse_decimal(value: str, default: Decimal) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        return default
    if not parsed.is_finite() or parsed < Decimal("0"):
        return default
    return parsed


def _clean_filter(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _view_opportunity(row: Mapping[str, Any]) -> dict[str, Any]:
    best_odds = json.loads(str(row["best_odds_json"]))
    stakes = json.loads(str(row["stakes_json"]))
    profit_pct = Decimal(str(row["profit_pct"])).quantize(Decimal("0.01"))
    return {
        "match_id": row["match_id"],
        "market_key": row["market_key"],
        "profit_pct": profit_pct,
        "best_odds": best_odds,
        "stakes": stakes,
        "detected_at": row["detected_at"],
    }
