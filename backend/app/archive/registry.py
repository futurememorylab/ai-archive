from __future__ import annotations

from typing import Any

from backend.app.archive.provider import ArchiveProvider
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter


def build_archive_provider(settings: Any, *, catdv_client: Any) -> ArchiveProvider:
    """Construct the active ArchiveProvider from settings.

    `settings` is duck-typed (only `archive_provider` is read) so this is easy
    to test without the full pydantic-settings instance.
    """
    name = getattr(settings, "archive_provider", "catdv")
    if name == "catdv":
        if catdv_client is None:
            raise ValueError("archive_provider=catdv requires a catdv_client")
        return CatdvArchiveAdapter(client=catdv_client)
    raise ValueError(f"unknown archive_provider: {name!r}")
