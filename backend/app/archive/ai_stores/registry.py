from __future__ import annotations

from typing import Any, Callable

from backend.app.archive.ai_store import AIInputStore
from backend.app.archive.ai_stores.gcs.adapter import GcsInputStore
from backend.app.archive.ai_stores.gemini_files.adapter import (
    GeminiFilesInputStore,
)


def build_ai_input_store(
    settings: Any,
    *,
    gcs_service: Any,
    files_repo: Any,
    db_provider: Callable[[], Any],
) -> AIInputStore:
    """Construct the active AIInputStore from settings.

    `settings` is duck-typed (only `ai_input_store` is read).
    """
    name = getattr(settings, "ai_input_store", "gcs")
    if name == "gcs":
        if gcs_service is None:
            raise ValueError("ai_input_store=gcs requires a gcs_service")
        return GcsInputStore(
            gcs=gcs_service, files_repo=files_repo, db_provider=db_provider
        )
    if name == "gemini-files":
        return GeminiFilesInputStore()
    raise ValueError(f"unknown ai_input_store: {name!r}")
