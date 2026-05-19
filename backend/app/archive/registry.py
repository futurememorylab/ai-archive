from __future__ import annotations

from typing import Any

from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter


def build_archive_provider(
    settings: Any,
    *,
    catdv_client: Any,
    clip_cache_repo: Any = None,
    field_def_cache_repo: Any = None,
    db_provider: Any = None,
) -> ArchiveProvider:
    """Construct the active ArchiveProvider from settings.

    `settings` is duck-typed (only `archive_provider`, `clip_cache_ttl_hours`,
    and `catdv_catalog_id` are read) so this is easy to test without the full
    pydantic-settings instance.
    """
    name = getattr(settings, "archive_provider", "catdv")
    if name == "catdv":
        if catdv_client is None:
            raise ValueError("archive_provider=catdv requires a catdv_client")
        return CatdvArchiveAdapter(
            client=catdv_client,
            clip_cache_repo=clip_cache_repo,
            field_def_cache_repo=field_def_cache_repo,
            db_provider=db_provider,
            clip_cache_ttl_hours=int(
                getattr(settings, "clip_cache_ttl_hours", 168)
            ),
            default_catalog_id=str(getattr(settings, "catdv_catalog_id", "")),
        )
    raise ValueError(f"unknown archive_provider: {name!r}")
