from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import health, local_data, signals

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(signals.router)
api_router.include_router(local_data.router)
