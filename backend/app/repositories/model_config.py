"""Repository for per-model pricing + default resolution (table: model_config).

Leaf layer — no service imports. `removed` is a soft delete so the boot-time
reconcile never re-adds a model the admin deleted. `upsert_seed` is INSERT OR
IGNORE so it never clobbers an admin edit (mirrors EnumValuesRepo.upsert_seed).
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite

_COLS = (
    "model, input_text_video_image_per_1m, input_audio_per_1m, "
    "input_cached_per_1m, output_per_1m, source_url, default_media_resolution, "
    "pricing_version, updated_at, removed, created_at"
)


@dataclass
class ModelConfigRow:
    model: str
    input_text_video_image_per_1m: float
    input_audio_per_1m: float
    input_cached_per_1m: float
    output_per_1m: float
    source_url: str
    default_media_resolution: str
    pricing_version: str
    updated_at: str
    removed: int
    created_at: str


def _row(r: tuple) -> ModelConfigRow:
    return ModelConfigRow(*r)


class ModelConfigRepo:
    async def get(self, conn: aiosqlite.Connection, model: str) -> ModelConfigRow | None:
        """Return the row for `model` regardless of its `removed` state, or None
        if absent. Reconcile/admin paths need to see tombstones (so a deleted
        model is not silently re-seeded); callers that want only live rows use
        `all_live`."""
        cur = await conn.execute(
            f"SELECT {_COLS} FROM model_config WHERE model = ?", (model,)
        )
        r = await cur.fetchone()
        return _row(r) if r else None

    async def all_live(self, conn: aiosqlite.Connection) -> list[ModelConfigRow]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM model_config WHERE removed = 0 ORDER BY model"
        )
        return [_row(r) for r in await cur.fetchall()]

    async def upsert_seed(
        self, conn: aiosqlite.Connection, row: ModelConfigRow, *, commit: bool
    ) -> None:
        """Insert a seed model only when absent. Never touches an existing row."""
        await conn.execute(
            f"INSERT OR IGNORE INTO model_config ({_COLS}) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                row.model,
                row.input_text_video_image_per_1m,
                row.input_audio_per_1m,
                row.input_cached_per_1m,
                row.output_per_1m,
                row.source_url,
                row.default_media_resolution,
                row.pricing_version,
                row.updated_at,
                row.removed,
                row.created_at,
            ),
        )
        if commit:
            await conn.commit()

    async def update_rates(
        self,
        conn: aiosqlite.Connection,
        model: str,
        *,
        input_text_video_image_per_1m: float,
        input_audio_per_1m: float,
        input_cached_per_1m: float,
        output_per_1m: float,
        pricing_version: str,
        commit: bool,
    ) -> None:
        await conn.execute(
            "UPDATE model_config SET input_text_video_image_per_1m = ?, "
            "input_audio_per_1m = ?, input_cached_per_1m = ?, output_per_1m = ?, "
            "pricing_version = ?, updated_at = datetime('now') "
            "WHERE model = ? AND removed = 0",
            (
                input_text_video_image_per_1m,
                input_audio_per_1m,
                input_cached_per_1m,
                output_per_1m,
                pricing_version,
                model,
            ),
        )
        if commit:
            await conn.commit()

    async def set_rates(
        self,
        conn: aiosqlite.Connection,
        model: str,
        *,
        input_text_video_image_per_1m: float,
        input_audio_per_1m: float,
        input_cached_per_1m: float,
        output_per_1m: float,
        pricing_version: str,
        commit: bool,
    ) -> None:
        """Create-or-update a rate card (and revive a tombstone). On INSERT,
        source_url='' and default_media_resolution='medium'; on UPDATE those two
        are preserved (not in the SET list)."""
        await conn.execute(
            "INSERT INTO model_config (model, input_text_video_image_per_1m, "
            "input_audio_per_1m, input_cached_per_1m, output_per_1m, source_url, "
            "default_media_resolution, pricing_version, updated_at, removed, created_at) "
            "VALUES (?, ?, ?, ?, ?, '', 'medium', ?, datetime('now'), 0, datetime('now')) "
            "ON CONFLICT(model) DO UPDATE SET "
            "input_text_video_image_per_1m = excluded.input_text_video_image_per_1m, "
            "input_audio_per_1m = excluded.input_audio_per_1m, "
            "input_cached_per_1m = excluded.input_cached_per_1m, "
            "output_per_1m = excluded.output_per_1m, "
            "pricing_version = excluded.pricing_version, "
            "updated_at = datetime('now'), removed = 0",
            (
                model,
                input_text_video_image_per_1m,
                input_audio_per_1m,
                input_cached_per_1m,
                output_per_1m,
                pricing_version,
            ),
        )
        if commit:
            await conn.commit()

    async def set_resolution(
        self, conn: aiosqlite.Connection, model: str, resolution: str, *, commit: bool
    ) -> None:
        """Update only the model's default media resolution (live rows only)."""
        await conn.execute(
            "UPDATE model_config SET default_media_resolution = ?, "
            "updated_at = datetime('now') WHERE model = ? AND removed = 0",
            (resolution, model),
        )
        if commit:
            await conn.commit()

    async def soft_delete(
        self, conn: aiosqlite.Connection, model: str, *, commit: bool
    ) -> None:
        await conn.execute(
            "UPDATE model_config SET removed = 1 WHERE model = ?", (model,)
        )
        if commit:
            await conn.commit()
