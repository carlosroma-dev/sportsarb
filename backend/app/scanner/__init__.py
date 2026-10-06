"""Fronteira fina e única para o scanner deep (pacote odds_arb).

Nada aqui altera o scanner: apenas reexporta os símbolos que a camada de API
consome, centralizando num único lugar o acoplamento com odds_arb.
"""

from __future__ import annotations

from odds_arb.collectors.deep.altenar_live import (
    fetch_estrelabet_detail,
    fetch_estrelabet_list,
)
from odds_arb.collectors.deep.betano_live import (
    fetch_betano_events_deep as fetch_betano_events,
)
from odds_arb.collectors.deep.betano_tennis import fetch_betano_tennis_events
from odds_arb.collectors.deep.competitions import resolve_competition
from odds_arb.collectors.deep.kto_live import fetch_kto_detail, fetch_kto_events_deep
from odds_arb.collectors.deep.novibet_live import (
    fetch_novibet_event_detail,
    fetch_novibet_list,
)
from odds_arb.collectors.deep.sportingbet_live import (
    fetch_sportingbet_detail,
    fetch_sportingbet_list,
)
from odds_arb.collectors.deep.superbet_live import (
    fetch_superbet_detail,
    fetch_superbet_list,
)
from odds_arb.collectors.deep.superbet_tennis import (
    fetch_superbet_tennis_detail,
    fetch_superbet_tennis_list,
)
from odds_arb.deep_live import LiveDeepConfig
from odds_arb.deep_web import (
    BOOKMAKER_OPTIONS,
    MARKET_OPTIONS,
    InMemorySignalStore,
    SignalSnapshot,
    build_combined_loop_runner,
    build_loop_runner,
)
from odds_arb.deep_web import _row_to_dict as row_to_dict
from odds_arb.deep_web import _rows_for_request as rows_for_request

__all__ = [
    "BOOKMAKER_OPTIONS",
    "MARKET_OPTIONS",
    "InMemorySignalStore",
    "LiveDeepConfig",
    "SignalSnapshot",
    "build_combined_loop_runner",
    "build_loop_runner",
    "fetch_betano_events",
    "fetch_betano_tennis_events",
    "fetch_estrelabet_detail",
    "fetch_estrelabet_list",
    "fetch_kto_detail",
    "fetch_kto_events_deep",
    "fetch_novibet_event_detail",
    "fetch_novibet_list",
    "fetch_sportingbet_detail",
    "fetch_sportingbet_list",
    "fetch_superbet_detail",
    "fetch_superbet_list",
    "fetch_superbet_tennis_detail",
    "fetch_superbet_tennis_list",
    "resolve_competition",
    "row_to_dict",
    "rows_for_request",
]
