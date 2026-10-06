from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, timedelta
from pathlib import Path

from rapidfuzz import fuzz

from odds_arb.core.models import Match

TEAM_ALIASES = {
    "cr flamengo": "flamengo",
    "flamengo rj": "flamengo",
    "vasco da gama": "vasco",
    "cr vasco da gama": "vasco",
}
TEAM_STOP_WORDS = frozenset(
    {
        "fc",
        "sc",
        "ec",
        "ac",
        "cf",
        "cr",
        "clube",
        "club",
        "de",
        "da",
        "do",
        "rj",
    }
)
TEAM_PREFIX_STOP_WORDS = frozenset(
    {
        # "athletic" is intentionally preserved because it can be the club's actual name.
        "cd",
        "gks",
        "fk",
        "sk",
        "ik",
        "bk",
        "ks",
        "uts",
        "mas",
        "heua",
        "afc",
        "bsc",
        "tsv",
        "sv",
        "vfb",
        "vfl",
        "rb",
        "esporte",
        "esportes",
        "sport",
        "deportes",
    }
)
TEAM_PREFIX_PHRASES = (("red", "bull"),)
BRAZILIAN_STATE_SUFFIXES = frozenset(
    {
        "go",
        "se",
        "ce",
        "mg",
        "sp",
        "rs",
        "pr",
        "ba",
        "pe",
        "al",
        "rn",
        "pa",
        "am",
        "mt",
        "ms",
        "to",
        "ma",
        "pi",
        "es",
        "sc",
        "pb",
        "rr",
        "ro",
        "ap",
        "df",
        "rj",
    }
)
TEAM_TOKEN_ALIASES = {
    "rep": "republica",
    "dem": "democratica",
    "st": "saint",
}
GENDER_MARKERS = frozenset({"fem", "feminino", "women", "femenino", "female"})
RESERVE_MARKERS = frozenset({"res", "reserves", "reserve", "youth", "juvenil"})
FINAL_RESERVE_MARKERS = frozenset({"ii", "b"})
MARKET_ALIASES = {
    "1x2": "1x2",
    "resultado final": "1x2",
    "vencedor do jogo": "1x2",
    "mais de 2 5": "over_under_2_5:over",
    "over 2 5": "over_under_2_5:over",
    "acima de 2 5 gols": "over_under_2_5:over",
    "menos de 2 5": "over_under_2_5:under",
    "under 2 5": "over_under_2_5:under",
    "ambas marcam sim": "both_teams_score:yes",
    "both teams to score yes": "both_teams_score:yes",
    "btts sim": "both_teams_score:yes",
    "ambas marcam nao": "both_teams_score:no",
    "both teams to score no": "both_teams_score:no",
    "btts nao": "both_teams_score:no",
}
FUZZY_MATCH_THRESHOLD = 88.0

# Aliases curados/aprovados (tabela team_aliases). Carregado uma vez por processo via
# init_team_aliases() e consultado em memoria por canonical_team_name; nunca por chamada.
_APPROVED_ALIASES: dict[str, str] = {}


def load_approved_aliases(db_path: str | Path) -> dict[str, str]:
    """Le os aliases aprovados de ``team_aliases`` e retorna ``{raw_name: canonical_name}``.

    A carga e graceful: se o banco nao existir, estiver indisponivel ou a tabela ainda
    nao tiver sido criada, retorna um dict vazio sem propagar excecao. As chaves sao
    normalizadas (``strip().lower()``) para casar com a busca de ``canonical_team_name``.
    """
    aliases: dict[str, str] = {}
    try:
        connection = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True)
    except sqlite3.Error:
        return aliases
    try:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT raw_name, canonical_name FROM team_aliases WHERE approved = 1"
        ).fetchall()
    except sqlite3.Error:
        # Tabela ausente, schema divergente ou banco corrompido: segue sem aliases.
        return aliases
    finally:
        connection.close()
    for row in rows:
        key = str(row["raw_name"]).strip().lower()
        if key:
            aliases[key] = str(row["canonical_name"])
    return aliases


def set_approved_aliases(aliases: Mapping[str, str]) -> None:
    """Substitui o cache em memoria de aliases aprovados."""
    global _APPROVED_ALIASES
    _APPROVED_ALIASES = {key.strip().lower(): value for key, value in aliases.items()}


def init_team_aliases(db_path: str | Path) -> dict[str, str]:
    """Carrega os aliases aprovados do banco para o cache em memoria, uma vez por processo."""
    aliases = load_approved_aliases(db_path)
    set_approved_aliases(aliases)
    return aliases


def canonical_team_name(name: str) -> str:
    alias = _APPROVED_ALIASES.get(name.strip().lower())
    if alias is not None:
        return alias

    marked_name = re.sub(r"[\(\[]\s*[fw]\s*[\)\]]", " fem ", name, flags=re.IGNORECASE)
    marked_name = re.sub(r"[\(\[]\s*r\s*[\)\]]", " res ", marked_name, flags=re.IGNORECASE)
    marked_name = re.sub(
        r"\bsub[\s-]?(?:21|23)\b",
        " res ",
        marked_name,
        flags=re.IGNORECASE,
    )
    normalized = _normalize_text(marked_name)
    if normalized in TEAM_ALIASES:
        return TEAM_ALIASES[normalized]

    tokens = [TEAM_TOKEN_ALIASES.get(token, token) for token in normalized.split()]
    has_gender_marker = any(token in GENDER_MARKERS for token in tokens)
    has_reserve_marker = any(token in RESERVE_MARKERS for token in tokens)
    tokens = [
        token for token in tokens if token not in GENDER_MARKERS and token not in RESERVE_MARKERS
    ]

    while len(tokens) > 1 and tokens[-1] in BRAZILIAN_STATE_SUFFIXES:
        tokens.pop()

    if tokens and tokens[-1] in FINAL_RESERVE_MARKERS:
        tokens.pop()
        has_reserve_marker = True

    base_name = " ".join(tokens)
    canonical_base = TEAM_ALIASES.get(base_name)
    if canonical_base is None:
        tokens = [token for token in tokens if token not in TEAM_STOP_WORDS]
        while tokens:
            matching_phrase = next(
                (
                    phrase
                    for phrase in TEAM_PREFIX_PHRASES
                    if tuple(tokens[: len(phrase)]) == phrase
                ),
                None,
            )
            if matching_phrase is not None:
                del tokens[: len(matching_phrase)]
                continue
            if tokens[0] in TEAM_PREFIX_STOP_WORDS:
                tokens.pop(0)
                continue
            break
        compact = " ".join(tokens)
        canonical_base = TEAM_ALIASES.get(compact, compact)

    canonical_tokens = canonical_base.split()
    if has_gender_marker:
        canonical_tokens.append("fem")
    if has_reserve_marker:
        canonical_tokens.append("res")
    return " ".join(canonical_tokens)


def canonical_market_name(name: str) -> str:
    normalized = _normalize_text(name)
    if normalized in MARKET_ALIASES:
        return MARKET_ALIASES[normalized]
    if "2 5" in normalized and any(prefix in normalized for prefix in ("mais", "over", "acima")):
        return "over_under_2_5:over"
    if "2 5" in normalized and any(prefix in normalized for prefix in ("menos", "under", "abaixo")):
        return "over_under_2_5:under"
    if "ambas" in normalized and "sim" in normalized:
        return "both_teams_score:yes"
    if "ambas" in normalized and "nao" in normalized:
        return "both_teams_score:no"
    return normalized


def canonical_match_id(match: Match) -> str:
    starts_at = match.starts_at.astimezone(UTC)
    bucket = starts_at.replace(
        minute=starts_at.minute - (starts_at.minute % 10),
        second=0,
        microsecond=0,
    )
    return ":".join(
        (
            match.sport,
            bucket.isoformat(),
            canonical_team_name(match.home_team),
            canonical_team_name(match.away_team),
        )
    )


def is_same_match(
    left: Match,
    right: Match,
    *,
    kickoff_tolerance_minutes: int = 5,
) -> bool:
    if left.sport != right.sport:
        return False

    kickoff_delta = abs(left.starts_at - right.starts_at)
    if kickoff_delta > timedelta(minutes=kickoff_tolerance_minutes):
        return False

    left_home = canonical_team_name(left.home_team)
    left_away = canonical_team_name(left.away_team)
    right_home = canonical_team_name(right.home_team)
    right_away = canonical_team_name(right.away_team)

    same_order_score = min(
        fuzz.ratio(left_home, right_home),
        fuzz.ratio(left_away, right_away),
    )
    swapped_order_score = min(
        fuzz.ratio(left_home, right_away),
        fuzz.ratio(left_away, right_home),
    )
    return max(same_order_score, swapped_order_score) >= FUZZY_MATCH_THRESHOLD


def cluster_match_ids(
    matches: Sequence[Match],
    *,
    kickoff_tolerance_minutes: int = 5,
) -> dict[str, str]:
    """Map each match_id to a shared canonical id by fuzzy-matching across bookmakers.

    Uses ``is_same_match`` (fuzzy team names + +/- tolerance no horario), so it unifies
    the same game even when the per-bookmaker canonical_match_id strings diverge (ex.:
    variantes de nome ou jogos que caem em buckets de 10 min diferentes).
    """
    tolerance = timedelta(minutes=kickoff_tolerance_minutes)
    ordered = sorted(matches, key=lambda match: (match.sport, match.starts_at, match.match_id))

    representatives: list[Match] = []
    mapping: dict[str, str] = {}
    for match in ordered:
        if match.match_id in mapping:
            continue
        representative = _find_representative(
            match,
            representatives,
            tolerance=tolerance,
            kickoff_tolerance_minutes=kickoff_tolerance_minutes,
        )
        if representative is None:
            representatives.append(match)
            mapping[match.match_id] = match.match_id
        else:
            mapping[match.match_id] = mapping[representative.match_id]
    return mapping


def _find_representative(
    match: Match,
    representatives: Sequence[Match],
    *,
    tolerance: timedelta,
    kickoff_tolerance_minutes: int,
) -> Match | None:
    for representative in reversed(representatives):
        if representative.sport != match.sport:
            continue
        if abs(representative.starts_at - match.starts_at) > tolerance:
            continue
        if is_same_match(
            representative,
            match,
            kickoff_tolerance_minutes=kickoff_tolerance_minutes,
        ):
            return representative
    return None


def _normalize_text(value: str) -> str:
    without_accents = "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", without_accents).strip().lower()
    return re.sub(r"\s+", " ", normalized)
