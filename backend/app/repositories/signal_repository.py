from __future__ import annotations

from typing import Protocol

from app.scanner import InMemorySignalStore, SignalSnapshot


class SignalRepository(Protocol):
    """Fonte de sinais para a API. Hoje é a memória do loop deep; no futuro pode
    ser DB ou um barramento WS, sem mudar o SignalsService."""

    def latest(self) -> SignalSnapshot | None: ...


class InMemorySignalRepository:
    def __init__(self, store: InMemorySignalStore) -> None:
        self._store = store

    def latest(self) -> SignalSnapshot | None:
        return self._store.latest()
