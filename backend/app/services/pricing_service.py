"""DB-backed per-model pricing. Mirrors EnumService: idempotent boot-time
reconcile of code seeds into model_config, then load the rows into the
process-wide rate cache used by services/pricing.compute_cost.

DB-only and offline-safe — every method is a local SQLite read/write, no
network. Lives on CoreCtx.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

import aiosqlite

from backend.app.repositories.model_config import ModelConfigRepo, ModelConfigRow
from backend.app.services import pricing
from backend.app.services.pricing import PRICING_VERSION, RateCard


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PricingService:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        repo: ModelConfigRepo,
    ) -> None:
        self._db = db_provider
        self._repo = repo

    async def reconcile_seeds(self) -> None:
        """Insert any seed model absent from model_config; never clobber edits
        or revive a tombstone (INSERT OR IGNORE in the repo)."""
        conn = self._db()
        now = _now()
        for model, card in pricing.SEED_RATE_CARDS.items():
            await self._repo.upsert_seed(
                conn,
                ModelConfigRow(
                    model=model,
                    input_text_video_image_per_1m=card.input_text_video_image_per_1m,
                    input_audio_per_1m=card.input_audio_per_1m,
                    input_cached_per_1m=card.input_cached_per_1m,
                    output_per_1m=card.output_per_1m,
                    source_url=card.source_url,
                    default_media_resolution="medium",
                    pricing_version=PRICING_VERSION,
                    updated_at=now,
                    removed=0,
                    created_at=now,
                ),
                commit=False,
            )
        await conn.commit()

    async def reload(self) -> None:
        """Load the live rows into the active rate cache."""
        conn = self._db()
        rows = await self._repo.all_live(conn)
        pricing.set_rate_cards(
            {
                r.model: RateCard(
                    input_text_video_image_per_1m=r.input_text_video_image_per_1m,
                    input_audio_per_1m=r.input_audio_per_1m,
                    input_cached_per_1m=r.input_cached_per_1m,
                    output_per_1m=r.output_per_1m,
                    source_url=r.source_url,
                    pricing_version=r.pricing_version,
                )
                for r in rows
            }
        )

    async def edit_rates(
        self,
        model: str,
        *,
        input_text_video_image_per_1m: float,
        input_audio_per_1m: float,
        input_cached_per_1m: float,
        output_per_1m: float,
    ) -> None:
        """Admin edit: persist new rates with a bumped pricing_version, then
        refresh the active cache. Past run_telemetry rows are untouched."""
        conn = self._db()
        await self._repo.update_rates(
            conn,
            model,
            input_text_video_image_per_1m=input_text_video_image_per_1m,
            input_audio_per_1m=input_audio_per_1m,
            input_cached_per_1m=input_cached_per_1m,
            output_per_1m=output_per_1m,
            pricing_version=f"edit-{_now()}",
            commit=True,
        )
        await self.reload()

    async def set_rates(
        self,
        model: str,
        *,
        input_text_video_image_per_1m: float,
        input_audio_per_1m: float,
        input_cached_per_1m: float,
        output_per_1m: float,
    ) -> None:
        """Create-or-update a model's rate card (bumped pricing_version), then
        refresh the active cache."""
        conn = self._db()
        await self._repo.set_rates(
            conn,
            model,
            input_text_video_image_per_1m=input_text_video_image_per_1m,
            input_audio_per_1m=input_audio_per_1m,
            input_cached_per_1m=input_cached_per_1m,
            output_per_1m=output_per_1m,
            pricing_version=f"edit-{_now()}",
            commit=True,
        )
        await self.reload()

    async def remove_model(self, model: str) -> None:
        """Soft-delete a model's rate card and refresh the cache."""
        conn = self._db()
        await self._repo.soft_delete(conn, model, commit=True)
        await self.reload()

    async def rows(self) -> list[ModelConfigRow]:
        return await self._repo.all_live(self._db())
