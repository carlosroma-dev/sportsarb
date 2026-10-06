"""Contrato canonico de tenis pre-jogo (MVP: vencedor da partida + total de games).

Tenis nao cabe no ``DeepMarketOdd`` de futebol: vencedor da partida e um mercado
2-way sem linha/side over-under, e o pareamento entre casas precisa ser
independente da ordem dos jogadores. Este modulo define um contrato minimo e
separado — nada aqui e usado pelo caminho de futebol.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from odds_arb.core.deep_markets import Side

MAX_TENNIS_ODD = Decimal("1000")


class TennisMarket(StrEnum):
    MATCH_WINNER = "match_winner"
    MATCH_TOTAL_GAMES = "match_total_games"


def _norm(value: str) -> str:
    without_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )
    collapsed = re.sub(r"[^a-zA-Z0-9]+", " ", without_accents).strip().lower()
    return re.sub(r"\s+", " ", collapsed)


def canonical_player_key(name: str) -> str:
    """Chave de jogador robusta a abreviacao entre casas.

    Betano lista nome completo ("Andrej Nedic"); Superbet frequentemente abrevia
    ("A.Nedic", "A.C.L.Obregon"). Reduzimos para ``inicial + sobrenome`` (ultimo
    token), que casa nos dois formatos. Nomes de um token so ficam como estao.
    """
    tokens = _norm(name).split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return tokens[0]
    return f"{tokens[0][0]} {tokens[-1]}"


class TennisMarketOdd(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    bookmaker: str = Field(min_length=1)
    raw_event_id: str = Field(min_length=1)
    raw_market_id: str = Field(min_length=1)
    raw_selection_id: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    # player_a/player_b seguem a ordem da casa (home/away). O pareamento entre
    # casas nunca depende dessa ordem: usa frozenset de canonical_player_key.
    player_a: str = Field(min_length=1)
    player_b: str = Field(min_length=1)
    start_time: datetime
    market: TennisMarket
    side: Side | None = None
    line: Decimal | None = None
    winner_player: str | None = None
    odd: Decimal
    raw_market_name: str = Field(min_length=1)
    raw_selection_name: str = Field(min_length=1)
    is_live: bool = False
    competition_name: str | None = None
    sport: Literal["tennis"] = "tennis"
    source_event_url: str | None = None

    @field_validator("start_time")
    @classmethod
    def _start_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "start_time must be timezone-aware"
            raise ValueError(msg)
        return value

    @field_validator("odd")
    @classmethod
    def _odd_in_bounds(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= Decimal("1") or value > MAX_TENNIS_ODD:
            msg = "odd must be finite, greater than 1 and within sanity bounds"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _fields_match_market(self) -> TennisMarketOdd:
        if self.market is TennisMarket.MATCH_WINNER:
            if self.winner_player is None:
                msg = "match_winner requires winner_player"
                raise ValueError(msg)
            if self.side is not None or self.line is not None:
                msg = "match_winner must not have side/line"
                raise ValueError(msg)
            winner_key = canonical_player_key(self.winner_player)
            players = {canonical_player_key(self.player_a), canonical_player_key(self.player_b)}
            if winner_key not in players:
                msg = "winner_player must be one of the event players"
                raise ValueError(msg)
        else:
            if self.side is None or self.line is None:
                msg = "match_total_games requires side and line"
                raise ValueError(msg)
            if self.winner_player is not None:
                msg = "match_total_games must not have winner_player"
                raise ValueError(msg)
            if not self.line.is_finite():
                msg = "line must be finite"
                raise ValueError(msg)
        return self


def tennis_players_key(odd: TennisMarketOdd) -> frozenset[str]:
    return frozenset({canonical_player_key(odd.player_a), canonical_player_key(odd.player_b)})


def build_tennis_match_key(odd: TennisMarketOdd) -> str:
    """Partida canonica: esporte + dia UTC + par de jogadores (ordem-independente).

    Usa o dia UTC (nao bucket de minutos) porque horario de tenis e estimado e
    diverge entre casas. O mesmo PAR de jogadores nao se enfrenta duas vezes no
    mesmo dia em jogos pre-jogo distintos, entao o dia e suficiente e mais seguro
    contra perder arbs por skew de horario.
    """
    day = odd.start_time.astimezone(UTC).date().isoformat()
    players = "|".join(sorted(tennis_players_key(odd)))
    return f"tennis:{day}:{players}"


def build_tennis_market_key(odd: TennisMarketOdd) -> tuple[str, str, str]:
    line = format(odd.line.normalize(), "f") if odd.line is not None else ""
    return (build_tennis_match_key(odd), odd.market.value, line)


class TennisArbOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: tuple[str, str, str]
    market: TennisMarket
    # match_winner: leg_a/leg_b ordenadas pela chave canonica do jogador.
    # match_total_games: leg_a = over, leg_b = under.
    leg_a: TennisMarketOdd
    leg_b: TennisMarketOdd
    implied_probability_sum: Decimal
    profit_pct: Decimal
    detected_at: datetime

    @field_validator("detected_at")
    @classmethod
    def _detected_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "detected_at must be timezone-aware"
            raise ValueError(msg)
        return value


def _best_cross_house_pair(
    a_legs: Sequence[TennisMarketOdd],
    b_legs: Sequence[TennisMarketOdd],
) -> tuple[TennisMarketOdd, TennisMarketOdd, Decimal] | None:
    best: tuple[TennisMarketOdd, TennisMarketOdd, Decimal] | None = None
    for a in a_legs:
        for b in b_legs:
            if a.bookmaker == b.bookmaker:
                continue
            implied = (Decimal("1") / a.odd) + (Decimal("1") / b.odd)
            if best is None or implied < best[2]:
                best = (a, b, implied)
    return best


def _winner_pair(
    group: Sequence[TennisMarketOdd],
) -> tuple[TennisMarketOdd, TennisMarketOdd, Decimal] | None:
    by_player: dict[str, list[TennisMarketOdd]] = defaultdict(list)
    for odd in group:
        if odd.winner_player is not None:
            by_player[canonical_player_key(odd.winner_player)].append(odd)
    if len(by_player) != 2:
        return None
    key_a, key_b = sorted(by_player)
    return _best_cross_house_pair(by_player[key_a], by_player[key_b])


def _total_pair(
    group: Sequence[TennisMarketOdd],
) -> tuple[TennisMarketOdd, TennisMarketOdd, Decimal] | None:
    overs = [o for o in group if o.side is Side.OVER]
    unders = [o for o in group if o.side is Side.UNDER]
    return _best_cross_house_pair(overs, unders)


def detect_tennis_arbs(
    odds: Sequence[TennisMarketOdd],
    *,
    min_profit_pct: Decimal = Decimal("0"),
) -> list[TennisArbOpportunity]:
    grouped: dict[tuple[str, str, str], list[TennisMarketOdd]] = defaultdict(list)
    for odd in odds:
        grouped[build_tennis_market_key(odd)].append(odd)

    opportunities: list[TennisArbOpportunity] = []
    for key, group in grouped.items():
        market = group[0].market
        pair = _winner_pair(group) if market is TennisMarket.MATCH_WINNER else _total_pair(group)
        if pair is None:
            continue
        leg_a, leg_b, implied = pair
        if implied >= Decimal("1"):
            continue
        profit_pct = ((Decimal("1") / implied) - Decimal("1")) * Decimal("100")
        if profit_pct < min_profit_pct:
            continue
        opportunities.append(
            TennisArbOpportunity(
                key=key,
                market=market,
                leg_a=leg_a,
                leg_b=leg_b,
                implied_probability_sum=implied,
                profit_pct=profit_pct,
                detected_at=datetime.now(UTC),
            )
        )
    return sorted(opportunities, key=lambda opp: opp.profit_pct, reverse=True)
