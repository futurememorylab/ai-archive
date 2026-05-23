from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.app.archive.ai_store_model import (
    AIStoreCapabilities,
    StoreHealth,
    UploadedRef,
)
from backend.app.archive.model import ClipKey

log = logging.getLogger(__name__)


class GcsInputStore:
    """Implements the AIInputStore Protocol over a GcsService and a DB-backed
    registry. PR 2 keeps the bucket-side blob naming as 'clips/<int>.mov'.
    """

    capabilities = AIStoreCapabilities(
        persistent=True,
        dedup_by_sha256=True,
        # No hard cap from GCS itself; we set a large bound for parity with
        # the AIInputStore contract.
        max_file_bytes=5 * 1024 * 1024 * 1024 * 1024,  # 5 TiB
    )

    def __init__(
        self,
        *,
        gcs: Any,  # GcsService duck-typed for testability
        files_repo: Any,  # AIStoreFilesRepo
        db_provider: Callable[[], Any],
    ) -> None:
        self._gcs = gcs
        self._repo = files_repo
        self._db_provider = db_provider
        self.id = f"gcs:{gcs.bucket_name}"

    async def ensure_uploaded(self, clip_key: ClipKey, local_path: Path, mime: str) -> UploadedRef:
        clip_id = int(clip_key[1])
        db = self._db_provider()
        sha = _sha256(local_path)

        existing = await self._repo.get(db, store_id=self.id, clip_id=clip_id)
        if existing is not None and existing["sha256"] == sha:
            await self._repo.touch(db, store_id=self.id, clip_id=clip_id)
            return self._row_to_ref(existing)

        gcs_uri = self._gcs.upload_if_absent(clip_id=clip_id, local_path=local_path, mime=mime)
        size = local_path.stat().st_size

        await self._repo.upsert(
            db,
            store_id=self.id,
            clip_id=clip_id,
            gcs_uri=gcs_uri,
            mime_type=mime,
            size_bytes=size,
            sha256=sha,
            expires_at=None,
            provider_id=clip_key[0],
            provider_clip_id=str(clip_key[1]),
        )

        return UploadedRef(
            handle=gcs_uri,
            mime_type=mime,
            size_bytes=size,
            sha256=sha,
            uploaded_at=datetime.now(UTC),
            expires_at=None,
        )

    async def status(self, clip_key: ClipKey) -> UploadedRef | None:
        clip_id = int(clip_key[1])
        row = await self._repo.get(self._db_provider(), store_id=self.id, clip_id=clip_id)
        if row is None:
            return None
        return self._row_to_ref(row)

    async def evict(self, clip_key: ClipKey) -> None:
        clip_id = int(clip_key[1])
        db = self._db_provider()
        row = await self._repo.get(db, store_id=self.id, clip_id=clip_id)
        if row is None:
            return
        try:
            self._gcs.delete(clip_id=clip_id)
        except Exception:  # noqa: BLE001
            log.exception("gcs delete failed for clip_id=%s", clip_id)
        await self._repo.delete(db, store_id=self.id, clip_id=clip_id)

    async def health(self) -> StoreHealth:
        try:
            ok = self._gcs._bucket.exists()
        except Exception as exc:  # noqa: BLE001
            return StoreHealth(ok=False, detail=str(exc))
        if not ok:
            return StoreHealth(ok=False, detail=f"bucket not found: {self._gcs.bucket_name}")
        return StoreHealth(ok=True)

    async def reference_for_gemini(self, ref: UploadedRef) -> dict:
        return {
            "file_data": {
                "file_uri": ref.handle,
                "mime_type": ref.mime_type,
            }
        }

    @staticmethod
    def _row_to_ref(row: dict[str, Any]) -> UploadedRef:
        uploaded_at = _parse_iso(row.get("uploaded_at"))
        expires_at = _parse_iso(row.get("expires_at"))
        return UploadedRef(
            handle=row["gcs_uri"],
            mime_type=row["mime_type"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            uploaded_at=uploaded_at,
            expires_at=expires_at,
        )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_iso(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
