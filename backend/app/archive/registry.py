from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.archive.providers.fs.adapter import (
    DEFAULT_MEDIA_EXTS,
    FilesystemArchiveProvider,
)


def build_archive_provider(
    settings: Any,
    *,
    catdv_client: Any = None,
    clip_cache_repo: Any = None,
    field_def_cache_repo: Any = None,
    clip_list_cache_repo: Any = None,
    db_provider: Any = None,
    is_online_provider: Any = None,
) -> ArchiveProvider:
    """Construct the active ArchiveProvider from settings.

    `settings` is duck-typed (only `archive_provider`, `clip_cache_ttl_hours`,
    `clip_list_cache_ttl_minutes`, `catdv_catalog_id`, `fs_root`,
    `fs_media_exts` are read) so this is easy to test without the full
    pydantic-settings instance.
    """
    name = getattr(settings, "archive_provider", "catdv")
    if name == "catdv":
        # client can be None when booting offline (CATDV_OFFLINE=true or
        # login failure at startup). The adapter only touches its client
        # when _is_online() is true.
        if catdv_client is None and is_online_provider is None:
            raise ValueError("archive_provider=catdv requires a catdv_client")
        return CatdvArchiveAdapter(
            client=catdv_client,
            clip_cache_repo=clip_cache_repo,
            field_def_cache_repo=field_def_cache_repo,
            clip_list_cache_repo=clip_list_cache_repo,
            db_provider=db_provider,
            clip_cache_ttl_hours=int(
                getattr(settings, "clip_cache_ttl_hours", 168)
            ),
            clip_list_cache_ttl_minutes=int(
                getattr(settings, "clip_list_cache_ttl_minutes", 10)
            ),
            default_catalog_id=str(getattr(settings, "catdv_catalog_id", "")),
            is_online_provider=is_online_provider,
        )
    if name == "fs":
        fs_root = getattr(settings, "fs_root", None)
        if fs_root is None or str(fs_root) in ("", "."):
            raise ValueError("archive_provider=fs requires fs_root")
        exts_setting = getattr(settings, "fs_media_exts", None)
        if isinstance(exts_setting, str) and exts_setting.strip():
            media_exts = tuple(
                e for e in (s.strip() for s in exts_setting.split(",")) if e
            )
        elif isinstance(exts_setting, (list, tuple)) and exts_setting:
            media_exts = tuple(exts_setting)
        else:
            media_exts = DEFAULT_MEDIA_EXTS
        return FilesystemArchiveProvider(
            fs_root=Path(fs_root),
            media_exts=media_exts,
            clip_cache_repo=clip_cache_repo,
            field_def_cache_repo=field_def_cache_repo,
            db_provider=db_provider,
        )
    raise ValueError(f"unknown archive_provider: {name!r}")
