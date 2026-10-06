def test_health_is_public_for_local_demo(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["signals"] == 1
