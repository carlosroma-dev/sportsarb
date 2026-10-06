"""Preferences and operation history backed by the local SQLite database."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_local_database
from app.models.user_preferences import UserPreferences
from app.repositories.preferences_repository import LocalDatabase

router = APIRouter(tags=["local data"])


class PreferencesPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    excluded_bookmakers: list[str] = Field(default_factory=list, alias="excludedBookmakers")
    min_arb: Decimal = Field(ge=0, alias="minArb")
    bankroll: Decimal = Field(gt=0)


class OperationCreate(BaseModel):
    user_id: str = "local-demo"
    event_name: str = Field(min_length=1)
    market_name: str = Field(min_length=1)
    scope_label: str | None = None
    subject: str | None = None
    line: str | None = None
    kickoff: str | None = None
    bookmaker_1: str = Field(min_length=1)
    bookmaker_2: str = Field(min_length=1)
    odd_1: float = Field(gt=1)
    odd_2: float = Field(gt=1)
    stake_1: float = Field(ge=0)
    stake_2: float = Field(ge=0)
    total_stake: float = Field(ge=0)
    expected_return: float = Field(ge=0)
    profit: float
    roi: float
    status: Literal["concluida", "cancelada"] = "concluida"
    notes: str | None = None


@router.get("/preferences", response_model=PreferencesPayload)
def preferences(db: LocalDatabase = Depends(get_local_database)) -> PreferencesPayload:
    current = db.get()
    return PreferencesPayload(
        excludedBookmakers=sorted(current.excluded_bookmakers),
        minArb=current.min_profit_pct,
        bankroll=current.default_bankroll,
    )


@router.put("/preferences", response_model=PreferencesPayload)
def save_preferences(
    payload: PreferencesPayload,
    db: LocalDatabase = Depends(get_local_database),
) -> PreferencesPayload:
    db.save(
        UserPreferences(
            excluded_bookmakers=frozenset(payload.excluded_bookmakers),
            min_profit_pct=payload.min_arb,
            default_bankroll=payload.bankroll,
        )
    )
    return payload


@router.get("/operations")
def operations(db: LocalDatabase = Depends(get_local_database)) -> list[dict[str, object]]:
    return db.list_operations()


@router.post("/operations", status_code=201)
def add_operation(
    payload: OperationCreate,
    db: LocalDatabase = Depends(get_local_database),
) -> dict[str, object]:
    row: dict[str, object] = payload.model_dump()
    row["user_id"] = "local-demo"
    row["id"] = str(uuid4())
    row["created_at"] = datetime.now(UTC).isoformat()
    return db.add_operation(row)


@router.patch("/operations/{operation_id}/cancel", status_code=204)
def cancel_operation(
    operation_id: str,
    db: LocalDatabase = Depends(get_local_database),
) -> None:
    if not db.cancel_operation(operation_id):
        raise HTTPException(status_code=404, detail="Operação não encontrada")
