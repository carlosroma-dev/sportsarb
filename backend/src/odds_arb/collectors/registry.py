from __future__ import annotations

from odds_arb.collectors.altenar import AltenarAdapter
from odds_arb.collectors.base import BookmakerAdapter
from odds_arb.collectors.betano import BetanoAdapter
from odds_arb.collectors.betfair import BetfairAdapter
from odds_arb.collectors.betmgm import BetmgmAdapter
from odds_arb.collectors.betnacional import BetnacionalAdapter
from odds_arb.collectors.esportesdasorte import EsportesdasorteAdapter
from odds_arb.collectors.kto import KtoAdapter
from odds_arb.collectors.novibet import NovibetAdapter
from odds_arb.collectors.pixbet import PixbetAdapter
from odds_arb.collectors.sportingbet import SportingbetAdapter
from odds_arb.collectors.superbet import SuperbetAdapter

REGISTRY: dict[str, BookmakerAdapter] = {
    adapter.name: adapter
    for adapter in (
        AltenarAdapter(name="bateubet", integration="bateu", champ_limit=None),
        BetfairAdapter(),
        BetmgmAdapter(),
        BetnacionalAdapter(),
        BetanoAdapter(),
        AltenarAdapter(name="br4bet", integration="br4bet"),
        EsportesdasorteAdapter(),
        AltenarAdapter(name="estrelabet", integration="estrelabet"),
        KtoAdapter(),
        NovibetAdapter(),
        PixbetAdapter(),
        SportingbetAdapter(),
        SuperbetAdapter(),
    )
}
