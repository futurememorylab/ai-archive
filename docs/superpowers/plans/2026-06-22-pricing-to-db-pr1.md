# PR1: Per-model pricing → DB (model_config) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded `RATE_CARDS` dict in `services/pricing.py` with an admin-editable `model_config` DB table (per-model rates + a `default_media_resolution` column reserved for PR2), seeded from the current rates at boot, with a new Admin "Models" tab — without changing any run behaviour or any cost number.

**Architecture:** A new `model_config` table is seeded at boot from `SEED_RATE_CARDS` (the renamed code dict) via a `PricingService` that mirrors `EnumService` (idempotent `reconcile_seeds`, soft-delete-aware). The service loads the DB rows into the existing module-level rate cache (`pricing.set_rate_cards`), so `compute_cost`'s signature is unchanged and every existing call site keeps working. The Admin "Models" tab edits rate rows and reloads the cache. Snapshot-at-write is preserved: editing bumps `pricing_version`, past `run_telemetry.cost_usd` is untouched.

**Tech Stack:** Python 3.12/3.13, FastAPI, aiosqlite, Jinja2 + HTMX + Alpine, pytest + pytest-asyncio.

**Spec:** `docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md` (§1, §6; "Implementation order" PR1).

**Conventions (read once):**
- Repos are **async**, stateless, receive `conn: aiosqlite.Connection` per call (see `backend/app/repositories/enum_values.py`). No stored connection.
- Run tests with the project venv: `.venv/bin/python -m pytest <path> -v`. (If `.venv` is absent, create with Python 3.12/3.13 — **not 3.14**, which is broken here.)
- New migration number is the next free integer after the highest existing `backend/migrations/NNNN_*.sql`. This plan assumes **0024**; if a higher number exists, use the next free one and update the filename in every step below.
- The `db` test fixture lives in `tests/unit/conftest.py` and runs all migrations on a tmp DB.

---

## File Structure

- **Create** `backend/migrations/0024_model_config.sql` — the table.
- **Create** `backend/app/repositories/model_config.py` — `ModelConfigRepo` (leaf, async).
- **Create** `backend/app/services/pricing_service.py` — `PricingService` (reconcile + cache load + edit).
- **Modify** `backend/app/services/pricing.py` — rename `RATE_CARDS` → `SEED_RATE_CARDS`; add a mutable `_ACTIVE_CARDS` cache + `rate_cards()` / `set_rate_cards()`; `compute_cost` reads the cache.
- **Modify** `backend/app/services/run_estimator.py` — `RATE_CARDS` → `rate_cards()`.
- **Modify** `backend/app/context.py` — add `model_config_repo` + `pricing_service` to `CoreCtx`, reconcile + reload at boot.
- **Modify** `backend/app/routes/pages/admin.py` — add the "Models" tab routes; `no_rate_card` now checks the cache.
- **Modify** `backend/app/templates/pages/admin.html` — add the Models tab link.
- **Create** `backend/app/templates/pages/_admin_models_table.html` — the Models tab body.
- **Create** tests under `tests/unit/`.

---

## Task 1: `model_config` table migration

**Files:**
- Create: `backend/migrations/0024_model_config.sql`
- Test: `tests/unit/test_model_config_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_model_config_migration.py
"""0024 creates model_config with the expected columns."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_model_config_table_exists_with_columns(db):
    cur = await db.execute("PRAGMA table_info(model_config)")
    cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "model",
        "input_text_video_image_per_1m",
        "input_audio_per_1m",
        "input_cached_per_1m",
        "output_per_1m",
        "source_url",
        "default_media_resolution",
        "pricing_version",
        "updated_at",
        "removed",
        "created_at",
    }


async def test_model_config_primary_key_is_model(db):
    cur = await db.execute("PRAGMA table_info(model_config)")
    pk_cols = [row[1] for row in await cur.fetchall() if row[5] == 1]
    assert pk_cols == ["model"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_model_config_migration.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: model_config`.

- [ ] **Step 3: Write the migration**

```sql
-- backend/migrations/0024_model_config.sql
-- 0024: per-model pricing + default media resolution. Replaces the hardcoded
-- RATE_CARDS dict in services/pricing.py. A row is materialised either by
-- boot-time reconcile (the SEED_RATE_CARDS seed) or by an admin edit. `removed`
-- is a soft delete so reconcile won't re-add a model the admin deleted. Editing
-- bumps pricing_version (snapshot-at-write: past run_telemetry.cost_usd is never
-- rewritten). default_media_resolution is reserved for PR2 (resolution wiring).
-- See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md.
CREATE TABLE model_config (
  model                          TEXT    NOT NULL PRIMARY KEY,
  input_text_video_image_per_1m  REAL    NOT NULL,
  input_audio_per_1m             REAL    NOT NULL,
  input_cached_per_1m            REAL    NOT NULL,
  output_per_1m                  REAL    NOT NULL,
  source_url                     TEXT    NOT NULL DEFAULT '',
  default_media_resolution       TEXT    NOT NULL DEFAULT 'medium',
  pricing_version                TEXT    NOT NULL,
  updated_at                     TEXT    NOT NULL,
  removed                        INTEGER NOT NULL DEFAULT 0,
  created_at                     TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_model_config_migration.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0024_model_config.sql tests/unit/test_model_config_migration.py
git commit -m "feat(pricing): add model_config table (0024)"
```

---

## Task 2: `ModelConfigRepo`

**Files:**
- Create: `backend/app/repositories/model_config.py`
- Test: `tests/unit/test_model_config_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_model_config_repo.py
"""ModelConfigRepo: seed-insert is idempotent; edits bump version; soft delete."""

import pytest

from backend.app.repositories.model_config import ModelConfigRepo, ModelConfigRow

pytestmark = pytest.mark.asyncio


def _card(model="m1"):
    return ModelConfigRow(
        model=model,
        input_text_video_image_per_1m=0.10,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        source_url="https://example.test",
        default_media_resolution="medium",
        pricing_version="2026-06",
        updated_at="2026-06-22T00:00:00+00:00",
        removed=0,
        created_at="2026-06-22T00:00:00+00:00",
    )


async def test_upsert_seed_inserts_then_is_idempotent(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card(), commit=True)
    # Second seed with different rates must NOT overwrite the first.
    changed = _card()
    object.__setattr__(changed, "output_per_1m", 99.0)  # frozen dataclass
    await repo.upsert_seed(db, changed, commit=True)
    row = await repo.get(db, "m1")
    assert row is not None
    assert row.output_per_1m == 0.40  # unchanged


async def test_all_live_excludes_removed(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card("keep"), commit=True)
    await repo.upsert_seed(db, _card("gone"), commit=True)
    await repo.soft_delete(db, "gone", commit=True)
    models = {r.model for r in await repo.all_live(db)}
    assert models == {"keep"}


async def test_update_rates_bumps_version(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card("m1"), commit=True)
    await repo.update_rates(
        db,
        "m1",
        input_text_video_image_per_1m=0.20,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        pricing_version="edit-2026-06-22T10:00:00Z",
        commit=True,
    )
    row = await repo.get(db, "m1")
    assert row.input_text_video_image_per_1m == 0.20
    assert row.pricing_version == "edit-2026-06-22T10:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_model_config_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app.repositories.model_config`.

- [ ] **Step 3: Write the repo**

```python
# backend/app/repositories/model_config.py
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

    async def soft_delete(
        self, conn: aiosqlite.Connection, model: str, *, commit: bool
    ) -> None:
        await conn.execute(
            "UPDATE model_config SET removed = 1 WHERE model = ?", (model,)
        )
        if commit:
            await conn.commit()
```

> Note: the test mutates a frozen-looking row via `object.__setattr__`; `ModelConfigRow` is a plain (non-frozen) dataclass, so a normal assignment works too — either is fine.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_model_config_repo.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/model_config.py tests/unit/test_model_config_repo.py
git commit -m "feat(pricing): add ModelConfigRepo"
```

---

## Task 3: Rename `RATE_CARDS` → `SEED_RATE_CARDS` + add the active cache

This keeps `compute_cost`'s signature and math identical, but its default lookup now reads a mutable cache (`_ACTIVE_CARDS`) that boot will populate from the DB. The seed dict stays as the reconcile source and the offline fallback.

**Files:**
- Modify: `backend/app/services/pricing.py`
- Modify: `backend/app/services/run_estimator.py` (import + usage)
- Modify: `backend/app/routes/pages/admin.py` (import only; full tab work in Task 7)
- Test: `tests/unit/test_pricing_cache.py`; update `tests/unit/test_pricing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pricing_cache.py
"""The active rate-card cache: defaults to the seed, swappable, used by compute_cost."""

import pytest

from backend.app.services import pricing
from backend.app.services.pricing import RateCard, compute_cost, rate_cards, set_rate_cards
from backend.app.services.telemetry_capture import TokenUsage


@pytest.fixture(autouse=True)
def _reset_cards():
    # Tests mutate a process-global cache; restore the seed afterwards.
    yield
    set_rate_cards(pricing.SEED_RATE_CARDS)


def test_cache_defaults_to_seed():
    assert "gemini-2.5-flash-lite" in rate_cards()


def test_set_rate_cards_replaces_active_lookup():
    set_rate_cards(
        {
            "only-model": RateCard(
                input_text_video_image_per_1m=1.0,
                input_audio_per_1m=1.0,
                input_cached_per_1m=1.0,
                output_per_1m=1.0,
                source_url="x",
            )
        }
    )
    assert "gemini-2.5-flash-lite" not in rate_cards()
    cost, _ = compute_cost(TokenUsage(tokens_in=1_000_000), "only-model")
    assert cost == pytest.approx(1.0)
    # A model no longer in the cache prices to None.
    none_cost, _ = compute_cost(TokenUsage(tokens_in=1000), "gemini-2.5-flash-lite")
    assert none_cost is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_pricing_cache.py -v`
Expected: FAIL — `ImportError: cannot import name 'rate_cards'`.

- [ ] **Step 3: Edit `pricing.py`**

Rename the dict and add the cache + accessors. Replace the `RATE_CARDS: dict[str, RateCard] = { ... }` block's **name** and add the cache below it; change `compute_cost`'s default lookup.

Change the dict declaration line from:

```python
RATE_CARDS: dict[str, RateCard] = {
```

to:

```python
# Seed values — the offline fallback and the source the PricingService
# reconciles into the model_config table at boot. The live lookup uses
# _ACTIVE_CARDS (populated from the DB); see set_rate_cards/rate_cards.
SEED_RATE_CARDS: dict[str, RateCard] = {
```

Immediately **after** the closing `}` of that dict, add:

```python
# Process-wide active cache. Defaults to the seed so imports work before the
# DB is wired (and in pure-unit tests); PricingService.reload() swaps in the
# DB rows at boot and after every admin edit.
_ACTIVE_CARDS: dict[str, RateCard] = dict(SEED_RATE_CARDS)


def rate_cards() -> dict[str, RateCard]:
    """The active per-model rate cards (DB-backed once the app has booted)."""
    return _ACTIVE_CARDS


def set_rate_cards(cards: dict[str, RateCard]) -> None:
    """Replace the active cache (called by PricingService after load/edit)."""
    _ACTIVE_CARDS.clear()
    _ACTIVE_CARDS.update(cards)
```

In `compute_cost`, change:

```python
    if card is None:
        card = RATE_CARDS.get(model)
```

to:

```python
    if card is None:
        card = _ACTIVE_CARDS.get(model)
```

- [ ] **Step 4: Update `run_estimator.py`**

Change the import:

```python
from backend.app.services.pricing import RATE_CARDS, compute_cost
```

to:

```python
from backend.app.services.pricing import compute_cost, rate_cards
```

And change the membership check inside the `_cost` closure from:

```python
    if model not in RATE_CARDS:
        return None
```

to:

```python
    if model not in rate_cards():
        return None
```

- [ ] **Step 5: Update the stale import in `admin.py`**

Change:

```python
from backend.app.services.pricing import RATE_CARDS
```

to:

```python
from backend.app.services.pricing import rate_cards
```

And update the one usage in `_enum_view` from:

```python
            "no_rate_card": is_model_enum and v.value not in RATE_CARDS,
```

to:

```python
            "no_rate_card": is_model_enum and v.value not in rate_cards(),
```

- [ ] **Step 6: Fix the existing pricing test import**

In `tests/unit/test_pricing.py`, the default-model test relies on the seed being active. It imports from `pricing` but does not reference `RATE_CARDS` by name (it calls `compute_cost(... "gemini-2.5-flash-lite")`), so no import change is needed — but add an autouse reset so other modules that swapped the cache don't bleed in:

Add near the top of `tests/unit/test_pricing.py` (after imports):

```python
@pytest.fixture(autouse=True)
def _reset_cards():
    from backend.app.services import pricing

    yield
    pricing.set_rate_cards(pricing.SEED_RATE_CARDS)
```

- [ ] **Step 7: Run the impacted tests**

Run: `.venv/bin/python -m pytest tests/unit/test_pricing.py tests/unit/test_pricing_cache.py -v`
Expected: PASS (all). Then `grep -rn "RATE_CARDS" backend/` should show **only** `SEED_RATE_CARDS` (no bare `RATE_CARDS` references remain).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/pricing.py backend/app/services/run_estimator.py \
        backend/app/routes/pages/admin.py tests/unit/test_pricing.py \
        tests/unit/test_pricing_cache.py
git commit -m "refactor(pricing): rename RATE_CARDS to SEED_RATE_CARDS + active cache"
```

---

## Task 4: `PricingService` — reconcile + load from DB

**Files:**
- Create: `backend/app/services/pricing_service.py`
- Test: `tests/unit/test_pricing_service.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_pricing_service.py
"""PricingService seeds model_config from SEED_RATE_CARDS and loads it into cache."""

import pytest

from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.services import pricing
from backend.app.services.pricing_service import PricingService

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_cards():
    yield
    pricing.set_rate_cards(pricing.SEED_RATE_CARDS)


def _service(db):
    return PricingService(db_provider=lambda: db, repo=ModelConfigRepo())


async def test_reconcile_seeds_all_seed_models(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    rows = await ModelConfigRepo().all_live(db)
    assert {r.model for r in rows} == set(pricing.SEED_RATE_CARDS)


async def test_reload_populates_active_cache_from_db(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    # Drop the cache to prove reload repopulates it from the DB.
    pricing.set_rate_cards({})
    await svc.reload()
    cards = pricing.rate_cards()
    assert "gemini-2.5-flash-lite" in cards
    seed = pricing.SEED_RATE_CARDS["gemini-2.5-flash-lite"]
    assert cards["gemini-2.5-flash-lite"].output_per_1m == seed.output_per_1m


async def test_reconcile_does_not_clobber_edits(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    await ModelConfigRepo().update_rates(
        db,
        "gemini-2.5-flash-lite",
        input_text_video_image_per_1m=9.99,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.01,
        output_per_1m=0.40,
        pricing_version="edit-x",
        commit=True,
    )
    await svc.reconcile_seeds()  # second boot
    row = await ModelConfigRepo().get(db, "gemini-2.5-flash-lite")
    assert row.input_text_video_image_per_1m == 9.99  # edit survived
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_pricing_service.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app.services.pricing_service`.

- [ ] **Step 3: Write the service**

```python
# backend/app/services/pricing_service.py
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

    async def rows(self) -> list[ModelConfigRow]:
        return await self._repo.all_live(self._db())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_pricing_service.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pricing_service.py tests/unit/test_pricing_service.py
git commit -m "feat(pricing): add PricingService (reconcile + cache load)"
```

---

## Task 5: Wire `PricingService` into `CoreCtx`

**Files:**
- Modify: `backend/app/context.py`
- Test: `tests/integration/test_pricing_boot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_pricing_boot.py
"""After CoreCtx.build, model_config is seeded and the cache is DB-backed."""

import pytest

from backend.app.context import CoreCtx
from backend.app.services import pricing
from backend.app.settings import Settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_cards():
    yield
    pricing.set_rate_cards(pricing.SEED_RATE_CARDS)


async def test_build_seeds_and_loads_pricing(tmp_path):
    settings = Settings(data_dir=tmp_path)
    ctx = await CoreCtx.build(settings)
    try:
        rows = await ctx.pricing_service.rows()
        assert {r.model for r in rows} == set(pricing.SEED_RATE_CARDS)
        # The active cache was reloaded from the DB during build.
        assert "gemini-2.5-flash-lite" in pricing.rate_cards()
    finally:
        await ctx.db_cm.__aexit__(None, None, None)
```

> If `Settings(data_dir=...)` is not accepted directly, mirror however the existing integration tests under `tests/integration/` construct `Settings`/`CoreCtx` (grep `CoreCtx.build` in tests). The assertions stay the same.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_pricing_boot.py -v`
Expected: FAIL — `AttributeError: 'CoreCtx' object has no attribute 'pricing_service'`.

- [ ] **Step 3: Add the repo + service fields and boot wiring**

In `backend/app/context.py`, add the imports near the other repo/service imports:

```python
from backend.app.repositories.model_config import ModelConfigRepo
from backend.app.services.pricing_service import PricingService
```

In the `CoreCtx` dataclass, add the repo field next to `enum_values_repo`:

```python
    model_config_repo: ModelConfigRepo = field(default_factory=ModelConfigRepo)
```

And add the service to the `field(init=False)` block next to `enum_service`:

```python
    pricing_service: PricingService = field(init=False)
```

In `CoreCtx.build`, immediately **after** the existing `enum_service` block:

```python
        await ctx.enum_service.reconcile_seeds()
```

add:

```python
        ctx.pricing_service = PricingService(
            db_provider=lambda: ctx.db,
            repo=ctx.model_config_repo,
        )
        await ctx.pricing_service.reconcile_seeds()
        await ctx.pricing_service.reload()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_pricing_boot.py -v`
Expected: PASS.

- [ ] **Step 5: Run the context-delegation guard (CoreCtx ⊆ LiveCtx drift test)**

Run: `.venv/bin/python -m pytest tests/unit/test_context_delegation.py -v`
Expected: PASS. (PricingService is DB-only, so it belongs on CoreCtx; if this guard complains, re-read its assertion — do **not** move pricing to LiveCtx.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py tests/integration/test_pricing_boot.py
git commit -m "feat(pricing): seed + load model_config at CoreCtx boot"
```

---

## Task 6: Seed-drift guard (no silent pricing change on migration)

Pins the seeded `model_config` to exactly the old `RATE_CARDS` numbers, so a future careless edit to `SEED_RATE_CARDS` can't silently change historical pricing assumptions without a deliberate test update.

**Files:**
- Test: `tests/unit/test_pricing_seed_guard.py`

- [ ] **Step 1: Write the test**

```python
# tests/unit/test_pricing_seed_guard.py
"""Guard: the seeded rate cards match the values shipped at PR1 time.

If you intentionally change a rate, update BOTH SEED_RATE_CARDS and this
pin in the same commit (and bump PRICING_VERSION). The pin is the
deliberate-change checkpoint — see the cost-prediction spec §6.
"""

from backend.app.services.pricing import SEED_RATE_CARDS

EXPECTED = {
    "gemini-2.5-flash-lite": (0.10, 0.30, 0.01, 0.40),
    "gemini-2.5-flash": (0.30, 1.00, 0.03, 2.50),
    "gemini-2.5-pro": (1.25, 1.25, 0.13, 10.00),
}


def test_seed_rate_cards_match_pin():
    actual = {
        m: (
            c.input_text_video_image_per_1m,
            c.input_audio_per_1m,
            c.input_cached_per_1m,
            c.output_per_1m,
        )
        for m, c in SEED_RATE_CARDS.items()
    }
    assert actual == EXPECTED
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/unit/test_pricing_seed_guard.py -v`
Expected: PASS. (If it fails, the seed dict drifted — reconcile the numbers and `EXPECTED` deliberately.)

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_pricing_seed_guard.py
git commit -m "test(pricing): pin seed rate cards (drift guard)"
```

---

## Task 7: Admin "Models" tab

A dedicated tab (not the generic enum table — a rate card is a structured record). It lists the live `model_config` rows with editable rate fields; saving calls `PricingService.edit_rates`, which bumps `pricing_version` and reloads the cache. The table re-renders via HTMX into the shared `#admin-enum-region`.

**Files:**
- Modify: `backend/app/routes/pages/admin.py`
- Create: `backend/app/templates/pages/_admin_models_table.html`
- Modify: `backend/app/templates/pages/admin.html`
- Test: `tests/integration/test_admin_models_tab.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_admin_models_tab.py
"""The Models tab renders rows and edits persist + reload the cache."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_models_tab_lists_seeded_models(admin_client):
    resp = await admin_client.get("/admin/models")
    assert resp.status_code == 200
    assert "gemini-2.5-flash-lite" in resp.text


async def test_edit_rates_persists_and_updates_cache(admin_client):
    resp = await admin_client.post(
        "/admin/models/gemini-2.5-flash-lite/rates",
        data={
            "input_text_video_image_per_1m": "0.20",
            "input_audio_per_1m": "0.30",
            "input_cached_per_1m": "0.01",
            "output_per_1m": "0.40",
        },
    )
    assert resp.status_code == 200
    from backend.app.services.pricing import rate_cards

    assert rate_cards()["gemini-2.5-flash-lite"].input_text_video_image_per_1m == 0.20
```

> Reuse the existing admin-authenticated test client. Grep `tests/integration/` for how `/admin` routes are exercised (an `admin_client` / role-seeded `AsyncClient` fixture already exists for the enum-tab tests); use that same fixture name here. If the project resets the pricing cache between tests, keep the autouse `_reset_cards` fixture pattern from Task 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_admin_models_tab.py -v`
Expected: FAIL — 404 on `/admin/models`.

- [ ] **Step 3: Add the routes**

In `backend/app/routes/pages/admin.py`, add a view helper and two routes (place after `_enum_view`):

```python
async def _models_view(ctx) -> dict:
    rows = await ctx.pricing_service.rows()
    return {
        "rows": [
            {
                "model": r.model,
                "input_text_video_image_per_1m": r.input_text_video_image_per_1m,
                "input_audio_per_1m": r.input_audio_per_1m,
                "input_cached_per_1m": r.input_cached_per_1m,
                "output_per_1m": r.output_per_1m,
                "default_media_resolution": r.default_media_resolution,
                "pricing_version": r.pricing_version,
            }
            for r in rows
        ]
    }


@router.get("/admin/models", response_class=HTMLResponse)
async def admin_models_table(request: Request):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    return templates.TemplateResponse(
        request, "pages/_admin_models_table.html", await _models_view(ctx)
    )


@router.post("/admin/models/{model}/rates", response_class=HTMLResponse)
async def admin_edit_model_rates(
    request: Request,
    model: str,
    input_text_video_image_per_1m: float = Form(...),
    input_audio_per_1m: float = Form(...),
    input_cached_per_1m: float = Form(...),
    output_per_1m: float = Form(...),
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    await ctx.pricing_service.edit_rates(
        model,
        input_text_video_image_per_1m=input_text_video_image_per_1m,
        input_audio_per_1m=input_audio_per_1m,
        input_cached_per_1m=input_cached_per_1m,
        output_per_1m=output_per_1m,
    )
    return templates.TemplateResponse(
        request, "pages/_admin_models_table.html", await _models_view(ctx)
    )
```

- [ ] **Step 4: Create the Models tab template**

```html
{# backend/app/templates/pages/_admin_models_table.html #}
{% import "components/_ui.html" as ui %}
<div class="admin-models" data-key="models">
  <p class="meta">Per-model Gemini rates (USD per 1M tokens). Editing bumps the
    pricing version; historical run costs are not changed.</p>
  <table class="admin-table">
    <thead>
      <tr>
        <th>Model</th><th>Text/Video/Image</th><th>Audio</th>
        <th>Cached</th><th>Output</th><th>Default res</th><th></th>
      </tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <form hx-post="/admin/models/{{ r.model }}/rates"
              hx-target="#admin-enum-region" hx-swap="innerHTML">
          <td class="mono-cell">{{ r.model }}</td>
          <td><input class="input sm" type="number" step="0.0001" min="0"
                     name="input_text_video_image_per_1m"
                     value="{{ r.input_text_video_image_per_1m }}"></td>
          <td><input class="input sm" type="number" step="0.0001" min="0"
                     name="input_audio_per_1m" value="{{ r.input_audio_per_1m }}"></td>
          <td><input class="input sm" type="number" step="0.0001" min="0"
                     name="input_cached_per_1m" value="{{ r.input_cached_per_1m }}"></td>
          <td><input class="input sm" type="number" step="0.0001" min="0"
                     name="output_per_1m" value="{{ r.output_per_1m }}"></td>
          <td class="mono-cell">{{ r.default_media_resolution }}</td>
          <td class="admin-actions">
            <button class="btn sm primary" type="submit">Save</button>
          </td>
        </form>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

> The `default_media_resolution` cell is read-only in PR1 (the dropdown + its use land in PR2). Use the existing `.input` / `.btn` classes from the design language — do not invent new ones (`tests/unit/test_design_language_guard.py` will fail otherwise). If `.input.sm` doesn't exist, use `{{ ui.field(...) }}` or the input class the enum table's add-row form uses.

- [ ] **Step 5: Add the tab link in `admin.html`**

In `backend/app/templates/pages/admin.html`, immediately after the Access tab `<a>` and **before** the `{% for d in definitions %}` loop, add:

```html
    <a class="ctab{% if active == 'models' %} active{% endif %}"
       href="/admin/models"
       hx-get="/admin/models"
       hx-target="#admin-enum-region"
       hx-swap="innerHTML"
       hx-push-url="false">Models</a>
```

- [ ] **Step 6: Run the test**

Run: `.venv/bin/python -m pytest tests/integration/test_admin_models_tab.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/pages/admin.py \
        backend/app/templates/pages/_admin_models_table.html \
        backend/app/templates/pages/admin.html \
        tests/integration/test_admin_models_tab.py
git commit -m "feat(admin): Models tab for editing per-model rates"
```

---

## Task 8: Full regression + walkthrough

**Files:**
- Modify (if needed): a walkthrough scenario under `tests/walkthrough/`

- [ ] **Step 1: Run the design-language + import-linter guards**

Run: `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py -v && lint-imports`
Expected: PASS / contracts kept. (Fix any `.btn`/`.input` or new-Jinja-env violations the guards flag.)

- [ ] **Step 2: Run the full unit + integration suites**

Run: `.venv/bin/python -m pytest tests/unit tests/integration -q`
Expected: PASS. Pay attention to any test that imported `RATE_CARDS` directly — update it to `SEED_RATE_CARDS` or `rate_cards()`.

- [ ] **Step 3: Update the admin walkthrough scenario**

Per CLAUDE.md, a UI change needs its walkthrough updated. Add a step to the existing admin scenario (or create one) that opens the **Models** tab and asserts a model row + Save control is visible. Run assert mode:

Run: `.venv/bin/python -m tests.walkthrough.run --assert`
Expected: PASS. (Use the `/e2e` skill if you need to scaffold/update a scenario; add `data-test` hooks if the assertion needs them.)

- [ ] **Step 4: Commit**

```bash
git add tests/walkthrough
git commit -m "test(e2e): cover admin Models tab"
```

---

## Self-Review (completed by plan author)

- **Spec coverage (PR1 slice of §1/§6):** table created (T1), repo (T2), pricing reads DB via cache (T3–T5), seeded from RATE_CARDS at boot (T4–T5), Admin "Models" tab (T7), seed-drift guard (T6), snapshot-at-write preserved — `edit_rates` bumps `pricing_version` and never rewrites `run_telemetry` (T4/T7). `default_media_resolution` column exists but is intentionally inert until PR2 (noted in T1, T4, T7).
- **Out of scope (PR2–4, by design):** sending `media_resolution` to Gemini, resolution-aware estimation, calibration, real-cost button. Not in this plan.
- **Type consistency:** `ModelConfigRow` fields are referenced identically across T2/T4/T7; `RateCard(... source_url=...)` matches the existing dataclass; `compute_cost` signature unchanged; `rate_cards()` / `set_rate_cards()` / `SEED_RATE_CARDS` used consistently T3→T7.
- **Placeholder scan:** none — every code/step is concrete. Two soft spots are flagged with explicit fallback instructions (the `Settings`/`CoreCtx.build` construction in T5, and the `admin_client` fixture + `.input` class in T7) rather than left vague.
- **Risk:** the module-level `_ACTIVE_CARDS` cache is process-wide; tests that swap it must restore the seed (autouse `_reset_cards` fixture, supplied in T3/T4/T5).
