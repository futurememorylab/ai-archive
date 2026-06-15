# Centralised Enumeration + Admin Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Centralise enumerations behind a code registry + `EnumService`, make the Gemini generation-model list runtime-editable through a new Admin console, and serve fixed enums to the frontend from one source.

**Architecture:** A code registry (`backend/app/enums/registry.py`) is the canonical declaration of every centralised enum. Fixed enums (e.g. `toast_level`) are served straight from code; the one editable enum (the model catalog) stores user edits in a single DB table (`enum_values`), reconciled against the code seed at boot with soft-delete tombstones. `EnumService` (on `CoreCtx`, offline-safe) is the single read/write API; an Admin console (`/admin`, bottom rail icon) edits the catalog via HTMX partials.

**Tech Stack:** Python 3.12/3.13, FastAPI, aiosqlite, Jinja2, HTMX, Alpine.js. Tests with pytest (`pytest-asyncio`). Run from repo root; the venv is already active in the dev shell.

**Conventions to honour (from CLAUDE.md):**
- TDD: failing test → implement → green → commit, every task.
- Repos are leaves (no service imports); services route DB through a `db_provider` lambda like `CacheInspector`.
- Frontend errors via `Alpine.store('toast')`; CRUD returns HTMX partials, never `location.reload()`.
- Use the `ui.*` macros + design tokens; no new `*-btn`/`modal-*` vocabulary.
- No sync FS in `async def`. Catch `Exception`, never `BaseException`.

**Migration number:** the latest migration is `0019_poster_cache.sql`, so this plan uses **`0020`**. If a higher number exists when you start, use the next free number and update references in Task 2.

---

## File Structure

**Create:**
- `backend/app/enums/__init__.py` — exports the registry symbols.
- `backend/app/enums/registry.py` — `EnumValueSpec`, `EnumSpec`, `ENUM_REGISTRY`.
- `backend/migrations/0020_enum_values.sql` — the `enum_values` table.
- `backend/app/repositories/enum_values.py` — `EnumValuesRepo` + `EnumValueRow`.
- `backend/app/services/enum_service.py` — `EnumService`, `EnumDefinition`, `EnumValue`.
- `backend/app/routes/pages/admin.py` — `/admin` console + CRUD routes.
- `backend/app/templates/pages/admin.html` — console shell.
- `backend/app/templates/pages/_admin_enum_table.html` — values-table partial.
- `backend/app/templates/icons/_admin.svg` — rail icon.
- Tests: `tests/unit/test_enum_registry.py`, `tests/unit/test_enum_values_repo.py`, `tests/unit/test_enum_service.py`, `tests/unit/test_enum_service_offline.py`, `tests/integration/test_admin_enums.py`, `tests/integration/test_prompt_new_models.py`, `tests/integration/test_enum_bootstrap.py`.

**Modify:**
- `backend/app/context.py` — add `enum_values_repo`, `enum_service`, reconcile at boot.
- `backend/app/routes/pages/__init__.py` — register the admin router.
- `backend/app/routes/pages/templates.py` — register the `app_enums_json` Jinja global.
- `backend/app/routes/pages/prompts.py` — dropdown + default from `EnumService`; `model_options` for edit form.
- `backend/app/templates/pages/_prompt_new.html` — loop catalog instead of literal list.
- `backend/app/templates/pages/_prompt_detail.html` — inject `MODELS` from context.
- `backend/app/static/promptEditor.js` — drop the hardcoded `MODELS`.
- `backend/app/static/toast.js` — read `window.APP_ENUMS.toast_level`.
- `backend/app/templates/pages/layout.html` — inject `window.APP_ENUMS`.
- `backend/app/templates/pages/_rail.html` — bottom-pinned Admin button.
- `backend/app/static/app.css` — pin last rail button to the bottom.
- `CLAUDE.md` — new "Enumerations" section.

---

## Task 1: Enum registry (code source of truth)

**Files:**
- Create: `backend/app/enums/__init__.py`, `backend/app/enums/registry.py`
- Test: `tests/unit/test_enum_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_enum_registry.py
from backend.app.enums.registry import ENUM_REGISTRY, EnumSpec


def test_generation_model_enum_is_editable_with_one_default():
    spec = ENUM_REGISTRY["gemini_generation_model"]
    assert isinstance(spec, EnumSpec)
    assert spec.editable is True
    defaults = [v for v in spec.values if v.default]
    assert len(defaults) == 1, "exactly one seeded default"
    assert defaults[0].value == "gemini-2.5-flash-lite"
    assert len(spec.values) == 8


def test_toast_level_enum_is_fixed():
    spec = ENUM_REGISTRY["toast_level"]
    assert spec.editable is False
    assert [v.value for v in spec.values] == ["info", "success", "error"]


def test_editable_enums_never_seed_two_defaults():
    for spec in ENUM_REGISTRY.values():
        if spec.editable:
            assert sum(1 for v in spec.values if v.default) <= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_enum_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: backend.app.enums.registry`

- [ ] **Step 3: Write the registry**

```python
# backend/app/enums/registry.py
"""Canonical declaration of every centralised enumeration.

Two kinds live here:
  * Fixed enums (editable=False) — code is the source of truth; values are
    served straight from this module (the DB is never consulted). They also
    keep their Literal type in models/ for static checking.
  * Editable enums (editable=True) — these `values` are the *seed*. The DB
    table `enum_values` stores the user's edits, reconciled against this seed
    at boot. See docs/superpowers/specs/2026-06-14-centralised-enumeration-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EnumValueSpec:
    value: str
    label: str | None = None
    default: bool = False  # editable enums: the seeded default pick
    metadata: dict | None = None  # forward-compat (region, rates, capabilities)


@dataclass(frozen=True)
class EnumSpec:
    key: str
    name: str
    description: str
    editable: bool
    values: tuple[EnumValueSpec, ...] = field(default_factory=tuple)


def _m(value: str, *, default: bool = False) -> EnumValueSpec:
    return EnumValueSpec(value=value, default=default)


ENUM_REGISTRY: dict[str, EnumSpec] = {
    "gemini_generation_model": EnumSpec(
        key="gemini_generation_model",
        name="Gemini generation models",
        description="Models offered when creating or editing a prompt.",
        editable=True,
        values=(
            _m("gemini-2.5-pro"),
            _m("gemini-2.5-flash"),
            _m("gemini-2.5-flash-lite", default=True),
            _m("gemini-3-flash-preview"),
            _m("gemini-3.1-pro-preview"),
            _m("gemini-3.1-flash-lite"),
            _m("gemini-3.1-flash-lite-preview"),
            _m("gemini-3.5-flash"),
        ),
    ),
    "toast_level": EnumSpec(
        key="toast_level",
        name="Toast levels",
        description="Severity levels for user-facing toast notifications.",
        editable=False,
        values=(
            EnumValueSpec("info"),
            EnumValueSpec("success"),
            EnumValueSpec("error"),
        ),
    ),
}
```

```python
# backend/app/enums/__init__.py
"""Centralised enumeration registry. See registry.py."""

from backend.app.enums.registry import (
    ENUM_REGISTRY,
    EnumSpec,
    EnumValueSpec,
)

__all__ = ["ENUM_REGISTRY", "EnumSpec", "EnumValueSpec"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_enum_registry.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/enums tests/unit/test_enum_registry.py
git commit -m "feat(#13): enum registry as canonical source of truth"
```

---

## Task 2: Migration + EnumValuesRepo

**Files:**
- Create: `backend/migrations/0020_enum_values.sql`, `backend/app/repositories/enum_values.py`
- Test: `tests/unit/test_enum_values_repo.py`

- [ ] **Step 1: Write the migration**

```sql
-- backend/migrations/0020_enum_values.sql
-- 0020: editable enumeration values. Holds ONLY user-editable enums' value
-- edits (the code registry in backend/app/enums/registry.py is canonical for
-- fixed enums and for editable-enum seeds). A row is materialised either by
-- boot-time reconcile (source='seed') or by an admin add (source='user').
-- remove is a soft delete (removed=1) so reconcile won't re-add a value the
-- user deleted. See ADR + spec 2026-06-14-centralised-enumeration-design.md.
CREATE TABLE enum_values (
  enum_key   TEXT    NOT NULL,
  value      TEXT    NOT NULL,
  label      TEXT,
  enabled    INTEGER NOT NULL DEFAULT 1,
  is_default INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  source     TEXT    NOT NULL DEFAULT 'user',
  removed    INTEGER NOT NULL DEFAULT 0,
  metadata   TEXT,
  created_at TEXT    NOT NULL,
  PRIMARY KEY (enum_key, value)
);
CREATE UNIQUE INDEX idx_enum_values_default
  ON enum_values(enum_key) WHERE is_default = 1 AND removed = 0;
CREATE INDEX idx_enum_values_key ON enum_values(enum_key, sort_order);
```

- [ ] **Step 2: Write the failing repo test**

```python
# tests/unit/test_enum_values_repo.py
from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.enum_values import EnumValuesRepo


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_add_list_live_and_soft_delete(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    await repo.add_value(db, "k", "a", label=None, commit=True)
    await repo.add_value(db, "k", "b", label="Bee", commit=True)
    live = await repo.live_values(db, "k")
    assert [r.value for r in live] == ["a", "b"]

    await repo.soft_delete(db, "k", "a", commit=True)
    live = await repo.live_values(db, "k")
    assert [r.value for r in live] == ["b"]
    # tombstone still present in all_rows
    allrows = await repo.all_rows(db, "k")
    assert {r.value: r.removed for r in allrows} == {"a": 1, "b": 0}


@pytest.mark.asyncio
async def test_add_duplicate_raises(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    await repo.add_value(db, "k", "a", label=None, commit=True)
    with pytest.raises(aiosqlite.IntegrityError):
        await repo.add_value(db, "k", "a", label=None, commit=True)


@pytest.mark.asyncio
async def test_upsert_seed_idempotent_and_no_resurrection(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    from backend.app.enums.registry import EnumValueSpec

    spec = EnumValueSpec("a", default=True)
    await repo.upsert_seed(db, "k", spec, sort_order=0, commit=True)
    await repo.upsert_seed(db, "k", spec, sort_order=0, commit=True)  # idempotent
    assert len(await repo.all_rows(db, "k")) == 1

    await repo.soft_delete(db, "k", "a", commit=True)
    await repo.upsert_seed(db, "k", spec, sort_order=0, commit=True)  # must NOT revive
    live = await repo.live_values(db, "k")
    assert live == []


@pytest.mark.asyncio
async def test_count_enabled(db: aiosqlite.Connection):
    repo = EnumValuesRepo()
    await repo.add_value(db, "k", "a", label=None, commit=True)
    await repo.add_value(db, "k", "b", label=None, commit=True)
    await repo.set_enabled(db, "k", "b", enabled=False, commit=True)
    assert await repo.count_enabled(db, "k") == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_enum_values_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: backend.app.repositories.enum_values`

- [ ] **Step 4: Write the repo**

```python
# backend/app/repositories/enum_values.py
"""Repository for editable enum value edits (table: enum_values).

Leaf layer — no service imports. Operates only on editable enum keys; the
service enforces editability. `remove` is a soft delete so the boot-time
reconcile never re-adds a value the user deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from backend.app.enums.registry import EnumValueSpec

_COLS = "enum_key, value, label, enabled, is_default, sort_order, source, removed, metadata, created_at"


@dataclass(frozen=True)
class EnumValueRow:
    enum_key: str
    value: str
    label: str | None
    enabled: int
    is_default: int
    sort_order: int
    source: str
    removed: int
    metadata: str | None
    created_at: str


def _row(r: tuple) -> EnumValueRow:
    return EnumValueRow(*r)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EnumValuesRepo:
    async def live_values(self, conn: aiosqlite.Connection, enum_key: str) -> list[EnumValueRow]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM enum_values "
            "WHERE enum_key = ? AND removed = 0 ORDER BY sort_order, value",
            (enum_key,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def all_rows(self, conn: aiosqlite.Connection, enum_key: str) -> list[EnumValueRow]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM enum_values WHERE enum_key = ? ORDER BY sort_order, value",
            (enum_key,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def get(self, conn: aiosqlite.Connection, enum_key: str, value: str) -> EnumValueRow | None:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM enum_values WHERE enum_key = ? AND value = ?",
            (enum_key, value),
        )
        r = await cur.fetchone()
        return _row(r) if r else None

    async def upsert_seed(
        self,
        conn: aiosqlite.Connection,
        enum_key: str,
        spec: EnumValueSpec,
        *,
        sort_order: int,
        commit: bool,
    ) -> None:
        """Insert a seed value only when absent. Never touches an existing row
        (so it neither clobbers user edits nor resurrects a tombstone)."""
        await conn.execute(
            "INSERT OR IGNORE INTO enum_values "
            f"({_COLS}) VALUES (?, ?, ?, 1, ?, ?, 'seed', 0, ?, ?)",
            (
                enum_key,
                spec.value,
                spec.label,
                1 if spec.default else 0,
                sort_order,
                None,
                _now(),
            ),
        )
        if commit:
            await conn.commit()

    async def add_value(
        self,
        conn: aiosqlite.Connection,
        enum_key: str,
        value: str,
        *,
        label: str | None,
        commit: bool,
    ) -> None:
        """Add a user value. If a tombstoned row exists, revive it instead of
        raising (re-adding a previously removed value should succeed)."""
        existing = await self.get(conn, enum_key, value)
        if existing is not None and existing.removed == 1:
            await conn.execute(
                "UPDATE enum_values SET removed = 0, enabled = 1, label = ? "
                "WHERE enum_key = ? AND value = ?",
                (label, enum_key, value),
            )
        else:
            next_sort = await self._next_sort(conn, enum_key)
            await conn.execute(
                "INSERT INTO enum_values "
                f"({_COLS}) VALUES (?, ?, ?, 1, 0, ?, 'user', 0, ?, ?)",
                (enum_key, value, label, next_sort, None, _now()),
            )
        if commit:
            await conn.commit()

    async def _next_sort(self, conn: aiosqlite.Connection, enum_key: str) -> int:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM enum_values WHERE enum_key = ?",
            (enum_key,),
        )
        (n,) = await cur.fetchone()
        return int(n)

    async def set_enabled(
        self, conn: aiosqlite.Connection, enum_key: str, value: str, *, enabled: bool, commit: bool
    ) -> None:
        await conn.execute(
            "UPDATE enum_values SET enabled = ? WHERE enum_key = ? AND value = ? AND removed = 0",
            (1 if enabled else 0, enum_key, value),
        )
        if commit:
            await conn.commit()

    async def set_default(
        self, conn: aiosqlite.Connection, enum_key: str, value: str, *, commit: bool
    ) -> None:
        """Clear the prior default and set this one — atomic pair."""
        await conn.execute(
            "UPDATE enum_values SET is_default = 0 WHERE enum_key = ? AND is_default = 1",
            (enum_key,),
        )
        await conn.execute(
            "UPDATE enum_values SET is_default = 1 WHERE enum_key = ? AND value = ? AND removed = 0",
            (enum_key, value),
        )
        if commit:
            await conn.commit()

    async def soft_delete(
        self, conn: aiosqlite.Connection, enum_key: str, value: str, *, commit: bool
    ) -> None:
        await conn.execute(
            "UPDATE enum_values SET removed = 1, is_default = 0 WHERE enum_key = ? AND value = ?",
            (enum_key, value),
        )
        if commit:
            await conn.commit()

    async def count_enabled(self, conn: aiosqlite.Connection, enum_key: str) -> int:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM enum_values WHERE enum_key = ? AND enabled = 1 AND removed = 0",
            (enum_key,),
        )
        (n,) = await cur.fetchone()
        return int(n)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_enum_values_repo.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0020_enum_values.sql backend/app/repositories/enum_values.py tests/unit/test_enum_values_repo.py
git commit -m "feat(#13): enum_values table + EnumValuesRepo with soft-delete tombstones"
```

---

## Task 3: EnumService — reads + reconcile

**Files:**
- Create: `backend/app/services/enum_service.py`
- Test: `tests/unit/test_enum_service.py` (read/reconcile portion)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_enum_service.py
from pathlib import Path

import aiosqlite
import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.enum_values import EnumValuesRepo
from backend.app.services.enum_service import EnumService


@pytest.fixture
async def db(tmp_path: Path):
    cm = open_db(tmp_path / "t.db")
    conn = await cm.__aenter__()
    await apply_migrations(conn, Path("backend/migrations"))
    yield conn
    await cm.__aexit__(None, None, None)


def _svc(db) -> EnumService:
    return EnumService(db_provider=lambda: db, repo=EnumValuesRepo())


@pytest.mark.asyncio
async def test_fixed_enum_served_from_registry_ignoring_db(db):
    svc = _svc(db)
    vals = await svc.values("toast_level")
    assert [v.value for v in vals] == ["info", "success", "error"]


@pytest.mark.asyncio
async def test_editable_empty_db_falls_back_to_registry_seed(db):
    svc = _svc(db)
    # No reconcile yet → DB empty → must fall back to seed, never empty.
    vals = await svc.generation_models()
    assert len(vals) == 8
    assert await svc.generation_default() == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_reconcile_materialises_seed_then_serves_from_db(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    rows = await EnumValuesRepo().all_rows(db, "gemini_generation_model")
    assert len(rows) == 8
    assert all(r.source == "seed" for r in rows)
    assert await svc.generation_default() == "gemini-2.5-flash-lite"


@pytest.mark.asyncio
async def test_reconcile_does_not_revive_tombstone_or_clobber(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    await svc.remove_value("gemini_generation_model", "gemini-3.5-flash")
    await svc.set_default("gemini_generation_model", "gemini-2.5-flash")
    await svc.reconcile_seeds()  # second boot
    live = {v.value for v in await svc.generation_models()}
    assert "gemini-3.5-flash" not in live  # tombstone honoured
    assert await svc.generation_default() == "gemini-2.5-flash"  # edit preserved


@pytest.mark.asyncio
async def test_definitions_editable_only(db):
    svc = _svc(db)
    keys = {d.key for d in await svc.definitions(editable_only=True)}
    assert keys == {"gemini_generation_model"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_enum_service.py -v`
Expected: FAIL with `ModuleNotFoundError: backend.app.services.enum_service`

- [ ] **Step 3: Write the service (reads + reconcile + write stubs filled in Task 4)**

```python
# backend/app/services/enum_service.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_enum_service.py -v`
Expected: PASS for the read/reconcile tests above. (Write tests are added in Task 4.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/enum_service.py tests/unit/test_enum_service.py
git commit -m "feat(#13): EnumService reads + boot-time reconcile"
```

---

## Task 4: EnumService — writes + guards

**Files:**
- Modify: `backend/app/services/enum_service.py`
- Test: `tests/unit/test_enum_service.py` (append)

- [ ] **Step 1: Append failing write tests**

```python
# tests/unit/test_enum_service.py  (append)
@pytest.mark.asyncio
async def test_add_and_remove_value(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    await svc.add_value("gemini_generation_model", "gemini-4.0-pro")
    assert "gemini-4.0-pro" in {v.value for v in await svc.generation_models()}
    await svc.remove_value("gemini_generation_model", "gemini-4.0-pro")
    assert "gemini-4.0-pro" not in {v.value for v in await svc.generation_models()}


@pytest.mark.asyncio
async def test_write_to_fixed_enum_refused(db):
    svc = _svc(db)
    with pytest.raises(EnumError):
        await svc.add_value("toast_level", "warning")


@pytest.mark.asyncio
async def test_cannot_disable_last_enabled(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    models = [v.value for v in await svc.generation_models()]
    # disable all but one
    for m in models[1:]:
        await svc.set_enabled("gemini_generation_model", m, enabled=False)
    with pytest.raises(EnumError):
        await svc.set_enabled("gemini_generation_model", models[0], enabled=False)


@pytest.mark.asyncio
async def test_set_default_clears_prior_and_requires_enabled(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    await svc.set_default("gemini_generation_model", "gemini-2.5-flash")
    assert await svc.generation_default() == "gemini-2.5-flash"
    # only one default remains
    defaults = [v for v in await svc.generation_models() if v.is_default]
    assert len(defaults) == 1
    # cannot set a disabled value as default
    await svc.set_enabled("gemini_generation_model", "gemini-2.5-pro", enabled=False)
    with pytest.raises(EnumError):
        await svc.set_default("gemini_generation_model", "gemini-2.5-pro")


@pytest.mark.asyncio
async def test_cannot_remove_or_disable_current_default(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    cur = await svc.generation_default()
    with pytest.raises(EnumError):
        await svc.remove_value("gemini_generation_model", cur)
    with pytest.raises(EnumError):
        await svc.set_enabled("gemini_generation_model", cur, enabled=False)


@pytest.mark.asyncio
async def test_duplicate_add_raises_enum_error(db):
    svc = _svc(db)
    await svc.reconcile_seeds()
    with pytest.raises(EnumError):
        await svc.add_value("gemini_generation_model", "gemini-2.5-pro")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/unit/test_enum_service.py -v -k "write or last_enabled or default or duplicate or fixed"`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Implement the writes (replace the four stubs)**

```python
# backend/app/services/enum_service.py  (replace the "writes" section)
    def _require_editable(self, key: str) -> EnumSpec:
        spec = self._spec(key)
        if not spec.editable:
            raise EnumError(f"enum {key!r} is not editable")
        return spec

    async def _ensure_materialised(self, key: str) -> None:
        """Editable writes operate on DB rows; if the table is empty (never
        reconciled) materialise the seed first so edits have rows to act on."""
        conn = self._db()
        if not await self._repo.all_rows(conn, key):
            spec = self._spec(key)
            for i, v in enumerate(spec.values):
                await self._repo.upsert_seed(conn, key, v, sort_order=i, commit=False)
            await conn.commit()

    async def add_value(self, key: str, value: str, *, label: str | None = None) -> None:
        self._require_editable(key)
        await self._ensure_materialised(key)
        conn = self._db()
        existing = await self._repo.get(conn, key, value)
        if existing is not None and existing.removed == 0:
            raise EnumError(f"{value!r} is already in the list")
        try:
            await self._repo.add_value(conn, key, value, label=label, commit=True)
        except aiosqlite.IntegrityError as exc:  # pragma: no cover - guarded above
            raise EnumError(f"{value!r} is already in the list") from exc

    async def set_enabled(self, key: str, value: str, *, enabled: bool) -> None:
        self._require_editable(key)
        await self._ensure_materialised(key)
        conn = self._db()
        if not enabled:
            row = await self._repo.get(conn, key, value)
            if row is not None and row.is_default:
                raise EnumError("set another value as default first")
            if await self._repo.count_enabled(conn, key) <= 1:
                raise EnumError("cannot disable the last enabled value")
        await self._repo.set_enabled(conn, key, value, enabled=enabled, commit=True)

    async def set_default(self, key: str, value: str) -> None:
        self._require_editable(key)
        await self._ensure_materialised(key)
        conn = self._db()
        row = await self._repo.get(conn, key, value)
        if row is None or row.removed == 1:
            raise EnumError(f"unknown value {value!r}")
        if not row.enabled:
            raise EnumError("enable the value before making it the default")
        await self._repo.set_default(conn, key, value, commit=True)

    async def remove_value(self, key: str, value: str) -> None:
        self._require_editable(key)
        await self._ensure_materialised(key)
        conn = self._db()
        row = await self._repo.get(conn, key, value)
        if row is None or row.removed == 1:
            raise EnumError(f"unknown value {value!r}")
        if row.is_default:
            raise EnumError("set another value as default first")
        if row.enabled and await self._repo.count_enabled(conn, key) <= 1:
            raise EnumError("cannot remove the last enabled value")
        await self._repo.soft_delete(conn, key, value, commit=True)
```

Also add the import at the top of the file if not present: `import aiosqlite` (already imported).

- [ ] **Step 4: Run all service tests**

Run: `pytest tests/unit/test_enum_service.py -v`
Expected: PASS (all read + write tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/enum_service.py tests/unit/test_enum_service.py
git commit -m "feat(#13): EnumService writes with editable/last-enabled/default guards"
```

---

## Task 5: Wire EnumService onto CoreCtx + LiveCtx + reconcile at boot

**Files:**
- Modify: `backend/app/context.py`
- Test: `tests/unit/test_enum_service_offline.py`

- [ ] **Step 1: Write the failing offline test**

```python
# tests/unit/test_enum_service_offline.py
import pytest

from backend.app.context import CoreCtx
from backend.app.settings import load_settings


@pytest.mark.asyncio
async def test_enum_service_on_core_ctx_works_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctx = await CoreCtx.build(load_settings())
    try:
        # reconcile ran during build → DB materialised, served offline
        models = await ctx.enum_service.generation_models()
        assert len(models) == 8
        assert await ctx.enum_service.generation_default() == "gemini-2.5-flash-lite"
    finally:
        await ctx.aclose()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/unit/test_enum_service_offline.py -v`
Expected: FAIL with `AttributeError: 'CoreCtx' object has no attribute 'enum_service'`

- [ ] **Step 3: Add the repo field + service to CoreCtx**

In `backend/app/context.py`, add the import near the other repo/service imports:

```python
from backend.app.repositories.enum_values import EnumValuesRepo
from backend.app.services.enum_service import EnumService
```

Add the repo field alongside the other repos (after `run_telemetry_repo`, line ~113):

```python
    run_telemetry_repo: RunTelemetryRepo = field(default_factory=RunTelemetryRepo)
    enum_values_repo: EnumValuesRepo = field(default_factory=EnumValuesRepo)
```

Add the service as an init=False field (near `cache_actions`, line ~125):

```python
    cache_actions: CacheActions = field(init=False)
    enum_service: EnumService = field(init=False)
```

In `build()`, after `ctx.telemetry_ctx = TelemetryCtx(...)` and before `return ctx` (line ~172), construct + reconcile:

```python
        ctx.enum_service = EnumService(
            db_provider=lambda: ctx.db,
            repo=ctx.enum_values_repo,
        )
        await ctx.enum_service.reconcile_seeds()
        return ctx
```

- [ ] **Step 4: Add LiveCtx delegators**

In the `LiveCtx` property-delegator block (near `def prompts_repo`, line ~252), add:

```python
    @property
    def enum_values_repo(self) -> EnumValuesRepo:
        return self.core.enum_values_repo

    @property
    def enum_service(self) -> EnumService:
        return self.core.enum_service
```

- [ ] **Step 5: Run the offline test + context drift guard**

Run: `pytest tests/unit/test_enum_service_offline.py tests/unit/test_context_delegation.py -v`
Expected: PASS (the drift guard requires every CoreCtx accessor be delegated by LiveCtx — the new delegators satisfy it).

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py tests/unit/test_enum_service_offline.py
git commit -m "feat(#13): wire EnumService onto CoreCtx/LiveCtx + reconcile at boot"
```

---

## Task 6: JSON API + `window.APP_ENUMS` bootstrap + toast.js

**Files:**
- Modify: `backend/app/routes/pages/templates.py`, `backend/app/templates/pages/layout.html`, `backend/app/static/toast.js`
- Create (route): add to `backend/app/routes/pages/admin.py` is later; the JSON API goes in a small dedicated router here.
- Test: `tests/integration/test_enum_bootstrap.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_enum_bootstrap.py
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_layout_injects_app_enums_with_toast_levels(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        html = client.get("/prompts").text
        assert "window.APP_ENUMS" in html
        assert '"toast_level"' in html
        assert "info" in html and "success" in html and "error" in html


def test_enums_api_serves_fixed_and_editable(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        fixed = client.get("/api/enums/toast_level").json()
        assert [v["value"] for v in fixed] == ["info", "success", "error"]
        editable = client.get("/api/enums/gemini_generation_model").json()
        assert any(v["value"] == "gemini-2.5-flash-lite" and v["default"] for v in editable)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/integration/test_enum_bootstrap.py -v`
Expected: FAIL (`window.APP_ENUMS` absent; `/api/enums/...` 404)

- [ ] **Step 3: Register the `app_enums_json` Jinja global**

In `backend/app/routes/pages/templates.py`, after the `templates` env is created, add:

```python
import json as _json

from backend.app.enums.registry import ENUM_REGISTRY


def _fixed_enums_json() -> str:
    """Static (DB-free) fixed-enum values for window.APP_ENUMS. Editable enums
    are intentionally excluded — they change at runtime and are delivered via
    route context / the JSON API."""
    data = {
        key: [v.value for v in spec.values]
        for key, spec in ENUM_REGISTRY.items()
        if not spec.editable
    }
    return _json.dumps(data)


templates.env.globals["app_enums_json"] = _fixed_enums_json()
```

- [ ] **Step 4: Inject into layout.html**

In `backend/app/templates/pages/layout.html`, immediately before the Alpine script tag (`<script defer src="/static/vendor/alpine.min.js"></script>`), add:

```html
  <script>window.APP_ENUMS = {{ app_enums_json | safe }};</script>
```

- [ ] **Step 5: Make toast.js read the level list**

In `backend/app/static/toast.js`, replace the `push` level line:

```javascript
    push(message, opts = {}) {
      const levels = (window.APP_ENUMS && window.APP_ENUMS.toast_level) || ['info', 'success', 'error'];
      let level = opts.level || 'info';
      if (!levels.includes(level)) level = 'info';
      const ttlMs = opts.ttlMs ?? (level === 'error' ? 8000 : 4000);
```

(Keep the rest of `push` unchanged.)

- [ ] **Step 6: Add the JSON API route**

Create `backend/app/routes/enums.py`:

```python
"""Read-only JSON API for centralised enumerations (frontend consumers)."""

from fastapi import APIRouter, HTTPException, Request

from backend.app.deps import get_core_ctx
from backend.app.services.enum_service import EnumError

router = APIRouter(tags=["enums"])


@router.get("/api/enums/{key}")
async def get_enum(request: Request, key: str) -> list[dict]:
    ctx = get_core_ctx(request)
    try:
        vals = await ctx.enum_service.values(key)
    except EnumError as exc:
        raise HTTPException(404, str(exc)) from exc
    return [
        {"value": v.value, "label": v.label, "enabled": v.enabled, "default": v.is_default}
        for v in vals
    ]
```

Register it in `backend/app/main.py` near the other routers (after the cache routers, before the `for r in page_routers` loop):

```python
from backend.app.routes.enums import router as enums_router
app.include_router(enums_router)
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/integration/test_enum_bootstrap.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/pages/templates.py backend/app/templates/pages/layout.html backend/app/static/toast.js backend/app/routes/enums.py backend/app/main.py tests/integration/test_enum_bootstrap.py
git commit -m "feat(#13): window.APP_ENUMS bootstrap + /api/enums + toast.js consumes registry"
```

---

## Task 7: Rewire prompt New + Edit + default from the catalog

**Files:**
- Modify: `backend/app/routes/pages/prompts.py`, `backend/app/templates/pages/_prompt_new.html`, `backend/app/templates/pages/_prompt_detail.html`, `backend/app/static/promptEditor.js`
- Test: `tests/integration/test_prompt_new_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_prompt_new_models.py
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_new_prompt_dropdown_reflects_catalog_and_default(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # add a runtime model + disable one, via the service through the app ctx
        import anyio
        from backend.app import main as main_mod

        async def _edit():
            svc = main_mod.app.state.core_ctx.enum_service
            await svc.add_value("gemini_generation_model", "gemini-4.0-pro")
            await svc.set_enabled("gemini_generation_model", "gemini-3.5-flash", enabled=False)

        anyio.from_thread.run  # noqa: B018 - documentation; we call sync below
        anyio_run = getattr(main_mod.app.state.core_ctx, "_test_run", None)
        # simplest: drive the async edit on the app loop via the test client's portal
        client.portal.call(_edit)  # type: ignore[attr-defined]

        html = client.get("/prompts/new").text
        assert "gemini-4.0-pro" in html  # runtime add visible
        assert "gemini-3.5-flash" not in html  # disabled excluded


def test_orphaned_model_still_shown_in_edit_form(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # create a prompt on a model, then remove that model from the catalog
        r = client.post(
            "/prompts/_create",
            data={
                "name": "orphan-test",
                "description": "",
                "body": "b",
                "target_map": "{}",
                "output_schema": "{}",
                "model": "gemini-3.1-pro-preview",
                "media_kind": "any",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303
        pid = r.headers["location"].rsplit("/", 1)[-1]

        async def _remove():
            svc = client.app.state.core_ctx.enum_service  # type: ignore[attr-defined]
            await svc.remove_value("gemini_generation_model", "gemini-3.1-pro-preview")

        client.portal.call(_remove)  # type: ignore[attr-defined]

        html = client.get(f"/prompts/{pid}").text
        assert "gemini-3.1-pro-preview" in html  # saved model still offered
```

> Note: `TestClient` exposes `client.portal` (an `anyio` blocking portal) to run
> coroutines on the app's loop. If your pytest/anyio version names it differently,
> fetch the ctx and run the edit via `asyncio` against `app.state.core_ctx` in a
> small helper fixture — the assertion targets (HTML contains/excludes a model)
> stay identical.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/integration/test_prompt_new_models.py -v`
Expected: FAIL (runtime add not visible — template still hardcodes the list)

- [ ] **Step 3: Update the New-prompt route to pass the catalog**

In `backend/app/routes/pages/prompts.py`, `prompt_new_page` — build the model list and default from the service. Replace the body:

```python
@router.get("/prompts/new", response_class=HTMLResponse)
async def prompt_new_page(request: Request):
    ctx = get_core_ctx(request)
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    models = [v.value for v in await ctx.enum_service.generation_models()]
    default_model = await ctx.enum_service.generation_default()
    return templates.TemplateResponse(
        request,
        "pages/_prompt_new.html",
        {
            "prompts": [p.model_dump() for p in prompts],
            "rail_active": "prompts",
            "error": None,
            "models": models,
            "form": {
                "name": "",
                "description": "",
                "body": "",
                "target_map_text": "{}",
                "output_schema_text": "{}",
                "model": default_model,
                "media_kind": "any",
            },
        },
    )
```

Also add `"models": [v.value for v in await ctx.enum_service.generation_models()]` to the **two error-path** `TemplateResponse` contexts in `action_create_prompt` (the validation-error branch ~line 101 and the IntegrityError branch ~line 133), and change the default in both `action_create_prompt`'s `model = form.get("model") or ...` (line 82) to:

```python
    model = form.get("model") or await ctx.enum_service.generation_default()
```

- [ ] **Step 4: Update `_prompt_new.html` to loop the context list**

In `backend/app/templates/pages/_prompt_new.html`, replace the hardcoded `<select>` loop (lines 19-23):

```html
        <select name="model" class="txt select-narrow">
          {% for m in models %}
            <option value="{{ m }}"{% if form.model == m %} selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
```

- [ ] **Step 5: Feed the Edit form picker from context (with orphan union)**

In `backend/app/routes/pages/prompts.py`, both routes that render `pages/prompts.html`
(`prompts_page`, `prompt_detail_page`) must pass `model_options`. Add a helper near
the bottom of the file:

```python
async def _model_options(ctx, selected_version) -> list[str]:
    """Enabled catalog models, unioned with the version's saved model when that
    model is no longer in the catalog (orphan safety: the Edit picker must still
    offer the saved value so editing never silently switches models)."""
    models = [v.value for v in await ctx.enum_service.generation_models()]
    saved = getattr(selected_version, "model", None) if selected_version else None
    if saved and saved not in models:
        models = [*models, saved]
    return models
```

In `prompts_page`, after computing `selected_version`, add to the context dict:

```python
            "model_options": await _model_options(ctx, selected_version),
```

In `prompt_detail_page`, likewise add to its context dict:

```python
            "model_options": await _model_options(ctx, selected_version),
```

- [ ] **Step 6: Inject `MODELS` into the Alpine editor**

In `backend/app/templates/pages/_prompt_detail.html`, find the Alpine init around line
11 (`model: {{ selected_version.model|tojson }},`) and add a sibling line inside the
same `x-data` object initialiser:

```html
                MODELS: {{ model_options|tojson }},
```

- [ ] **Step 7: Drop the hardcoded MODELS from promptEditor.js**

In `backend/app/static/promptEditor.js`, delete the `MODELS: [ ... ],` block (lines
34-43). The value now comes from the template's `x-data`. Leave a one-line comment:

```javascript
    // MODELS is injected by _prompt_detail.html from the enum catalog (issue #13).
```

- [ ] **Step 8: Run tests**

Run: `pytest tests/integration/test_prompt_new_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 9: Commit**

```bash
git add backend/app/routes/pages/prompts.py backend/app/templates/pages/_prompt_new.html backend/app/templates/pages/_prompt_detail.html backend/app/static/promptEditor.js tests/integration/test_prompt_new_models.py
git commit -m "feat(#13): prompt New/Edit model lists + default come from the catalog (orphan-safe)"
```

---

## Task 8: Admin rail icon (bottom-pinned)

**Files:**
- Create: `backend/app/templates/icons/_admin.svg`
- Modify: `backend/app/templates/pages/_rail.html`, `backend/app/static/app.css`

- [ ] **Step 1: Create the icon**

```html
<!-- backend/app/templates/icons/_admin.svg -->
<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
     stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z" />
  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
</svg>
```

- [ ] **Step 2: Add the bottom-pinned button to `_rail.html`**

After the Cache button (the last `<a class="rail-btn" ... title="Cache">` line) and
before the `<script>`, add:

```html
<a class="rail-btn rail-btn-bottom{% if _active == 'admin' %} active{% endif %}"
   href="/admin" title="Admin">{% include "icons/_admin.svg" %}</a>
```

- [ ] **Step 3: Pin it to the bottom in CSS**

In `backend/app/static/app.css`, after the `.rail-btn.active::before` rule (~line 221), add:

```css
.rail-btn-bottom { margin-top: auto; margin-bottom: 8px; }
```

- [ ] **Step 4: Manual check**

Run the app (`server-start` skill or your usual command) and load any page; the Admin
gear sits at the bottom of the left rail. (No automated test for pure CSS placement.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/icons/_admin.svg backend/app/templates/pages/_rail.html backend/app/static/app.css
git commit -m "feat(#13): bottom-pinned Admin rail icon"
```

---

## Task 9: Admin console shell + Models tab (read)

**Files:**
- Create: `backend/app/routes/pages/admin.py`, `backend/app/templates/pages/admin.html`, `backend/app/templates/pages/_admin_enum_table.html`
- Modify: `backend/app/routes/pages/__init__.py`
- Test: `tests/integration/test_admin_enums.py` (read portion)

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_admin_enums.py
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


def test_admin_lists_editable_enum_with_models(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin")
        assert r.status_code == 200
        assert "Gemini generation models" in r.text
        assert "gemini-2.5-flash-lite" in r.text


def test_admin_table_partial(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.get("/admin/enums/gemini_generation_model")
        assert r.status_code == 200
        assert "gemini-2.5-flash-lite" in r.text
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/integration/test_admin_enums.py -v -k "lists or partial"`
Expected: FAIL (404 — route missing)

- [ ] **Step 3: Write the admin router (read routes)**

```python
# backend/app/routes/pages/admin.py
"""Admin console: data-driven editing of editable enumerations (issue #13)."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from backend.app.deps import get_core_ctx
from backend.app.routes.pages.templates import templates
from backend.app.services.enum_service import EnumError
from backend.app.services.pricing import RATE_CARDS

router = APIRouter(tags=["pages"])


async def _enum_view(ctx, key: str) -> dict:
    defs = {d.key: d for d in await ctx.enum_service.definitions(editable_only=True)}
    if key not in defs:
        raise HTTPException(404, f"no editable enum {key!r}")
    values = await ctx.enum_service.values(key)
    is_model_enum = key == "gemini_generation_model"
    rows = [
        {
            "value": v.value,
            "label": v.label,
            "enabled": v.enabled,
            "is_default": v.is_default,
            "no_rate_card": is_model_enum and v.value not in RATE_CARDS,
        }
        for v in values
    ]
    return {"definition": defs[key], "rows": rows, "key": key}


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    ctx = get_core_ctx(request)
    definitions = await ctx.enum_service.definitions(editable_only=True)
    active_key = definitions[0].key if definitions else None
    view = await _enum_view(ctx, active_key) if active_key else None
    return templates.TemplateResponse(
        request,
        "pages/admin.html",
        {
            "rail_active": "admin",
            "definitions": definitions,
            "active_key": active_key,
            "view": view,
        },
    )


@router.get("/admin/enums/{key}", response_class=HTMLResponse)
async def admin_enum_table(request: Request, key: str):
    ctx = get_core_ctx(request)
    view = await _enum_view(ctx, key)
    return templates.TemplateResponse(request, "pages/_admin_enum_table.html", view)
```

- [ ] **Step 4: Write the console shell template**

```html
{# backend/app/templates/pages/admin.html #}
{% extends "pages/layout.html" %}
{% import "components/_ui.html" as ui %}
{% block title %}Admin · CatDV Annotator{% endblock %}
{% block rail_active %}admin{% endblock %}
{% block crumb %}{{ ui.breadcrumb([('Admin', None)]) }}{% endblock %}
{% block body %}
<div class="page admin-page">
  {% call ui.page_header('Admin') %}{% endcall %}
  <div class="cache-tabs">
    {% for d in definitions %}
      <a class="ctab{% if d.key == active_key %} active{% endif %}"
         href="/admin/enums/{{ d.key }}"
         hx-get="/admin/enums/{{ d.key }}"
         hx-target="#admin-enum-region"
         hx-swap="innerHTML"
         hx-push-url="false">{{ d.name }}</a>
    {% endfor %}
  </div>
  <div id="admin-enum-region">
    {% if view %}{% include "pages/_admin_enum_table.html" %}{% endif %}
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Write the table partial (read-only for now; actions added Task 10)**

```html
{# backend/app/templates/pages/_admin_enum_table.html #}
<div class="admin-enum" data-key="{{ key }}">
  <p class="meta">{{ definition.description }}</p>
  <table class="admin-table">
    <thead>
      <tr><th>Value</th><th>Label</th><th>Default</th><th>Enabled</th><th></th></tr>
    </thead>
    <tbody>
      {% for r in rows %}
      <tr>
        <td class="mono-cell">
          {{ r.value }}
          {% if r.no_rate_card %}<span class="pill warn" title="No pricing rate card; cost will not be tracked">no rate card</span>{% endif %}
        </td>
        <td>{{ r.label or '' }}</td>
        <td>{% if r.is_default %}★{% endif %}</td>
        <td>{{ 'on' if r.enabled else 'off' }}</td>
        <td></td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 6: Register the router**

In `backend/app/routes/pages/__init__.py`:

```python
from backend.app.routes.pages.admin import router as admin_router
from backend.app.routes.pages.clips import router as clips_router
from backend.app.routes.pages.prompts import router as prompts_router
from backend.app.routes.pages.studio import router as studio_router

page_routers = [clips_router, prompts_router, studio_router, admin_router]
```

- [ ] **Step 7: Run the read tests**

Run: `pytest tests/integration/test_admin_enums.py -v -k "lists or partial"`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add backend/app/routes/pages/admin.py backend/app/templates/pages/admin.html backend/app/templates/pages/_admin_enum_table.html backend/app/routes/pages/__init__.py tests/integration/test_admin_enums.py
git commit -m "feat(#13): Admin console shell + data-driven Models tab (read)"
```

---

## Task 10: Admin CRUD actions (add / enable / default / delete)

**Files:**
- Modify: `backend/app/routes/pages/admin.py`, `backend/app/templates/pages/_admin_enum_table.html`
- Test: `tests/integration/test_admin_enums.py` (append)

- [ ] **Step 1: Append failing CRUD tests**

```python
# tests/integration/test_admin_enums.py  (append)
def test_add_returns_partial_and_appears(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/enums/gemini_generation_model/values",
            data={"value": "gemini-4.0-pro"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        assert "gemini-4.0-pro" in r.text  # partial, not full page
        assert "<html" not in r.text.lower()


def test_remove_last_enabled_refused(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        # disable all but the default, then try to delete the default
        async def _setup_and_get_default():
            svc = client.app.state.core_ctx.enum_service  # type: ignore[attr-defined]
            await svc.reconcile_seeds()
            return await svc.generation_default()

        default = client.portal.call(_setup_and_get_default)  # type: ignore[attr-defined]
        r = client.request(
            "DELETE",
            f"/admin/enums/gemini_generation_model/values/{default}",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 400
        assert "default" in r.text.lower()


def test_set_default_moves_marker(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as client:
        r = client.post(
            "/admin/enums/gemini_generation_model/values/gemini-2.5-flash/default",
            headers={"HX-Request": "true"},
        )
        assert r.status_code == 200
        # exactly one ★ in the returned partial
        assert r.text.count("★") == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/integration/test_admin_enums.py -v -k "add or remove_last or set_default"`
Expected: FAIL (routes missing)

- [ ] **Step 3: Add CRUD routes to `admin.py`**

Add these imports at the top: `from fastapi import Form` (extend the existing fastapi import line) and `from backend.app.services.errors import humanise`. Then append:

```python
async def _table_response(request: Request, ctx, key: str, *, status_code: int = 200):
    view = await _enum_view(ctx, key)
    return templates.TemplateResponse(
        request, "pages/_admin_enum_table.html", view, status_code=status_code
    )


@router.post("/admin/enums/{key}/values", response_class=HTMLResponse)
async def admin_add_value(request: Request, key: str, value: str = Form(...), label: str | None = Form(None)):
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.add_value(key, value.strip(), label=(label or None))
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)


@router.post("/admin/enums/{key}/values/{value}/enabled", response_class=HTMLResponse)
async def admin_toggle_enabled(request: Request, key: str, value: str, enabled: bool = Form(...)):
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.set_enabled(key, value, enabled=enabled)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)


@router.post("/admin/enums/{key}/values/{value}/default", response_class=HTMLResponse)
async def admin_set_default(request: Request, key: str, value: str):
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.set_default(key, value)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)


@router.delete("/admin/enums/{key}/values/{value}", response_class=HTMLResponse)
async def admin_remove_value(request: Request, key: str, value: str):
    ctx = get_core_ctx(request)
    try:
        await ctx.enum_service.remove_value(key, value)
    except EnumError as exc:
        raise HTTPException(400, humanise(exc)) from exc
    return await _table_response(request, ctx, key)
```

- [ ] **Step 4: Add the action controls to the table partial**

Replace the empty action cells and add an add-row form in `_admin_enum_table.html`.
Update the `<tbody>` row's last two cells and add a footer form (uses `ui` macros via
plain HTMX forms; no `location.reload()`):

```html
        <td>
          <button class="btn sm ghost"
                  hx-post="/admin/enums/{{ key }}/values/{{ r.value }}/default"
                  hx-target="#admin-enum-region" hx-swap="innerHTML"
                  {% if r.is_default %}disabled{% endif %}>Make default</button>
          <form class="inline" hx-post="/admin/enums/{{ key }}/values/{{ r.value }}/enabled"
                hx-target="#admin-enum-region" hx-swap="innerHTML">
            <input type="hidden" name="enabled" value="{{ 'false' if r.enabled else 'true' }}">
            <button class="btn sm ghost" type="submit">{{ 'Disable' if r.enabled else 'Enable' }}</button>
          </form>
        </td>
        <td>
          <button class="btn sm ghost danger"
                  hx-delete="/admin/enums/{{ key }}/values/{{ r.value }}"
                  hx-target="#admin-enum-region" hx-swap="innerHTML">Delete</button>
        </td>
```

Add, after the `</table>`:

```html
  <form class="admin-add-row" hx-post="/admin/enums/{{ key }}/values"
        hx-target="#admin-enum-region" hx-swap="innerHTML"
        hx-on::after-request="if(event.detail.successful) this.reset()">
    {{ ui.field('Add value', name='value', placeholder='model-id') }}
    <button class="btn primary" type="submit">Add</button>
  </form>
```

> The error toast for a failed add/delete comes for free: HTMX surfaces the 400 body;
> add `hx-on::response-error` wiring to `Alpine.store('toast')` only if the project's
> existing pattern (see `cacheActions.js`) doesn't already globally bridge HTMX errors.
> Check `static/cacheActions.js` / `htmxAlpine.js` for the existing bridge before adding
> bespoke handling — reuse it.

- [ ] **Step 5: Run all admin tests**

Run: `pytest tests/integration/test_admin_enums.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/pages/admin.py backend/app/templates/pages/_admin_enum_table.html tests/integration/test_admin_enums.py
git commit -m "feat(#13): Admin Models CRUD via HTMX partials with guards"
```

---

## Task 11: CLAUDE.md "Enumerations" section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add the section**

Insert after the "Cache management" section (or near "AI Integration"):

```markdown
## Enumerations

Two kinds of enumeration; route each correctly.

- **Fixed enum** — every value has matching handling logic (`if status ==
  'applied'`, a CSS class per level, a code branch). Keep it a `Literal` in
  `models/` for static checking **and** declare it in
  `backend/app/enums/registry.py` with `editable=False`, so the frontend reads it
  from one place. The values are served straight from code (the DB is never
  touched). Add a guard test pinning the registry values to `get_args(<Literal>)`.

- **Editable list** — an open set whose values are just data passed through (model
  catalogs). Declare it in the registry with `editable=True` (the `values` are the
  seed + the one `default=True`). The DB table `enum_values` stores the user's
  edits; `EnumService.reconcile_seeds()` materialises seeds at boot with
  soft-delete tombstones. Users edit it in the Admin console (`/admin`).

**Never** hardcode either kind in a template, a `<select>`, or a JS array again.

How to consume:
- Backend: `ctx.enum_service.values(key)` / `.generation_models()` /
  `.generation_default()`. `EnumService` is on `CoreCtx` — DB-only and
  offline-safe.
- Frontend: fixed enums arrive as `window.APP_ENUMS.<key>` (injected by
  `layout.html`). Editable lists arrive via route context (server-rendered, so
  orphaned saved values can be unioned in) or `GET /api/enums/{key}`.

How to add a new enum: add an `EnumSpec` to `ENUM_REGISTRY`. Editable enums also
get a row in the Admin console automatically (tabs are data-driven from
`definitions(editable_only=True)`). No new table or migration is needed unless you
add a second editable enum — they all share `enum_values`.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(#13): CLAUDE.md Enumerations guidance"
```

---

## Task 12: Full verification + ADR + follow-up issue

**Files:**
- Create: `docs/adr/00NN-centralised-enumeration.md` (next ADR number)
- Modify: `docs/decisions.md`

- [ ] **Step 1: Run the whole suite + guards**

Run:
```bash
pytest tests/unit/test_enum_registry.py tests/unit/test_enum_values_repo.py \
  tests/unit/test_enum_service.py tests/unit/test_enum_service_offline.py \
  tests/integration/test_admin_enums.py tests/integration/test_prompt_new_models.py \
  tests/integration/test_enum_bootstrap.py \
  tests/unit/test_context_delegation.py tests/unit/test_design_language_guard.py \
  tests/unit/test_templates_shared.py tests/unit/test_no_sync_fs_in_async.py -v
```
Expected: all PASS. Then run `lint-imports` and the full `pytest -q` to confirm no
regressions (especially `tests/integration/test_clips_page_perf.py` and the design
guards).

- [ ] **Step 2: Write the ADR**

Create `docs/adr/00NN-centralised-enumeration.md` (use the next free number; check
`ls docs/adr | tail`). Use the MADR-lite format with `## Context` / `## Alternatives`
/ `## Decision` / `## Consequences`. Capture: code registry as canonical SoT;
`enum_values` holds only editable edits (no `enum_definitions` table — superseded);
fixed enums served to the frontend read-only; boot-time reconcile with soft-delete
tombstones; orphaned-reference union in the prompt edit form. Add a row to the index
table in `docs/decisions.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/adr docs/decisions.md
git commit -m "docs(#13): ADR for centralised enumeration architecture"
```

- [ ] **Step 4: Open the follow-up issue**

```bash
gh issue create -R futurememorylab/ai-archive \
  --title "Centralise remaining fixed enums into the registry (follow-up to #13)" \
  --body "$(cat <<'EOF'
Follow-up to #13. The enum registry + EnumService + window.APP_ENUMS mechanism is
in place, with `toast_level` migrated as the exemplar. Migrate the remaining
*fixed* (code-coupled) enums into `backend/app/enums/registry.py` (editable=False)
for frontend SSOT — behaviour-preserving, no logic change:

- `JobStatus` / `ItemStatus` (+ the `_BATCH_STATUS_VIEW` display map in clips.py)
- `CacheFilter` / `AnnoFilter` (clip_list_filters.py)
- review `decision` (annotation.py)
- prompt version state (prompt.py)

For each: add an `EnumSpec`, add a guard test pinning registry values to
`get_args(<Literal>)`, and switch the frontend usage (JS/templates) to
`window.APP_ENUMS`. Do not make these editable.
EOF
)"
```

- [ ] **Step 5: Final commit / push handled by the branch workflow.**

---

## Self-Review

**Spec coverage** (spec section → task):
- Code registry / fixed + editable kinds → Task 1.
- `enum_values` table + repo + soft-delete tombstones → Task 2.
- EnumService reads + total fallback + reconcile → Task 3; writes + all guards → Task 4.
- CoreCtx/LiveCtx wiring + reconcile at boot + offline-safe → Task 5.
- `window.APP_ENUMS` (fixed) + `/api/enums/{key}` + toast.js consumer → Task 6.
- Three/four drift sites rewired (settings default via service, dropdown, edit picker, default) + orphaned-reference union → Task 7. (pricing.py intentionally only gets a badge — Task 9/10.)
- Admin rail icon (bottom-pinned) → Task 8; console shell + data-driven tabs (read) → Task 9; CRUD + guards + no-rate-card badge → Task 10.
- CLAUDE.md guidance → Task 11.
- ADR + follow-up issue for remaining fixed enums → Task 12.

**Placeholder scan:** the only `NN`/`00NN` tokens are the migration number (`0020`, with the override note) and the ADR number (resolved against `docs/adr`), both explicitly instructed — no TBDs in code steps.

**Type consistency:** `EnumValueRow` (repo) vs `EnumValue`/`EnumDefinition` (service) are distinct by design (row vs API model) and used consistently. `EnumError` raised in service, caught in routes (Tasks 6, 10) and the JSON API (Task 6). `generation_models()`/`generation_default()`/`values()`/`reconcile_seeds()` names match across Tasks 3–7. `enum_service`/`enum_values_repo` field names match across Tasks 5–10.

**Known soft spot to confirm during execution:** the integration tests drive async
edits via `client.portal.call(...)`. If the installed Starlette/anyio `TestClient`
doesn't expose `.portal`, replace those few lines with a small fixture that grabs
`app.state.core_ctx.enum_service` and runs the coroutine on the running loop — the
HTML assertions are unchanged. This is the one environment-dependent detail; resolve
it in Task 6 (first use) and reuse the chosen helper in Tasks 7 and 10.
```
