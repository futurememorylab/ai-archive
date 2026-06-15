"""Settings — pydantic-settings model loaded from env + .env. Single
source of truth for runtime configuration (CatDV creds, GCP project,
cache caps, provider selection, etc.)."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    data_dir: Path = Field(default=Path("./data"))

    # Mandatory per-deployment identifier. Namespaces uploaded-clip GCS
    # object keys (instances/{instance_id}/uploads/{clip_id}.mov) so two
    # instances sharing one bucket cannot overwrite each other's uploads
    # (issue #55). No default -> the app refuses to boot if it is unset.
    instance_id: str

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
    # Proxy-media cache + playback backend. "local" (dev): download to
    # the local proxy cache and serve from disk, GCS as read fallback.
    # "ai_store" (cloud, ephemeral disk): cache writes upload to GCS and
    # playback redirects to signed URLs; the local proxy cache is unused.
    # See docs/specs/2026-06-10-cloud-media-cache-ai-store-design.md.
    media_cache: Literal["local", "ai_store"] = "local"

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

    # WireGuard / onetun (cloud only). Today consumed by entrypoint.sh; now
    # read here so the app can supervise onetun and expose a status/toggle.
    # vpn_managed (all four present) gates the whole VPN feature — true on
    # Cloud Run, false in local dev (no tunnel). WG_PRIVATE_KEY is a secret.
    wg_private_key: SecretStr | None = None
    wg_endpoint: str | None = None
    wg_peer_pubkey: str | None = None
    wg_source_ip: str | None = None
    wg_keepalive_s: int = 25
    # onetun tunnel MTU. 1000 (~1060B WireGuard wire packet) is verified to clear
    # the Cloud Run -> gateway path MTU; 1380 black-holed outbound multi-segment
    # requests (the writeback PUT). Prod overrides via ONETUN_MTU. See ADR 0076
    # (corrects ADR 0074).
    onetun_mtu: int = 1000
    onetun_local_forward: str = "127.0.0.1:18080:192.168.1.41:8080:TCP"

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

    @field_validator("instance_id")
    @classmethod
    def _instance_id_is_slug(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", v):
            raise ValueError(
                "INSTANCE_ID must be a lowercase slug matching "
                "[a-z0-9][a-z0-9-]* (e.g. 'prod', 'staging', 'local-pete')"
            )
        return v

    @model_validator(mode="after")
    def _validate_fs_archive(self) -> "Settings":
        if self.archive_provider == "fs":
            empty = self.fs_root is None or str(self.fs_root) in ("", ".")
            if empty:
                raise ValueError("FS_ROOT is required when ARCHIVE_PROVIDER=fs")
        return self

    @property
    def vpn_managed(self) -> bool:
        """True when WireGuard is configured (cloud). Gates the VPN feature."""
        return bool(
            self.wg_private_key is not None
            and self.wg_private_key.get_secret_value()
            and self.wg_endpoint
            and self.wg_peer_pubkey
            and self.wg_source_ip
        )


def load_settings() -> Settings:
    return Settings()
