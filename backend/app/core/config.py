"""Configuração da aplicação (variáveis de ambiente / arquivo .env)."""

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "QuantFinance Hub"

    # Trocar por postgresql+asyncpg://user:pass@host/db para migrar de SQLite -> PostgreSQL.
    database_url: str = "sqlite+aiosqlite:///./quantfinance.db"
    sql_echo: bool = False

    # --- Pluggy ---
    pluggy_base_url: str = "https://api.pluggy.ai"
    pluggy_client_id: str = Field(
        default="", validation_alias=AliasChoices("PLUGGY_CLIENT_ID", "CLIENT_ID")
    )
    pluggy_client_secret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("PLUGGY_CLIENT_SECRET", "CLIENT_SECRET"),
    )
    pluggy_item_id: str | None = None
    pluggy_timeout_seconds: float = 30.0
    pluggy_max_retries: int = 4
    # Janela de reprocessamento em syncs incrementais (transações PENDING viram POSTED depois).
    pluggy_transactions_overlap_days: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
