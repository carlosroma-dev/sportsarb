from odds_arb.collectors.deep.altenar_live import (
    ALTENAR_WIDGET_BASE_URL,
    ESTRELABET_INTEGRATION,
    build_detail_params,
    build_events_params,
    discover_altenar_events,
    pair_altenar_to_betano_universe,
)


def test_discover_altenar_events_extracts_football_prematch_team_set() -> None:
    payload = {
        "events": [
            {
                "id": 1,
                "name": "Inglaterra vs. RD Congo",
                "sportId": 66,
                "status": 0,
                "et": 0,
                "competitorIds": [10, 20],
            },
            {
                "id": 2,
                "name": "Virtual - Match",
                "sportId": 66,
                "status": 1,
                "et": 1,
                "competitorIds": [30, 40],
            },
        ],
        "competitors": [
            {"id": 10, "name": "Inglaterra"},
            {"id": 20, "name": "RD Congo"},
            {"id": 30, "name": "Virtual A"},
            {"id": 40, "name": "Virtual B"},
        ],
    }

    events = discover_altenar_events(payload)

    assert [(event.event_id, event.home_team, event.away_team) for event in events] == [
        ("1", "Inglaterra", "RD Congo")
    ]


def test_pair_altenar_to_betano_universe_returns_matching_event_ids() -> None:
    events = discover_altenar_events(
        {
            "events": [
                {
                    "id": 1,
                    "name": "Inglaterra vs. RD Congo",
                    "sportId": 66,
                    "status": 0,
                    "et": 0,
                    "competitorIds": [10, 20],
                },
                {
                    "id": 2,
                    "name": "França vs. Noruega",
                    "sportId": 66,
                    "status": 0,
                    "et": 0,
                    "competitorIds": [30, 40],
                },
            ],
            "competitors": [
                {"id": 10, "name": "Inglaterra"},
                {"id": 20, "name": "RD Congo"},
                {"id": 30, "name": "França"},
                {"id": 40, "name": "Noruega"},
            ],
        }
    )

    assert pair_altenar_to_betano_universe(events, {frozenset({"inglaterra", "rd congo"})}) == ["1"]


def test_estrelabet_params_target_public_altenar_widget() -> None:
    events_params = build_events_params(integration=ESTRELABET_INTEGRATION, champ_ids=["3146"])
    detail_params = build_detail_params(
        integration=ESTRELABET_INTEGRATION,
        event_id="16934703",
    )

    assert ALTENAR_WIDGET_BASE_URL.endswith("/api/widget")
    assert events_params["integration"] == "estrelabet"
    assert events_params["champIds"] == "3146"
    assert events_params["sportId"] == "66"
    assert detail_params["eventId"] == "16934703"
