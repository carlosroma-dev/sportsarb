from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeepCompetition:
    alias: str
    betano_url: str
    label: str


COMPETITIONS: dict[str, DeepCompetition] = {
    "copa-do-mundo": DeepCompetition(
        alias="copa-do-mundo",
        betano_url="https://www.betano.bet.br/sport/futebol/competicoes/copa-do-mundo/189813/",
        label="Copa do Mundo",
    ),
    "serie-b": DeepCompetition(
        alias="serie-b",
        betano_url="https://www.betano.bet.br/sport/futebol/brasil/brasileirao-serie-b/10017/",
        label="Brasileirao Serie B",
    ),
}


def resolve_competition(alias: str) -> DeepCompetition:
    try:
        return COMPETITIONS[alias]
    except KeyError:
        known = ", ".join(sorted(COMPETITIONS))
        msg = f"competição desconhecida: {alias!r}. Conhecidas: {known}"
        raise KeyError(msg) from None
