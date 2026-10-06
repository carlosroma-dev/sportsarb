import pytest

from odds_arb.collectors.deep.competitions import (
    COMPETITIONS,
    DeepCompetition,
    resolve_competition,
)


def test_copa_do_mundo_is_registered():
    comp = resolve_competition("copa-do-mundo")
    assert isinstance(comp, DeepCompetition)
    assert comp.alias == "copa-do-mundo"
    assert "betano.bet.br" in comp.betano_url
    assert "/odds/" not in comp.betano_url  # it's a listing page, not an event


def test_serie_b_is_registered():
    comp = resolve_competition("serie-b")
    assert isinstance(comp, DeepCompetition)
    assert comp.alias == "serie-b"
    assert comp.label == "Brasileirao Serie B"
    assert comp.betano_url == (
        "https://www.betano.bet.br/sport/futebol/brasil/brasileirao-serie-b/10017/"
    )


def test_unknown_alias_raises_with_known_listed():
    with pytest.raises(KeyError) as exc:
        resolve_competition("nao-existe")
    assert "copa-do-mundo" in str(exc.value)
    assert "serie-b" in str(exc.value)


def test_registry_keys_match_alias_field():
    for alias, comp in COMPETITIONS.items():
        assert alias == comp.alias
