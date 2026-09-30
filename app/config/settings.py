from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"
    service_api_key: str = ""

    connector_kind: str = "mock"
    mock_data_dir: str = "/data/backups"

    analytics_pg_host: str
    analytics_pg_port: int = 5432
    analytics_pg_db: str
    analytics_pg_user: str
    analytics_pg_password: str

    core_pg_host: str
    core_pg_port: int = 5432
    core_pg_db: str
    core_pg_user: str
    core_pg_password: str

    core_ch_host: str
    core_ch_port: int = 8123
    core_ch_db: str
    core_ch_user: str
    core_ch_password: str
    core_api_url: str = "http://tm-app:8080"

    cors_origins: str = "http://localhost:5173"

    scheduler_enabled: bool = True
    debug_charts: bool = False

    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440

    @property
    def analytics_dsn(self) -> str:
        return (f"postgresql+psycopg://{self.analytics_pg_user}:{self.analytics_pg_password}"
                f"@{self.analytics_pg_host}:{self.analytics_pg_port}/{self.analytics_pg_db}")

    @property
    def core_dsn(self) -> str:
        return (f"postgresql+psycopg://{self.core_pg_user}:{self.core_pg_password}"
                f"@{self.core_pg_host}:{self.core_pg_port}/{self.core_pg_db}")


@lru_cache
def get_settings() -> Settings:
    return Settings()