def test_demo_signals_are_available_without_credentials(client):
    response = client.get("/api/v1/signals")
    assert response.status_code == 200
    body = response.json()
    assert body["competition"] == "demo"
    assert body["collected_by_house"] == {"demo_a": 1, "demo_b": 1}
    assert len(body["signals"]) == 1
    assert body["signals"][0]["over_bookmaker"] == "demo_a"


def test_excluding_demo_source_recomputes_signals(client):
    response = client.put(
        "/api/v1/preferences",
        json={"excludedBookmakers": ["demo_a"], "minArb": "0", "bankroll": "1000"},
    )
    assert response.status_code == 200
    assert client.get("/api/v1/signals").json()["signals"] == []


def test_market_and_bookmaker_options(client):
    assert client.get("/api/v1/markets").status_code == 200
    assert {row["id"] for row in client.get("/api/v1/bookmakers").json()} == {"demo_a", "demo_b"}
