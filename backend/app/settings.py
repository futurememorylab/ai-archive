"""Settings — pydantic-settings model loaded from env + .env. Single
source of truth for runtime configuration (CatDV creds, GCP project,
cache caps, provider selection, etc.)."""

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
    proxy_cache_cap_gb: float = 20.0
    catdv_offline: bool = False

    gcp_project_id: str
    gcp_location: str = "europe-west3"
    gcs_bucket_name: str
    google_application_credentials: Path | None = None
    gemini_model: str = "gemini-2.5-flash-lite"
    gemini_api_key: str | None = None
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    gemini_live_voice: str = "Aoede"
    gemini_live_inactivity_s: int = 60

    archive_provider: str = "catdv"
    ai_input_store: str = "gcs"
    clip_cache_ttl_hours: int = 168
    clip_list_cache_ttl_minutes: int = 10
    # Playback byte-source preference (NOT exclusive): MediaLocator tries
    # both cache layers, this just orders them. "local" = proxy cache
    # first (dev); "gcs" = signed URL from the AI store first (cloud,
    # where local disk is ephemeral). See the Cloud Run deployment spec.
    playback_source: Literal["local", "gcs"] = "local"

    # Prompt Studio uploads (Spec B). Web-safe only; no server-side
    # transcode, so the allowlist is browser-playable container/codecs.
    studio_upload_max_mb: int = 500
    studio_upload_allowed_mimes: str = "video/mp4,video/webm"

    # Filesystem archive provider (when ARCHIVE_PROVIDER=fs)
    fs_root: Path | None = None
    fs_media_exts: str = ".mov,.mp4,.mkv,.mxf,.m4v,.avi"

    # connection monitor
    health_probe_interval_s: int = 30
    health_probe_timeout_s: int = 5
    # boot-time CatDV login probe. Bounds how long startup can block on an
    # unreachable CatDV before degrading to offline (reconnect later via the
    # Reconnect button / the background probe). Kept short so dev restarts are
    # snappy; distinct from the 60s client timeout used for real downloads.
    catdv_startup_login_timeout_s: float = 2.0
    # CatDV connection lifecycle. "manual" (default): build the client but
    # do NOT log in at boot — the operator clicks Connect to spend a seat
    # and Disconnect to release it (the Cloud Run instance is always-on, so
    # auto-login would hold a seat 24/7). "auto": log in at startup (legacy
    # behavior, for local dev). CATDV_OFFLINE=true still wins (no client).
    catdv_connect_mode: Literal["auto", "manual"] = "manual"
    # Auto-disconnect (logout, freeing the seat) after this many seconds
    # with no operator-driven CatDV API call. The 5s pill poll and the
    # background health probe do NOT count as activity.
    catdv_idle_logout_s: int = 900
    # set by run.sh when launching uvicorn with --reload; disables the
    # in-app shutdown button (the reloader supervisor may respawn the worker)
    dev_reload: bool = False

    # sync engine
    sync_retry_base_s: int = 2
    sync_retry_max_s: int = 300
    sync_tick_interval_s: int = 5
    # Maximum attempts before a pending_op flips from 'pending' to 'failed'.
    # Prevents an infinitely-retried row from blocking the queue when the
    # underlying error never resolves. Default 10 ≈ ~17 min worst case at
    # default backoff (2,4,8,16,32,64,128,256,300,300 seconds).
    sync_max_attempts: int = 10

    # cache management + LRU eviction
    media_cache_cap_gb: int = 50
    lru_tick_interval_s: int = 300

    # media prefetch queue
    prefetch_tick_interval_s: int = 2

    @model_validator(mode="after")
    def _validate_fs_archive(self) -> "Settings":
        if self.archive_provider == "fs":
            empty = self.fs_root is None or str(self.fs_root) in ("", ".")
            if empty:
                raise ValueError("FS_ROOT is required when ARCHIVE_PROVIDER=fs")
        return self


def load_settings() -> Settings:
    return Settings()
