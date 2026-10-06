from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.core.limiter import limiter
from app.deps import get_signal_repository
from app.repositories.signal_repository import SignalRepository

router = APIRouter(tags=["health"])


@router.get("/health")
@limiter.limit("30/minute")
def health(
    request: Request,
    repo: SignalRepository = Depends(get_signal_repository),
) -> dict[str, Any]:
    snapshot = repo.latest()
    return {
        "status": "ok",
        "updated_at": snapshot.updated_at.isoformat() if snapshot else None,
        "signals": len(snapshot.opportunities) if snapshot else 0,
    }
