from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    data_dir: Path = Field(default=Path("./data"))

    catdv_base_url: str
    catdv_username: str | None = None
    catdv_password: str | None = None
    catdv_catalog_id: int

    proxy_source: Literal["rest", "filesystem"] = "rest"
    proxy_fs_root: Path | None = None
    proxy_path_template: str | None = None
    proxy_cache_cap_gb: float = 20.0

    gcp_project_id: str
    gcp_location: str = "europe-west3"
    gcs_bucket_name: str
    google_application_credentials: Path | None = None
    gemini_model: str = "gemini-2.5-flash-lite"

    archive_provider: str = "catdv"
    ai_input_store: str = "gcs"
    clip_cache_ttl_hours: int = 168
    clip_list_cache_ttl_minutes: int = 10

    # Filesystem archive provider (when ARCHIVE_PROVIDER=fs)
    fs_root: Path | None = None
    fs_media_exts: str = ".mov,.mp4,.mkv,.mxf,.m4v,.avi"

    # connection monitor
    health_probe_interval_s: int = 30
    health_probe_timeout_s: int = 5

    # sync engine
    sync_retry_base_s: int = 2
    sync_retry_max_s: int = 300
    sync_tick_interval_s: int = 5

    # cache management + LRU eviction
    media_cache_cap_gb: int = 50
    lru_tick_interval_s: int = 300

    # media prefetch queue
    prefetch_tick_interval_s: int = 2

    @model_validator(mode="after")
    def _validate_proxy(self) -> "Settings":
        fs_root_empty = self.proxy_fs_root is None or str(self.proxy_fs_root) in ("", ".")
        if self.proxy_source == "filesystem" and fs_root_empty:
            raise ValueError("PROXY_FS_ROOT is required when PROXY_SOURCE=filesystem")
        return self

    @model_validator(mode="after")
    def _validate_fs_archive(self) -> "Settings":
        if self.archive_provider == "fs":
            empty = self.fs_root is None or str(self.fs_root) in ("", ".")
            if empty:
                raise ValueError("FS_ROOT is required when ARCHIVE_PROVIDER=fs")
        return self


def load_settings() -> Settings:
    return Settings()
