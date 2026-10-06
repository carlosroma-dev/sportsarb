import pytest
from app.core.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    app = create_app(Settings(local_db_path=str(tmp_path / "portfolio.db")))
    with TestClient(app) as test_client:
        yield test_client
