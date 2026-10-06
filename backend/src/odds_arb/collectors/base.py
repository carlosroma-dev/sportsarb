from __future__ import annotations

import asyncio
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field

from odds_arb.core.models import Match, Odd

DEFAULT_RETRY_ATTEMPTS = 2
DEFAULT_RETRY_BACKOFF_SECONDS = 0.5

logger = structlog.get_logger(__name__)


class CollectorError(Exception):
    """Raised when a bookmaker collector cannot fetch or parse odds."""


class RawMarket(BaseModel):
    """Canonical market container.

    Everything above this layer is bookmaker-specific scraping/parsing. Everything below
    receives this stable shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    market_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    selections: list[Odd]


class RawEvent(BaseModel):
    """Canonical event schema used by storage, arbitrage, reporting, and dashboard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str = Field(min_length=1)
    bookmaker: str = Field(min_length=1)
    match: Match
    markets: list[RawMarket]

    def odds(self) -> list[Odd]:
        return [selection for market in self.markets for selection in market.selections]


CanonicalEvent = RawEvent
CanonicalSelection = Odd
CanonicalOdd = Odd


class BookmakerAdapter(Protocol):
    """Adapter contract for one bookmaker.

    `fetch` returns the raw bytes from the bookmaker/API. `parse` translates those bytes
    into canonical events. `normalize` exposes canonical selections/odds for contract
    tests and future pipelines.
    """

    name: str

    async def fetch(self) -> bytes:
        """Fetch raw bookmaker payload bytes."""

    def parse(
        self,
        raw: bytes,
        *,
        sport: str = "soccer",
        now: datetime | None = None,
    ) -> list[RawEvent]:
        """Parse raw bytes into canonical events."""

    def normalize(self, event: RawEvent) -> list[Odd]:
        """Return canonical odds/selections for one canonical event."""


class Collector(ABC):
    name: str
    last_error: str | None = None
    is_complete_snapshot: bool = True

    @abstractmethod
    async def fetch(self, sport: str = "soccer") -> Sequence[RawEvent]:
        """Return bookmaker events with normalized markets and odds."""


class AdapterCollector(Collector):
    """Compatibility wrapper so the scanner can run registry adapters."""

    def __init__(self, adapter: BookmakerAdapter) -> None:
        self.adapter = adapter
        self.name = adapter.name
        self.last_error = None
        self.is_complete_snapshot = bool(getattr(adapter, "is_complete_snapshot", True))

    def __getattr__(self, name: str) -> object:
        return getattr(self.adapter, name)

    async def fetch(self, sport: str = "soccer") -> list[RawEvent]:
        started_at = datetime.now(UTC)
        try:
            raw = await self.adapter.fetch()
            events = self.adapter.parse(raw, sport=sport, now=started_at)
            self.last_error = None
            logger.info(
                "adapter.fetch.ok",
                adapter=self.name,
                events=len(events),
                latency_ms=_latency_ms(started_at),
            )
            return events
        except Exception as exc:  # Adapter isolation: one bookmaker must not stop a scan.
            self.last_error = str(exc)
            logger.warning(
                "adapter.fetch.failed",
                adapter=self.name,
                latency_ms=_latency_ms(started_at),
                error=str(exc),
            )
            return []


async def fetch_bytes_with_retries(
    operation: Callable[[], Awaitable[bytes]],
    *,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await operation()
        except Exception as exc:
            last_error = exc
            if attempt == max(1, attempts) - 1:
                break
            await asyncio.sleep(backoff_seconds * (2**attempt))
    if last_error is None:
        msg = "fetch operation did not run"
        raise CollectorError(msg)
    raise last_error


def fetch_bytes_with_retries_sync(
    operation: Callable[[], bytes],
    *,
    attempts: int = DEFAULT_RETRY_ATTEMPTS,
    backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return operation()
        except Exception as exc:
            last_error = exc
            if attempt == max(1, attempts) - 1:
                break
            time.sleep(backoff_seconds * (2**attempt))
    if last_error is None:
        msg = "fetch operation did not run"
        raise CollectorError(msg)
    raise last_error


def response_bytes(response: object) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")

    json_method = getattr(response, "json", None)
    if callable(json_method):
        return json_payload_bytes(json_method())

    msg = "response does not expose raw content or json()"
    raise CollectorError(msg)


def json_payload_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def json_from_bytes(raw: bytes) -> Any:
    return json.loads(raw)


def _latency_ms(started_at: datetime) -> int:
    return int((datetime.now(UTC) - started_at).total_seconds() * 1000)
