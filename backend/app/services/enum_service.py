"""Single read/write API for centralised enumerations.

Fixed enums are served from the code registry; editable enums from the DB
(falling back to the registry seed when the DB is empty, so a list is never
empty). Lives on CoreCtx — DB-only, offline-safe. See the design spec.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import aiosqlite

from backend.app.enums.registry import ENUM_REGISTRY, EnumSpec
from backend.app.repositories.enum_values import EnumValuesRepo

GENERATION_MODEL_KEY = "gemini_generation_model"


class EnumError(Exception):
    """Raised for invalid enum writes (non-editable key, guard violations)."""


@dataclass(frozen=True)
class EnumDefinition:
    key: str
    name: str
    description: str
    editable: bool


@dataclass(frozen=True)
class EnumValue:
    value: str
    label: str | None
    enabled: bool
    is_default: bool
    sort_order: int


class EnumService:
    def __init__(
        self,
        *,
        db_provider: Callable[[], aiosqlite.Connection],
        repo: EnumValuesRepo,
        registry: dict[str, EnumSpec] | None = None,
    ) -> None:
        self._db = db_provider
        self._repo = repo
        self._registry = registry if registry is not None else ENUM_REGISTRY

    # ---- definitions ----
    async def definitions(self, *, editable_only: bool = False) -> list[EnumDefinition]:
        return [
            EnumDefinition(s.key, s.name, s.description, s.editable)
            for s in self._registry.values()
            if (s.editable or not editable_only)
        ]

    def _spec(self, key: str) -> EnumSpec:
        spec = self._registry.get(key)
        if spec is None:
            raise EnumError(f"unknown enum {key!r}")
        return spec

    # ---- values ----
    async def values(self, key: str, *, enabled_only: bool = False) -> list[EnumValue]:
        spec = self._spec(key)
        if not spec.editable:
            return [
                EnumValue(v.value, v.label, True, bool(v.default), i)
                for i, v in enumerate(spec.values)
            ]
        rows = await self._repo.live_values(self._db(), key)
        if not rows:  # total fallback: never empty
            return self._seed_values(spec)
        out = [
            EnumValue(r.value, r.label, bool(r.enabled), bool(r.is_default), r.sort_order)
            for r in rows
        ]
        if enabled_only:
            out = [v for v in out if v.enabled]
        return out

    def _seed_values(self, spec: EnumSpec) -> list[EnumValue]:
        return [
            EnumValue(v.value, v.label, True, bool(v.default), i)
            for i, v in enumerate(spec.values)
        ]

    # ---- generation-model convenience ----
    async def generation_models(self, *, enabled_only: bool = True) -> list[EnumValue]:
        return await self.values(GENERATION_MODEL_KEY, enabled_only=enabled_only)

    async def generation_default(self) -> str:
        vals = await self.values(GENERATION_MODEL_KEY)
        for v in vals:
            if v.is_default and v.enabled:
                return v.value
        for v in vals:
            if v.enabled:
                return v.value
        # ultimate fallback: registry seed default
        spec = self._spec(GENERATION_MODEL_KEY)
        for v in spec.values:
            if v.default:
                return v.value
        return spec.values[0].value

    # ---- reconcile ----
    async def reconcile_seeds(self) -> None:
        """Idempotent boot-time sync of code seeds into the DB. Adds any new
        seed value absent from the table; never clobbers edits or revives a
        tombstone (INSERT OR IGNORE in the repo)."""
        conn = self._db()
        for spec in self._registry.values():
            if not spec.editable:
                continue
            for i, v in enumerate(spec.values):
                await self._repo.upsert_seed(conn, spec.key, v, sort_order=i, commit=False)
        await conn.commit()

    # ---- writes (implemented in Task 4) ----
    async def add_value(self, key: str, value: str, *, label: str | None = None) -> None:
        raise NotImplementedError

    async def set_enabled(self, key: str, value: str, *, enabled: bool) -> None:
        raise NotImplementedError

    async def set_default(self, key: str, value: str) -> None:
        raise NotImplementedError

    async def remove_value(self, key: str, value: str) -> None:
        raise NotImplementedError
