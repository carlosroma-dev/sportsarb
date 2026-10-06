from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração da API lida de variáveis de ambiente / .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    local_db_path: str = "portfolio.db"
    demo_mode: bool = True

    frontend_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    enable_deep_markets: bool = False
    # Tenis (vencedor + total de games) roda junto do loop deep quando este
    # esta habilitado; ENABLE_TENNIS=false desliga so o tenis.
    enable_tennis: bool = True
    deep_competition: str = "copa-do-mundo"
    deep_competitions: str = ""
    deep_scan_interval_seconds: float = 300.0
    deep_min_arb_pct: str = "0"
    bankroll_default: str = "1000"
    deep_notify_pct: str = "8"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.frontend_origins.split(",") if o.strip()]

    @property
    def deep_competition_aliases(self) -> list[str]:
        raw = self.deep_competitions or self.deep_competition
        aliases = [alias.strip() for alias in raw.split(",") if alias.strip()]
        return aliases or ["copa-do-mundo"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
