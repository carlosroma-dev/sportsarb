from __future__ import annotations

from pydantic import BaseModel


class SignalDTO(BaseModel):
    signal_id: str
    match: str
    metric: str
    metric_label: str
    scope_label: str
    subject: str | None
    period_label: str
    line: str
    kickoff: str
    profit_pct: str
    over_bookmaker: str
    over_odd: str
    over_url: str | None
    over_stake: str
    under_bookmaker: str
    under_odd: str
    under_url: str | None
    under_stake: str
    settlement_warning: bool
    highlight: bool


class SignalsResponse(BaseModel):
    updated_at: str | None
    competition: str | None
    collected_by_house: dict[str, int]
    signals: list[SignalDTO]
