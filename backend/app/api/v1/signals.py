from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.limiter import limiter
from app.deps import get_signals_service, get_user_preferences
from app.models.signal import SignalsResponse
from app.models.user_preferences import UserPreferences
from app.services.signals_service import SignalsService

router = APIRouter(tags=["signals"])


@router.get("/signals", response_model=SignalsResponse)
@limiter.limit("60/minute")
def signals(
    request: Request,
    min_arb: str | None = Query(default=None),
    market: str | None = Query(default=None),
    bankroll: str | None = Query(default=None),
    competition: str | None = Query(default=None),
    preferences: UserPreferences = Depends(get_user_preferences),
    service: SignalsService = Depends(get_signals_service),
) -> SignalsResponse:
    return SignalsResponse(
        **service.get_signals(
            # Preferencia do usuario e o default; a query string (filtro da
            # sessao atual no dashboard) sempre tem prioridade quando enviada.
            min_arb=min_arb if min_arb is not None else str(preferences.min_profit_pct),
            market=market,
            bankroll=bankroll if bankroll is not None else str(preferences.default_bankroll),
            competition=competition,
            excluded_bookmakers=preferences.excluded_bookmakers,
        )
    )


@router.get("/markets")
@limiter.limit("60/minute")
def markets(
    request: Request,
    service: SignalsService = Depends(get_signals_service),
) -> list[dict[str, str]]:
    return service.market_options()


@router.get("/bookmakers")
@limiter.limit("60/minute")
def bookmakers(
    request: Request,
) -> list[dict[str, str]]:
    return [
        {"id": "demo_a", "label": "Fonte A (fictícia)"},
        {"id": "demo_b", "label": "Fonte B (fictícia)"},
    ]
