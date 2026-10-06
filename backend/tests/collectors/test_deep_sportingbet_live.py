from odds_arb.collectors.deep.sportingbet_live import (
    build_detail_params,
    discover_sportingbet_events,
    pair_sportingbet_to_betano_universe,
    sportingbet_team_set,
)


def test_discover_sportingbet_events_from_fixtures_payload() -> None:
    payload = {
        "fixtures": [
            {
                "id": "2:7826108",
                "name": {"value": "Inglaterra - RD Congo"},
                "participants": [
                    {"name": {"value": "Inglaterra"}, "properties": {"type": "HomeTeam"}},
                    {"name": {"value": "RD Congo"}, "properties": {"type": "AwayTeam"}},
                ],
            },
            {"id": "", "name": {"value": "Broken"}},
        ]
    }

    events = discover_sportingbet_events(payload)

    assert len(events) == 1
    assert events[0].fixture_id == "2:7826108"
    assert events[0].match_name == "Inglaterra - RD Congo"
    assert sportingbet_team_set(events[0]) == frozenset({"inglaterra", "rd congo"})


def test_pair_sportingbet_to_betano_universe() -> None:
    events = discover_sportingbet_events(
        {
            "fixtures": [
                {"id": "a", "name": {"value": "Inglaterra - RD Congo"}},
                {"id": "b", "name": {"value": "Franca - Noruega"}},
            ]
        }
    )

    paired = pair_sportingbet_to_betano_universe(
        events,
        {frozenset({"inglaterra", "rd congo"})},
    )

    assert paired == ["a"]


def test_build_detail_params_uses_all_offer_mapping_and_fixture_id() -> None:
    params = build_detail_params(access_id="access", fixture_id="2:7826108")

    assert params["x-bwin-accessid"] == "access"
    assert params["offerMapping"] == "All"
    assert params["fixtureIds"] == "2:7826108"
    assert "skip" not in params
    assert "take" not in params
