from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from odds_arb.core.dedup import canonical_match_id, canonical_team_name
from odds_arb.core.models import Match


class Period(StrEnum):
    FULL_TIME = "full_time"
    FIRST_HALF = "first_half"
    SECOND_HALF = "second_half"
    UNKNOWN = "unknown"


class MarketFamily(StrEnum):
    TEAM_TOTAL = "team_total"
    MATCH_TOTAL = "match_total"
    HANDICAP = "handicap"
    UNKNOWN = "unknown"


class Metric(StrEnum):
    SHOTS_ON_TARGET = "shots_on_target"
    SHOTS = "shots"
    CORNERS = "corners"
    CARDS = "cards"
    FOULS = "fouls"
    OFFSIDES = "offsides"
    TACKLES = "tackles"
    THROW_INS = "throw_ins"
    GOAL_KICKS = "goal_kicks"
    UNKNOWN = "unknown"


class Side(StrEnum):
    OVER = "over"
    UNDER = "under"


class LineSource(StrEnum):
    SELECTION_HANDICAP = "selection_handicap"
    SELECTION_NAME_REGEX = "selection_name_regex"
    TABLE_LAYOUT_ROW = "table_layout_row"
    MARKET_PARAMETER = "market_parameter"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    REJECT = "reject"


class RejectReason(StrEnum):
    NO_METRIC = "no_metric"
    NO_SIDE = "no_side"
    NO_LINE = "no_line"
    SUBJECT_UNRESOLVED = "subject_unresolved"
    NOISE_MARKET = "noise_market"
    AMBIGUOUS_PERIOD = "ambiguous_period"
    HANDICAP_MARKET = "handicap_market"
    SUSPENDED = "suspended"
    LIVE = "live"
    ODD_OUT_OF_BOUNDS = "odd_out_of_bounds"


class DeepMarketOdd(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    bookmaker: str = Field(min_length=1)
    raw_event_id: str = Field(min_length=1)
    raw_market_id: str = Field(min_length=1)
    raw_selection_id: str = Field(min_length=1)
    event_name: str = Field(min_length=1)
    home_team: str = Field(min_length=1)
    away_team: str = Field(min_length=1)
    start_time: datetime
    period: Period
    market_family: MarketFamily
    metric: Metric
    subject: str | None = None
    side: Side
    odd: Decimal
    raw_market_name: str = Field(min_length=1)
    raw_selection_name: str = Field(min_length=1)
    is_live: bool = False
    is_available: bool | None = None
    line: Decimal
    raw_line_value: str | None = None
    line_source: LineSource = LineSource.UNKNOWN
    competition_name: str | None = None
    competition_id: str | None = None
    country: str | None = None
    sport: str | None = "soccer"
    source_event_url: str | None = None

    @field_validator("start_time")
    @classmethod
    def _start_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "start_time must be timezone-aware"
            raise ValueError(msg)
        return value

    @field_validator("odd", "line")
    @classmethod
    def _must_be_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            msg = "odd/line must be finite"
            raise ValueError(msg)
        return value

    @field_validator("odd")
    @classmethod
    def _odd_above_one(cls, value: Decimal) -> Decimal:
        if value <= Decimal("1"):
            msg = "odd must be greater than 1"
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _subject_matches_family(self) -> DeepMarketOdd:
        if self.market_family is MarketFamily.TEAM_TOTAL and self.subject is None:
            msg = "team_total requires subject"
            raise ValueError(msg)
        if self.market_family is MarketFamily.MATCH_TOTAL and self.subject is not None:
            msg = "match_total must not have subject"
            raise ValueError(msg)
        return self


MAX_ODD = Decimal("1000")


class DeepMarketNormalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    odd: DeepMarketOdd | None
    reject_reason: RejectReason | None
    confidence: Confidence
    evidence: dict[str, object] = Field(default_factory=dict)


def _norm(value: str) -> str:
    without_accents = "".join(
        ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch)
    )
    collapsed = re.sub(r"[^a-zA-Z0-9]+", " ", without_accents).strip().lower()
    return re.sub(r"\s+", " ", collapsed)


_NOISE_PATTERNS = (
    "trave",
    "fora da area",
    "vermelh",
    "red card",
    "faixa",
    "range",
    "handicap",
    "asiatic",
    "exato",
    "exact",
    "impar",
    "par ou impar",
    "corrida",
    "cada equipe",
    "cada time",
    "primeiro a marcar",
    "primeiro gol",
    "1 chute",
    "1o chute",
    "1 falta",
    "1o falta",
    "1 finaliza",
    "1o finaliza",
    "equipe com mais",
    "time com mais",
    "media",
    "00 00",
)


def _is_noise(market_norm: str, raw_market_name: str) -> bool:
    if ";" in raw_market_name:  # bet-builder combo
        return True
    return any(token in market_norm for token in _NOISE_PATTERNS)


# Tokens used to tell match-total names ("Total de Escanteios") apart from a
# team-prefixed name that references an unknown team ("Barcelona Escanteios").
_METRIC_PHRASE_TOKENS: dict[Metric, frozenset[str]] = {
    Metric.SHOTS_ON_TARGET: frozenset(
        {"chutes", "chute", "no", "gol", "gols", "shots", "shot", "on", "target", "sot"}
    ),
    Metric.SHOTS: frozenset(
        {"chutes", "chute", "finalizacoes", "finalizacao", "remates", "remate", "shots", "shot"}
    ),
    Metric.CORNERS: frozenset({"escanteios", "escanteio", "corners", "corner"}),
    Metric.CARDS: frozenset({"cartoes", "cartao", "cards", "card", "booking", "bookings"}),
    Metric.FOULS: frozenset({"faltas", "falta", "fouls", "foul"}),
    Metric.OFFSIDES: frozenset({"impedimentos", "impedimento", "offsides", "offside"}),
    Metric.TACKLES: frozenset({"desarmes", "desarme", "tackles", "tackle"}),
    Metric.THROW_INS: frozenset({"laterais", "lateral", "arremessos", "arremesso"}),
    Metric.GOAL_KICKS: frozenset({"tiros", "tiro", "meta", "goal", "kicks", "kick"}),
}
_CONNECTIVE_TOKENS = frozenset(
    {"total", "de", "da", "do", "mais", "menos", "partida", "jogo", "na", "no", "e", "ou"}
)
_PERIOD_TOKENS = frozenset(
    {"1o", "2o", "1", "2", "o", "tempo", "primeiro", "segundo", "first", "second", "half", "ht"}
)


def _detect_period(market_norm: str) -> Period | None:
    # Returns None when ambiguous/unresolved (caller rejects).
    if "intervalo" in market_norm:
        return None
    first_half_patterns = (
        re.search(r"\b1\s*o?\s*tempo\b", market_norm)
        or "primeiro tempo" in market_norm
        or "first half" in market_norm
    )
    if first_half_patterns:
        return Period.FIRST_HALF
    second_half_patterns = (
        re.search(r"\b2\s*o?\s*tempo\b", market_norm)
        or "segundo tempo" in market_norm
        or "second half" in market_norm
    )
    if second_half_patterns:
        return Period.SECOND_HALF
    return Period.FULL_TIME


def _detect_side(selection_norm: str) -> Side | None:
    over_patterns = (
        "mais de" in selection_norm
        or selection_norm.startswith("over")
        or " over " in f" {selection_norm} "
    )
    if over_patterns:
        return Side.OVER
    under_patterns = (
        "menos de" in selection_norm
        or selection_norm.startswith("under")
        or " under " in f" {selection_norm} "
    )
    if under_patterns:
        return Side.UNDER
    return None


def _detect_metric(market_norm: str) -> Metric | None:
    shot_on_target_patterns = (
        "chutes no gol" in market_norm
        or "chute no gol" in market_norm
        or "chutes a gol" in market_norm
        or "chute a gol" in market_norm
        or "shots on target" in market_norm
        or re.search(r"\bsot\b", market_norm)
    )
    if shot_on_target_patterns:
        return Metric.SHOTS_ON_TARGET
    card_patterns = (
        "cartoes" in market_norm
        or "cartao" in market_norm
        or "cards" in market_norm
        or "booking" in market_norm
    )
    if card_patterns:
        return Metric.CARDS
    if "escanteio" in market_norm or "corner" in market_norm:
        return Metric.CORNERS
    if (
        "no gol" not in market_norm
        and "a gol" not in market_norm
        and "on target" not in market_norm
        and (
            "finaliza" in market_norm
            or "remate" in market_norm
            or "chute" in market_norm
            or "shots" in market_norm
            or "shot" in market_norm
        )
    ):
        return Metric.SHOTS
    if "falta" in market_norm:
        return Metric.FOULS
    if "impedimento" in market_norm:
        return Metric.OFFSIDES
    if "desarme" in market_norm:
        return Metric.TACKLES
    # Use "laterais" (plural, as both houses write it), NOT bare "later": "later"
    # is a substring of team names like "Inglaterra" (ing-LATER-ra), which
    # mis-tagged every England market as throw-ins and produced phantom arbs.
    if "laterais" in market_norm or "arremesso" in market_norm:
        return Metric.THROW_INS
    if "tiro de meta" in market_norm or "tiros de meta" in market_norm:
        return Metric.GOAL_KICKS
    if "goal kick" in market_norm or "goal kicks" in market_norm:
        return Metric.GOAL_KICKS
    return None


def _resolve_subject(
    market_norm: str, metric: Metric, home_team: str, away_team: str
) -> tuple[MarketFamily, str | None] | None:
    market_tokens = set(market_norm.split())
    matched: list[str] = []
    for team in (home_team, away_team):
        team_tokens = canonical_team_name(team).split()
        if team_tokens and all(token in market_tokens for token in team_tokens):
            matched.append(team)
    if len(matched) == 1:
        return MarketFamily.TEAM_TOTAL, matched[0]
    if len(matched) == 2:
        return None  # both teams present -> ambiguous
    # No known team matched. Distinguish a clean match-total name from a name
    # that references an unknown team (must be rejected, never match_total).
    residual = (
        market_tokens
        - _METRIC_PHRASE_TOKENS.get(metric, frozenset())
        - _CONNECTIVE_TOKENS
        - _PERIOD_TOKENS
    )
    if residual:
        return None  # unknown subject -> reject
    return MarketFamily.MATCH_TOTAL, None


def _reject(reason: RejectReason, evidence: dict[str, object]) -> DeepMarketNormalizationResult:
    return DeepMarketNormalizationResult(
        accepted=False,
        odd=None,
        reject_reason=reason,
        confidence=Confidence.REJECT,
        evidence=evidence,
    )


def normalize_deep_market(
    *,
    bookmaker: str,
    raw_event_id: str,
    raw_market_id: str,
    raw_selection_id: str,
    event_name: str,
    home_team: str,
    away_team: str,
    start_time: datetime,
    raw_market_name: str,
    raw_selection_name: str,
    line: Decimal | None,
    raw_line_value: str | None,
    line_source: LineSource,
    odd: Decimal,
    is_live: bool,
    is_available: bool | None = None,
    competition_name: str | None = None,
    competition_id: str | None = None,
    country: str | None = None,
    sport: str | None = "soccer",
    source_event_url: str | None = None,
) -> DeepMarketNormalizationResult:
    market_norm = _norm(raw_market_name)
    selection_norm = _norm(raw_selection_name)
    evidence: dict[str, object] = {
        "market_norm": market_norm,
        "selection_norm": selection_norm,
        "line": str(line) if line is not None else None,
        "raw_line_value": raw_line_value,
        "line_source": line_source.value,
        "competition_name": competition_name,
        "competition_id": competition_id,
        "country": country,
        "sport": sport,
        "source_event_url": source_event_url,
    }

    if is_live:
        return _reject(RejectReason.LIVE, evidence)
    if is_available is False:
        return _reject(RejectReason.SUSPENDED, evidence)
    if _is_noise(market_norm, raw_market_name):
        return _reject(RejectReason.NOISE_MARKET, evidence)

    period = _detect_period(market_norm)
    evidence["period"] = period.value if period is not None else None
    if period is None or period is Period.UNKNOWN:
        return _reject(RejectReason.AMBIGUOUS_PERIOD, evidence)

    side = _detect_side(selection_norm)
    evidence["side"] = side.value if side is not None else None
    if side is None:
        return _reject(RejectReason.NO_SIDE, evidence)

    metric = _detect_metric(market_norm)
    evidence["metric"] = metric.value if metric is not None else None
    if metric is None:
        return _reject(RejectReason.NO_METRIC, evidence)

    resolved = _resolve_subject(market_norm, metric, home_team, away_team)
    if resolved is None:
        return _reject(RejectReason.SUBJECT_UNRESOLVED, evidence)
    market_family, subject = resolved
    evidence["market_family"] = market_family.value
    evidence["subject"] = subject

    if line is None:
        return _reject(RejectReason.NO_LINE, evidence)
    if not odd.is_finite() or odd <= Decimal("1") or odd > MAX_ODD:
        return _reject(RejectReason.ODD_OUT_OF_BOUNDS, evidence)

    deep_odd = DeepMarketOdd(
        bookmaker=bookmaker,
        raw_event_id=raw_event_id,
        raw_market_id=raw_market_id,
        raw_selection_id=raw_selection_id,
        event_name=event_name,
        home_team=home_team,
        away_team=away_team,
        start_time=start_time,
        period=period,
        market_family=market_family,
        metric=metric,
        subject=subject,
        side=side,
        odd=odd,
        raw_market_name=raw_market_name,
        raw_selection_name=raw_selection_name,
        is_live=is_live,
        is_available=is_available,
        line=line,
        raw_line_value=raw_line_value,
        line_source=line_source,
        competition_name=competition_name,
        competition_id=competition_id,
        country=country,
        sport=sport,
        source_event_url=source_event_url,
    )
    return DeepMarketNormalizationResult(
        accepted=True,
        odd=deep_odd,
        reject_reason=None,
        confidence=Confidence.HIGH,
        evidence=evidence,
    )


def _event_match_id(odd: DeepMarketOdd) -> str:
    match = Match(
        match_id="deep",
        sport=odd.sport or "soccer",
        home_team=odd.home_team,
        away_team=odd.away_team,
        starts_at=odd.start_time,
    )
    return canonical_match_id(match)


def build_deep_market_key(
    odd: DeepMarketOdd,
) -> tuple[str, str, str, str, str | None, str]:
    subject_canonical = canonical_team_name(odd.subject) if odd.subject else None
    return (
        _event_match_id(odd),
        odd.period.value,
        odd.market_family.value,
        odd.metric.value,
        subject_canonical,
        format(odd.line.normalize(), "f"),
    )


class DeepArbOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: tuple[str, str, str, str, str | None, str]
    over_leg: DeepMarketOdd
    under_leg: DeepMarketOdd
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


def detect_deep_market_arbs(
    odds: Sequence[DeepMarketOdd],
    *,
    min_profit_pct: Decimal = Decimal("0"),
) -> list[DeepArbOpportunity]:
    grouped: dict[tuple[str, str, str, str, str | None, str], list[DeepMarketOdd]] = defaultdict(
        list
    )
    for odd in odds:
        grouped[build_deep_market_key(odd)].append(odd)

    opportunities: list[DeepArbOpportunity] = []
    for key, group in grouped.items():
        overs = [o for o in group if o.side is Side.OVER]
        unders = [o for o in group if o.side is Side.UNDER]
        if not overs or not unders:
            continue
        # Pick the cross-house (over, under) pair with the lowest implied sum.
        # The unconstrained optimum is (max over, max under); when those share a
        # bookmaker, the true best valid pair may use a second-best leg, so scan
        # all different-house pairs (n is tiny: a few bookmakers per key).
        best_pair: tuple[DeepMarketOdd, DeepMarketOdd] | None = None
        best_implied: Decimal | None = None
        for over in overs:
            for under in unders:
                if over.bookmaker == under.bookmaker:
                    continue
                implied = (Decimal("1") / over.odd) + (Decimal("1") / under.odd)
                if best_implied is None or implied < best_implied:
                    best_implied = implied
                    best_pair = (over, under)
        if best_pair is None or best_implied is None:
            continue
        best_over, best_under = best_pair
        implied = best_implied
        if implied >= Decimal("1"):
            continue
        profit_pct = ((Decimal("1") / implied) - Decimal("1")) * Decimal("100")
        if profit_pct < min_profit_pct:
            continue
        opportunities.append(
            DeepArbOpportunity(
                key=key,
                over_leg=best_over,
                under_leg=best_under,
                implied_probability_sum=implied,
                profit_pct=profit_pct,
                detected_at=datetime.now(UTC),
            )
        )
    return sorted(opportunities, key=lambda opp: opp.profit_pct, reverse=True)
