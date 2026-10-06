from app.core.config import Settings


def test_local_settings_need_no_credentials():
    settings = Settings()
    assert settings.demo_mode is True
    assert settings.local_db_path == "portfolio.db"
    assert "http://127.0.0.1:5173" in settings.origins_list
