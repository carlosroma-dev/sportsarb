from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from odds_arb.core import dedup
from odds_arb.core.dedup import (
    canonical_market_name,
    canonical_match_id,
    canonical_team_name,
    cluster_match_ids,
    init_team_aliases,
    is_same_match,
    load_approved_aliases,
    set_approved_aliases,
)


@pytest.fixture(autouse=True)
def _reset_alias_cache() -> Iterator[None]:
    """Garante que o cache global de aliases nao vaza entre testes."""
    set_approved_aliases({})
    try:
        yield
    finally:
        set_approved_aliases({})


def _make_team_aliases_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE team_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                raw_name TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL DEFAULT 'ai',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                approved INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                UNIQUE(raw_name)
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO team_aliases (raw_name, canonical_name, confidence, approved)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("MAS Maghrib A.Fes", "maghrib fes", 0.95, 1),
                ("  Some Pending Club  ", "pending", 0.8, 0),
            ],
        )
        connection.commit()
    finally:
        connection.close()


def test_load_approved_aliases_returns_only_approved_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "aliases.db"
    _make_team_aliases_db(db_path)

    aliases = load_approved_aliases(db_path)

    assert aliases == {"mas maghrib a.fes": "maghrib fes"}


def test_load_approved_aliases_is_graceful_when_db_missing(tmp_path: Path) -> None:
    assert load_approved_aliases(tmp_path / "does-not-exist.db") == {}


def test_load_approved_aliases_is_graceful_without_table(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()

    assert load_approved_aliases(db_path) == {}


def test_canonical_team_name_applies_approved_alias() -> None:
    set_approved_aliases({"mas maghrib a.fes": "maghrib fes"})

    assert canonical_team_name("MAS Maghrib A.Fes") == "maghrib fes"


def test_canonical_team_name_alias_lookup_is_case_and_space_insensitive() -> None:
    set_approved_aliases({"gks gornik leczna": "gornik leczna"})

    assert canonical_team_name("  GKS Gornik Leczna  ") == "gornik leczna"


def test_canonical_team_name_without_aliases_uses_normalization_rules() -> None:
    assert canonical_team_name("FC Barcelona") == "barcelona"


def test_init_team_aliases_loads_cache_from_db(tmp_path: Path) -> None:
    db_path = tmp_path / "aliases.db"
    _make_team_aliases_db(db_path)

    loaded = init_team_aliases(db_path)

    assert loaded == {"mas maghrib a.fes": "maghrib fes"}
    assert canonical_team_name("MAS Maghrib A.Fes") == "maghrib fes"
    assert dedup._APPROVED_ALIASES == {"mas maghrib a.fes": "maghrib fes"}


def test_canonical_team_name_collapses_flamengo_variants() -> None:
    names = ["Flamengo", "Flamengo RJ", "CR Flamengo"]

    canonical_names = {canonical_team_name(name) for name in names}

    assert len(canonical_names) == 1


def test_canonical_team_name_removes_international_club_prefixes() -> None:
    assert canonical_team_name("GKS Gornik Leczna") == canonical_team_name("Gornik Leczna")
    assert canonical_team_name("CD Palestino") == canonical_team_name("Palestino")
    assert canonical_team_name("FC Barcelona") == "barcelona"


def test_canonical_team_name_removes_deportes_only_as_a_prefix() -> None:
    assert canonical_team_name("Deportes Magallanes") == canonical_team_name("Magallanes")
    assert canonical_team_name("Universidad de Deportes") == "universidad deportes"


def test_canonical_team_name_removes_isolated_brazilian_state_suffixes() -> None:
    assert canonical_team_name("CRB AL") == canonical_team_name("CRB")
    assert canonical_team_name("Fortaleza CE") == canonical_team_name("Fortaleza")
    assert canonical_team_name("Goiás") == "goias"


def test_canonical_team_name_unifies_gender_markers() -> None:
    variants = [
        "Lanus (F)",
        "Lanus (W)",
        "Lanus [W]",
        "Lanus [F]",
        "Lanus Feminino",
        "Lanus Women",
        "Lanus Femenino",
        "Lanus Female",
    ]

    assert {canonical_team_name(name) for name in variants} == {"lanus fem"}


def test_canonical_team_name_unifies_reserve_markers() -> None:
    variants = [
        "Valentine (R)",
        "Valentine [R]",
        "Valentine Reserves",
        "Valentine Reserve",
        "Valentine FC II",
        "Valentine Sub-23",
        "Valentine Sub23",
        "Valentine Sub-21",
        "Valentine Sub21",
        "Valentine Youth",
        "Valentine Juvenil",
        "Valentine B",
    ]

    assert {canonical_team_name(name) for name in variants} == {"valentine res"}


def test_canonical_team_name_expands_common_abbreviations() -> None:
    assert canonical_team_name("República Democrática do Congo") == canonical_team_name(
        "Rep. Democrática do Congo"
    )
    assert canonical_team_name("St. Gallen") == "saint gallen"
    assert canonical_team_name("Rep Dem Congo") == "republica democratica congo"


def test_canonical_team_name_preserves_name_bearing_tokens() -> None:
    assert canonical_team_name("Atletico Madrid") == "atletico madrid"
    assert canonical_team_name("Deportivo Cali") == "deportivo cali"
    assert canonical_team_name("Athletic Bilbao") == "athletic bilbao"


def test_canonical_team_name_keeps_mens_and_womens_teams_distinct(make_match) -> None:
    assert canonical_team_name("Lanus") != canonical_team_name("Lanus Feminino")
    assert canonical_team_name("Lanus Feminino").endswith(" fem")

    mens_match = make_match("Lanus", "San Luis", match_id="mens")
    womens_match = make_match("Lanus (F)", "San Luis [W]", match_id="womens")

    assert not is_same_match(mens_match, womens_match)


def test_canonical_team_name_is_idempotent() -> None:
    names = [
        "GKS Gornik Leczna",
        "Deportes Magallanes",
        "CRB AL",
        "Lanus [W]",
        "Valentine FC II",
        "Rep. Democrática do Congo",
    ]

    for name in names:
        canonical = canonical_team_name(name)
        assert canonical_team_name(canonical) == canonical


def test_same_teams_with_five_minute_kickoff_difference_are_same_match(make_match) -> None:
    left = make_match("Flamengo", "Vasco")
    right = make_match(
        "CR Flamengo",
        "Vasco da Gama",
        left.starts_at + timedelta(minutes=5),
        match_id="other-source-id",
    )

    assert is_same_match(left, right, kickoff_tolerance_minutes=5)


def test_same_teams_with_six_minute_kickoff_difference_are_different_matches(make_match) -> None:
    left = make_match("Flamengo", "Vasco")
    right = make_match(
        "CR Flamengo",
        "Vasco da Gama",
        left.starts_at + timedelta(minutes=6),
        match_id="other-source-id",
    )

    assert not is_same_match(left, right, kickoff_tolerance_minutes=5)


def test_canonical_match_id_is_stable_for_aliases_inside_time_tolerance(make_match) -> None:
    left = make_match("Flamengo", "Vasco")
    right = make_match(
        "Flamengo RJ",
        "Vasco da Gama",
        left.starts_at + timedelta(minutes=5),
        match_id="bookmaker-specific-id",
    )

    assert canonical_match_id(left) == canonical_match_id(right)


def test_canonical_match_id_normalizes_kickoff_to_utc(make_match) -> None:
    left = make_match(starts_at=datetime(2026, 6, 15, 21, 5, tzinfo=UTC))
    right = make_match(
        starts_at=datetime(2026, 6, 15, 18, 5, tzinfo=timezone(timedelta(hours=-3))),
        match_id="bookmaker-specific-id",
    )

    assert canonical_match_id(left) == canonical_match_id(right)


def test_canonical_market_name_collapses_over_two_point_five_aliases() -> None:
    labels = ["Mais de 2.5", "Over 2.5", "Acima de 2.5 gols"]

    canonical_names = {canonical_market_name(label) for label in labels}

    assert len(canonical_names) == 1


def test_canonical_market_name_collapses_both_teams_score_yes_aliases() -> None:
    labels = ["Ambas Marcam - Sim", "Both Teams To Score Yes", "BTTS Sim"]

    canonical_names = {canonical_market_name(label) for label in labels}

    assert len(canonical_names) == 1


def test_canonical_market_name_handles_fallback_alias_patterns() -> None:
    assert canonical_market_name("Total gols acima de 2.5") == "over_under_2_5:over"
    assert canonical_market_name("Total gols abaixo de 2.5") == "over_under_2_5:under"
    assert canonical_market_name("Ambas equipes marcam? Sim") == "both_teams_score:yes"
    assert canonical_market_name("Ambas equipes marcam? Nao") == "both_teams_score:no"
    assert canonical_market_name("Mercado Customizado") == "mercado customizado"


def test_cluster_match_ids_unifies_variants_across_ten_minute_bucket_boundary(make_match) -> None:
    # 21:09 e 21:11: 2 min de diferenca, mas em buckets de 10 min diferentes.
    left = make_match(
        "Flamengo",
        "Vasco",
        datetime(2026, 6, 15, 21, 9, tzinfo=UTC),
        match_id="kto-id",
    )
    right = make_match(
        "Flamengo RJ",
        "Vasco da Gama",
        datetime(2026, 6, 15, 21, 11, tzinfo=UTC),
        match_id="superbet-id",
    )

    mapping = cluster_match_ids([left, right])

    assert mapping["kto-id"] == mapping["superbet-id"]


def test_cluster_match_ids_keeps_different_teams_apart(make_match) -> None:
    left = make_match("Flamengo", "Vasco", match_id="a")
    right = make_match("Palmeiras", "Corinthians", match_id="b")

    mapping = cluster_match_ids([left, right])

    assert mapping["a"] != mapping["b"]


def test_cluster_match_ids_keeps_same_teams_beyond_tolerance_apart(make_match) -> None:
    left = make_match("Flamengo", "Vasco", datetime(2026, 6, 15, 21, 0, tzinfo=UTC), match_id="a")
    right = make_match("Flamengo", "Vasco", datetime(2026, 6, 15, 21, 6, tzinfo=UTC), match_id="b")

    mapping = cluster_match_ids([left, right], kickoff_tolerance_minutes=5)

    assert mapping["a"] != mapping["b"]


def test_different_sports_are_not_same_match(make_match) -> None:
    left = make_match("Flamengo", "Vasco")
    right = make_match("Flamengo", "Vasco").model_copy(update={"sport": "basketball"})

    assert not is_same_match(left, right)
