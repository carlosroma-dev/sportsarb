from odds_arb.collectors.deep.kto_live import (
    KTO_ALL_MATCHES_PATH,
    KTO_EVENT_DETAIL_PATH,
    build_detail_url,
    build_list_url,
    discover_kto_events,
    kto_team_set,
    pair_kto_to_betano_universe,
)


def test_discover_kto_events_filters_virtual_noise_and_extracts_team_set() -> None:
    payload = {
        "events": [
            {
                "event": {
                    "id": 1,
                    "name": "Spain (LaikingDast) - Países Baixos (Uncle)",
                    "homeName": "Spain (LaikingDast)",
                    "awayName": "Países Baixos (Uncle)",
                    "group": "eSports Battle World Cup (2x4 min)",
                }
            },
            {
                "event": {
                    "id": 2,
                    "name": "Inglaterra - Congo",
                    "homeName": "Inglaterra",
                    "awayName": "Congo",
                    "group": "Copa do Mundo 2026",
                }
            },
        ]
    }

    events = discover_kto_events(payload)

    assert [event.event_id for event in events] == ["2"]
    assert kto_team_set(events[0]) == frozenset({"inglaterra", "congo"})


def test_pair_kto_to_betano_universe_returns_matching_event_ids() -> None:
    events = discover_kto_events(
        {
            "events": [
                {
                    "event": {
                        "id": 2,
                        "name": "Inglaterra - Congo",
                        "homeName": "Inglaterra",
                        "awayName": "Congo",
                        "group": "Copa do Mundo 2026",
                    }
                },
                {
                    "event": {
                        "id": 3,
                        "name": "França - Noruega",
                        "homeName": "França",
                        "awayName": "Noruega",
                        "group": "Copa do Mundo 2026",
                    }
                },
            ]
        }
    )

    assert pair_kto_to_betano_universe(events, {frozenset({"congo", "inglaterra"})}) == ["2"]


def test_kto_urls_use_all_matches_and_event_detail_paths() -> None:
    assert KTO_ALL_MATCHES_PATH in build_list_url()
    assert KTO_EVENT_DETAIL_PATH.format(event_id="123") in build_detail_url("123")
    assert "client_id=200" in build_list_url()
    assert "useCombined=true" in build_detail_url("123")


def test_fetch_kto_detail_retries_transient_getter_failure() -> None:
    from httpx import RemoteProtocolError

    from odds_arb.collectors.deep.kto_live import fetch_kto_detail

    calls = {"count": 0}

    def flaky_getter(_url: str) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RemoteProtocolError("connection terminated")
        return {"events": [{"id": 123}], "betOffers": []}

    assert fetch_kto_detail("123", json_getter=flaky_getter) == {
        "events": [{"id": 123}],
        "betOffers": [],
    }
    assert calls["count"] == 2
