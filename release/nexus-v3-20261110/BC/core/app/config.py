"""Environment-driven configuration for nexus-core."""
from functools import lru_cache
from typing import Literal

from pydantic import Field
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
    vision4_mode: Literal["off", "shadow", "active"] = "off"
    vision4_model_dir: str = "/app/data/models/vision-model-v1"
    shadow_sample_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    shadow_trace_max_bytes: int = Field(default=8192, ge=1024, le=65536)
    shadow_error_log_interval_s: int = Field(default=60, ge=10, le=3600)
    shadow_capacity_warn_bytes: int = Field(default=524288000, ge=1048576)
    shadow_recent_error_warn_count: int = Field(default=10, ge=1)
    active_router_enabled: bool = False
    active_router_source_ids: str = ""
    active_router_kill_switch: bool = True

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
