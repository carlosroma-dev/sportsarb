from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MarketKey = Literal["1x2", "over_under_2_5", "both_teams_score", "double_chance"]


class Match(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    match_id: str = Field(min_length=1)
    sport: str = Field(default="soccer", min_length=1)
    home_team: str = Field(min_length=1)
    away_team: str = Field(min_length=1)
    starts_at: datetime
    league: str | None = None
    country: str | None = None
    raw_event_id: str | None = None

    @field_validator("starts_at")
    @classmethod
    def _starts_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "starts_at must be timezone-aware"
            raise ValueError(msg)
        return value


class Market(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    market_id: str = Field(min_length=1)
    match_id: str = Field(min_length=1)
    market_key: MarketKey
    label: str = Field(min_length=1)
    line: Decimal | None = None
    raw_market_id: str | None = None


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    selection_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    outcome_key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    raw_selection_id: str | None = None


class Odd(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    match_id: str = Field(min_length=1)
    market_key: MarketKey
    outcome_key: str = Field(min_length=1)
    price: Decimal = Field(allow_inf_nan=True)
    bookmaker: str = Field(min_length=1)
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_id: str | None = None
    market_id: str | None = None
    selection_id: str | None = None
    raw_label: str | None = None

    @field_validator("captured_at")
    @classmethod
    def _captured_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "captured_at must be timezone-aware"
            raise ValueError(msg)
        return value


class ArbitrageOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    match_id: str = Field(min_length=1)
    market_key: MarketKey
    best_odds: dict[str, Odd] = Field(min_length=2)
    implied_probability_sum: Decimal
    profit_pct: Decimal
    stakes: dict[str, Decimal] = Field(default_factory=dict)
    detected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("detected_at")
    @classmethod
    def _detected_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "detected_at must be timezone-aware"
            raise ValueError(msg)
        return value
