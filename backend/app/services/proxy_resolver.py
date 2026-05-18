import os
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ProxyResolver(Protocol):
    async def path_for_clip_id(self, clip_id: int) -> Path: ...
    def is_managed(self, path: Path) -> bool: ...


class RestProxyResolver:
    """Downloads proxies via CatDV REST and caches them on local disk."""

    def __init__(self, catdv, cache_dir: Path) -> None:
        self._catdv = catdv
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def path_for_clip_id(self, clip_id: int) -> Path:
        dest = self._cache_dir / f"{clip_id}.mov"
        if not dest.exists() or dest.stat().st_size == 0:
            await self._catdv.download_proxy(clip_id, dest)
        return dest

    def is_managed(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._cache_dir.resolve())
        except ValueError:
            return False
        return True


class ProxyNotFound(FileNotFoundError):
    """Raised when a proxy can't be located on the filesystem."""


class FilesystemProxyResolver:
    """Returns proxy paths from the CatDV server's local filesystem (no download)."""

    def __init__(self, root: Path, path_template: str = "{root}/{clip_id}.mov") -> None:
        self._root = root
        self._template = path_template

    async def path_for_clip_id(self, clip_id: int) -> Path:
        path = Path(self._template.format(root=str(self._root), clip_id=clip_id))
        if not path.exists():
            raise ProxyNotFound(f"proxy not on disk: {path}")
        if not os.access(path, os.R_OK):
            raise ProxyNotFound(f"proxy not readable: {path}")
        return path

    def is_managed(self, path: Path) -> bool:
        return False


def build_resolver(
    *,
    source: str,
    catdv_client,
    cache_dir: Path | None,
    fs_root: Path | None,
    path_template: str | None,
) -> ProxyResolver:
    if source == "rest":
        if cache_dir is None or catdv_client is None:
            raise ValueError("rest source requires catdv_client and cache_dir")
        return RestProxyResolver(catdv=catdv_client, cache_dir=cache_dir)
    if source == "filesystem":
        if fs_root is None:
            raise ValueError("filesystem source requires fs_root")
        return FilesystemProxyResolver(
            root=fs_root,
            path_template=path_template or "{root}/{clip_id}.mov",
        )
    raise ValueError(f"unknown PROXY_SOURCE: {source!r}")
