from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse

from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.limiter import limiter
from app.demo import seed_demo_signals
from app.repositories.preferences_repository import LocalDatabase, PreferencesRepository
from app.repositories.signal_repository import (
    InMemorySignalRepository,
    SignalRepository,
)
from app.scanner import (
    InMemorySignalStore,
    LiveDeepConfig,
    build_combined_loop_runner,
    build_loop_runner,
    fetch_betano_events,
    fetch_betano_tennis_events,
    fetch_estrelabet_detail,
    fetch_estrelabet_list,
    fetch_kto_detail,
    fetch_kto_events_deep,
    fetch_novibet_event_detail,
    fetch_novibet_list,
    fetch_sportingbet_detail,
    fetch_sportingbet_list,
    fetch_superbet_detail,
    fetch_superbet_list,
    fetch_superbet_tennis_detail,
    fetch_superbet_tennis_list,
    resolve_competition,
)


def _start_deep_loop(app: FastAPI, store: InMemorySignalStore, settings: Settings) -> None:
    """Dispara o loop deep em background, reusando o mesmo wiring do `deep-serve`."""
    configs = [
        LiveDeepConfig(
            competition=resolve_competition(alias),
            min_profit_pct=Decimal("0"),
            bankroll=Decimal(settings.bankroll_default),
            interval_seconds=settings.deep_scan_interval_seconds,
            audit_path=Path(f"deep_markets_audit_{alias}.jsonl"),
        )
        for alias in settings.deep_competition_aliases
    ]
    fetchers: dict[str, Any] = {
        "betano_fetcher": fetch_betano_events,
        "superbet_list_fetcher": fetch_superbet_list,
        "superbet_detail_fetcher": fetch_superbet_detail,
        "sportingbet_list_fetcher": fetch_sportingbet_list,
        "sportingbet_detail_fetcher": fetch_sportingbet_detail,
        "kto_list_fetcher": fetch_kto_events_deep,
        "kto_detail_fetcher": fetch_kto_detail,
        "estrelabet_list_fetcher": fetch_estrelabet_list,
        "estrelabet_detail_fetcher": fetch_estrelabet_detail,
        "novibet_list_fetcher": fetch_novibet_list,
        "novibet_detail_fetcher": fetch_novibet_event_detail,
    }
    if settings.enable_tennis:
        fetchers.update(
            tennis_betano_fetcher=fetch_betano_tennis_events,
            tennis_superbet_list_fetcher=fetch_superbet_tennis_list,
            tennis_superbet_detail_fetcher=fetch_superbet_tennis_detail,
        )
    if len(configs) == 1:
        runner = build_loop_runner(store, configs[0], **fetchers)
    else:
        runner = build_combined_loop_runner(store, configs, **fetchers)
    tasks: set[asyncio.Task[None]] = getattr(app.state, "deep_tasks", set())
    task: asyncio.Task[None] = asyncio.create_task(cast(Coroutine[Any, Any, None], runner()))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    app.state.deep_tasks = tasks


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: StarletteRequest,
        call_next: RequestResponseEndpoint,
    ) -> StarletteResponse:
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


def create_app(
    settings: Settings | None = None,
    *,
    signal_repository: SignalRepository | None = None,
    preferences_repository: PreferencesRepository | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Mestre das Odds API")

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, cast(Any, _rate_limit_exceeded_handler))
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(_SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    store: InMemorySignalStore | None = None
    if signal_repository is None:
        store = InMemorySignalStore()
        if settings.demo_mode:
            seed_demo_signals(store)
        signal_repository = InMemorySignalRepository(store)

    local_database = LocalDatabase(settings.local_db_path)
    if preferences_repository is None:
        preferences_repository = local_database

    app.state.settings = settings
    app.state.signal_repository = signal_repository
    app.state.preferences_repository = preferences_repository
    app.state.local_database = local_database

    app.include_router(api_router, prefix="/api/v1")

    @app.on_event("startup")
    async def _startup() -> None:
        if settings.enable_deep_markets and store is not None:
            _start_deep_loop(app, store, settings)

    return app


app = create_app()
