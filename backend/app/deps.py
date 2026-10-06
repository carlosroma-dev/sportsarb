"""FastAPI dependencies for the local portfolio application."""

from __future__ import annotations

from fastapi import Depends, Request

from app.core.config import Settings
from app.models.user_preferences import UserPreferences
from app.repositories.preferences_repository import LocalDatabase, PreferencesRepository
from app.repositories.signal_repository import SignalRepository
from app.services.signals_service import SignalsService


def current_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_signal_repository(request: Request) -> SignalRepository:
    return request.app.state.signal_repository  # type: ignore[no-any-return]


def get_signals_service(
    repo: SignalRepository = Depends(get_signal_repository),
    settings: Settings = Depends(current_settings),
) -> SignalsService:
    return SignalsService(repo, default_min_arb=settings.deep_min_arb_pct)


def get_preferences_repository(request: Request) -> PreferencesRepository:
    return request.app.state.preferences_repository  # type: ignore[no-any-return]


def get_local_database(request: Request) -> LocalDatabase:
    return request.app.state.local_database  # type: ignore[no-any-return]


def get_user_preferences(
    repo: PreferencesRepository = Depends(get_preferences_repository),
) -> UserPreferences:
    return repo.get("local-demo")
