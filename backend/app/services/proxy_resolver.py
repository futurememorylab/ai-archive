"""ProxyResolver protocol + REST and filesystem implementations. Resolves
a clip_id to a local file path (downloading via CatDV REST if needed)
and records the result in ProxyCacheRepo."""

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import aiosqlite

from backend.app.media_kind import is_image_path
from backend.app.repositories.proxy_cache import ProxyCacheRepo

if TYPE_CHECKING:
    from backend.app.services.media_store_map import MediaStoreMap


@runtime_checkable
class ProxyResolver(Protocol):
    is_host_local: bool

    async def path_for_clip_id(self, clip_id: int) -> Path: ...
    def is_managed(self, path: Path) -> bool: ...


class RestProxyResolver:
    """Downloads proxies via CatDV REST and caches them on local disk.

    After a successful download, records the file into `proxy_cache` so
    `CacheInspector` and friends see it.
    """

    is_host_local = False

    def __init__(
        self,
        catdv,
        cache_dir: Path,
        *,
        proxy_cache_repo: ProxyCacheRepo | None = None,
        db_provider: Callable[[], aiosqlite.Connection] | None = None,
        archive=None,
    ) -> None:
        self._catdv = catdv
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._repo = proxy_cache_repo
        self._db_provider = db_provider
        self._archive = archive

    async def path_for_clip_id(self, clip_id: int) -> Path:
        # Cache hit by recorded row — avoids a get_clip round-trip and works
        # for both image ({id}.jpg) and video ({id}.mov) files. Also repairs
        # legacy rows with NULL provider_* columns.
        if self._repo is not None and self._db_provider is not None:
            conn = self._db_provider()
            existing = await self._repo.get(conn, clip_id)
            if existing is not None:
                cached = Path(existing["file_path"])
                if cached.exists() and cached.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                    if existing.get("provider_id"):
                        await self._repo.touch(conn, clip_id)
                    else:
                        await self._repo.record(
                            conn,
                            clip_id=clip_id,
                            file_path=str(cached),
                            size_bytes=cached.stat().st_size,  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                            etag=None,
                            provider_id="catdv",
                            provider_clip_id=str(clip_id),
                        )
                    return cached

        dest, download = await self._dest_and_downloader(clip_id)
        if not dest.exists() or dest.stat().st_size == 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            await download()

        if self._repo is not None and self._db_provider is not None:
            conn = self._db_provider()
            await self._repo.record(
                conn,
                clip_id=clip_id,
                file_path=str(dest),
                size_bytes=dest.stat().st_size,  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                etag=None,
                provider_id="catdv",
                provider_clip_id=str(clip_id),
            )
        return dest

    async def _dest_and_downloader(
        self, clip_id: int
    ) -> tuple[Path, Callable[[], Awaitable[None]]]:
        """Return (dest_path, async download callable) for this clip.

        Images → original file at {id}{ext} via download_original; everything
        else → web proxy at {id}.mov via download_proxy. Falls back to the
        video path when no archive is wired (preserves legacy behaviour).
        """
        if self._archive is not None:
            clip = await self._archive.get_clip(str(clip_id))
            media = clip.provider_data.get("media") or {}
            file_path = media.get("filePath")
            media_id = media.get("ID")
            if isinstance(file_path, str) and is_image_path(file_path) and media_id is not None:
                ext = Path(file_path).suffix.lower()
                dest = self._cache_dir / f"{clip_id}{ext}"
                mid = int(media_id)

                async def _dl_image() -> None:
                    await self._catdv.download_original(mid, dest)

                return dest, _dl_image

        dest = self._cache_dir / f"{clip_id}.mov"

        async def _dl_video() -> None:
            await self._catdv.download_proxy(clip_id, dest)

        return dest, _dl_video

    def is_managed(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._cache_dir.resolve())
        except ValueError:
            return False
        return True


class ProxyNotFound(FileNotFoundError):
    """Raised when a proxy can't be located on the filesystem."""


class FilesystemProxyResolver:
    """Returns proxy paths from the CatDV server's local filesystem.

    No download. Uses `/mediastores` to map the clip's `media.filePath`
    (hires) to its on-disk web-proxy path. Intended for deployments
    running on the same host as the CatDV server.
    """

    is_host_local = True
    # NOTE: image (still) clips are only supported on the REST proxy path
    # (RestProxyResolver). Host-local stills have no on-disk proxy, so they
    # are out of scope here — see ADR 0026.

    def __init__(self, *, archive, media_store_map: "MediaStoreMap") -> None:
        self._archive = archive
        self._map = media_store_map

    async def path_for_clip_id(self, clip_id: int) -> Path:
        clip = await self._archive.get_clip(str(clip_id))
        media = (clip.provider_data or {}).get("media") or {}
        hires = media.get("filePath")
        if not hires:
            raise ProxyNotFound(f"clip {clip_id}: no media.filePath")
        proxy = self._map.resolve_proxy(hires)
        if proxy is None:
            raise ProxyNotFound(f"clip {clip_id}: no mediastore rule for {hires!r}")
        if not proxy.exists():  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            raise ProxyNotFound(f"clip {clip_id}: proxy not on disk: {proxy}")
        if not os.access(proxy, os.R_OK):
            raise ProxyNotFound(f"clip {clip_id}: proxy not readable: {proxy}")
        return proxy

    def is_managed(self, path: Path) -> bool:
        return False


class LocalCacheOnlyResolver:
    """Returns proxy paths only if they're already on local disk.

    Does NOT contact CatDV. Used when the app runs in offline mode
    (CATDV_OFFLINE=true or detected disconnect). Raises ``ProxyNotFound``
    when the requested clip's proxy hasn't been previously cached.
    """

    is_host_local = False

    def __init__(
        self,
        *,
        repo: ProxyCacheRepo,
        db_provider: Callable[[], aiosqlite.Connection],
        cache_dir: Path | None = None,
    ) -> None:
        self._repo = repo
        self._db_provider = db_provider
        self._cache_dir = cache_dir

    async def path_for_clip_id(self, clip_id: int) -> Path:
        row = await self._repo.get(self._db_provider(), clip_id)
        if row is None:
            raise ProxyNotFound(f"clip {clip_id} not cached locally")
        file_path = Path(row["file_path"])
        if not file_path.exists() or file_path.stat().st_size == 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            raise ProxyNotFound(f"clip {clip_id} cache row present but file missing: {file_path}")
        return file_path

    def is_managed(self, path: Path) -> bool:
        if self._cache_dir is None:
            return False
        try:
            path.resolve().relative_to(self._cache_dir.resolve())
        except ValueError:
            return False
        return True


def build_resolver(
    *,
    source: str,
    catdv_client,
    cache_dir: Path | None,
    archive=None,
    media_store_map: "MediaStoreMap | None" = None,
    proxy_cache_repo: ProxyCacheRepo | None = None,
    db_provider: Callable[[], aiosqlite.Connection] | None = None,
) -> ProxyResolver:
    if source == "cache-only":
        if proxy_cache_repo is None or db_provider is None:
            raise ValueError("cache-only source requires proxy_cache_repo and db_provider")
        return LocalCacheOnlyResolver(
            repo=proxy_cache_repo,
            db_provider=db_provider,
            cache_dir=cache_dir,
        )
    if source == "rest":
        if cache_dir is None or catdv_client is None:
            raise ValueError("rest source requires catdv_client and cache_dir")
        return RestProxyResolver(
            catdv=catdv_client,
            cache_dir=cache_dir,
            proxy_cache_repo=proxy_cache_repo,
            db_provider=db_provider,
            archive=archive,
        )
    if source == "filesystem":
        if archive is None or media_store_map is None:
            raise ValueError("filesystem source requires archive provider and media_store_map")
        return FilesystemProxyResolver(archive=archive, media_store_map=media_store_map)
    raise ValueError(f"unknown PROXY_SOURCE: {source!r}")
