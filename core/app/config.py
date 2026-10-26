"""Environment-driven configuration for nexus-core."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    """Settings resolved from NEXUS_-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="NEXUS_", extra="ignore")

    db_host: str = "nexus-db"
    db_port: int = 5432
    db_user: str = "nexus"
    db_password: str = "nexus"
    db_name: str = "nexus"

    media_dir: str = "/app/data/media"
    static_dir: str = "/app/static"
    admin_password: str = "nexus-admin-dev"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    extract_batch: int = 20
    extract_interval_s: float = 2.0
    extract_ocr_enabled: bool = True

    def db_kwargs(self) -> dict:
        """Connection parameters passed as typed kwargs (never a URL string)."""
        return {
            "host": self.db_host,
            "port": self.db_port,
            "user": self.db_user,
            "password": self.db_password,
            "database": self.db_name,
        }


@lru_cache
def get_settings() -> CoreSettings:
    return CoreSettings()
