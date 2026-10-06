def test_preferences_and_operations_persist_across_app_instances(tmp_path):
    from app.core.config import Settings
    from app.main import create_app
    from fastapi.testclient import TestClient

    settings = Settings(local_db_path=str(tmp_path / "local.db"))
    with TestClient(create_app(settings)) as client:
        assert (
            client.put(
                "/api/v1/preferences",
                json={"excludedBookmakers": ["demo_b"], "minArb": "2", "bankroll": "500"},
            ).status_code
            == 200
        )
        operation = client.post(
            "/api/v1/operations",
            json={
                "event_name": "Aurora x Horizonte",
                "market_name": "Escanteios",
                "bookmaker_1": "demo_a",
                "bookmaker_2": "demo_b",
                "odd_1": 2.25,
                "odd_2": 2.10,
                "stake_1": 100,
                "stake_2": 100,
                "total_stake": 200,
                "expected_return": 210,
                "profit": 10,
                "roi": 5,
            },
        )
        assert operation.status_code == 201
        operation_id = operation.json()["id"]

    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/preferences").json()["bankroll"] == "500"
        assert len(client.get("/api/v1/operations").json()) == 1
        assert client.patch(f"/api/v1/operations/{operation_id}/cancel").status_code == 204
        assert client.get("/api/v1/operations").json()[0]["status"] == "cancelada"
