from datetime import UTC, datetime

from odds_arb.collectors.deep.superbet_live import (
    SuperbetListEvent,
    build_by_date_params,
    discover_superbet_events,
    extract_superbet_event,
    fetch_superbet_detail,
    fetch_superbet_list,
    pair_to_betano_universe,
    superbet_team_set,
)
from odds_arb.core.dedup import canonical_team_name


def test_discover_extracts_id_and_match_name():
    payload = {
        "data": [
            {"eventId": 111, "matchName": "Egito · Irã"},
            {"eventId": 222, "matchName": "Brasil · Itália"},
            {"matchName": "missing id"},
        ]
    }
    events = discover_superbet_events(payload)
    assert events == [
        SuperbetListEvent(event_id="111", match_name="Egito · Irã"),
        SuperbetListEvent(event_id="222", match_name="Brasil · Itália"),
    ]


def test_team_set_is_canonical_and_order_independent():
    assert superbet_team_set("Egito · Irã") == frozenset(
        {canonical_team_name("Egito"), canonical_team_name("Irã")}
    )


def test_pairing_keeps_only_matches_in_betano_universe():
    sb = [
        SuperbetListEvent(event_id="111", match_name="Egito · Irã"),
        SuperbetListEvent(event_id="999", match_name="Time X · Time Y"),
    ]
    universe = {frozenset({canonical_team_name("Irã"), canonical_team_name("Egito")})}
    assert pair_to_betano_universe(sb, universe) == ["111"]


def test_extract_event_returns_first_data_item():
    detail = {"data": [{"eventId": 111, "odds": []}]}
    assert extract_superbet_event(detail) == {"eventId": 111, "odds": []}
    assert extract_superbet_event({"data": []}) is None


def test_build_by_date_params_shapes_request():
    start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
    end = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)
    params = build_by_date_params(start, end)
    assert params["sportId"] == "5"
    assert params["offerState"] == "prematch"
    assert params["startDate"] == "2026-06-24 12:00:00"
    assert params["endDate"] == "2026-06-25 12:00:00"


def test_fetch_list_and_detail_use_injected_getter():
    start = datetime(2026, 6, 24, 12, 0, 0, tzinfo=UTC)
    end = datetime(2026, 6, 25, 12, 0, 0, tzinfo=UTC)
    seen: list[str] = []

    def getter(url, params):
        seen.append(url)
        if "by-date" in url:
            return {"data": [{"eventId": 111, "matchName": "A · B"}]}
        return {"data": [{"eventId": 111, "odds": [{"name": "x"}]}]}

    lst = fetch_superbet_list(start, end, json_getter=getter)
    assert lst["data"][0]["eventId"] == 111
    detail = fetch_superbet_detail("111", json_getter=getter)
    assert detail == {"eventId": 111, "odds": [{"name": "x"}]}
    assert any("by-date" in u for u in seen)
    assert any(u.endswith("/111") for u in seen)
