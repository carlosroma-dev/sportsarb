from app.core.config import Settings
from app.main import _start_deep_loop, create_app
from fastapi.testclient import TestClient


def test_loop_disabled_by_default(tmp_path):
    # enable_deep_markets=False (default): o startup NAO deve criar tasks do loop.
    settings = Settings(local_db_path=str(tmp_path / "portfolio.db"))
    app = create_app(settings)
    with TestClient(app):
        pass  # dispara o evento de startup
    assert getattr(app.state, "deep_tasks", set()) == set()
    assert callable(_start_deep_loop)
