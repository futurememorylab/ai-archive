# Resolution Becomes Real (PR2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `media_resolution` a real, captured setting — it drives the actual Gemini call, has a per-model default (admin-editable) and a per-prompt-version override, and the effective value is recorded in `run_telemetry`.

**Architecture:** A fixed `media_resolution` enum ('low'|'medium'|'high'). The effective resolution for a run is resolved override-first: `prompt_version.media_resolution` → `model_config.default_media_resolution` → `'medium'`. That value is mapped to the google-genai `MediaResolution` SDK enum and passed in the `generate_content` config, and is written to `run_telemetry.media_resolution_setting`. The admin "Gemini models" tab's resolution column becomes an editable dropdown.

**Tech Stack:** FastAPI, aiosqlite (SQLite), Jinja/HTMX/Alpine, `google-genai` (Vertex), pydantic v2, pytest.

**Scope note:** This PR deliberately excludes (a) the "show resolution on cost surfaces" estimate-label display — that lands in PR3 with the resolution-aware estimator; and (b) the prompt-editor UI control for the override — a later UI slice. PR2 wires the override end-to-end at the data/engine level (settable + tested via the version repo API).

**Conventions:** Tests run with `.venv/bin/python -m pytest <path> -v` (Python 3.12). Commits are SSH-signed: `git commit -S`. Migrations live in `backend/migrations/NNNN_name.sql`; next free number is **0025** (highest existing is 0024). Stay on branch `claude/relaxed-archimedes-ii1l2t`.

---

### Task 1: `media_resolution` fixed enum

**Files:**
- Create: `backend/app/models/media.py`
- Modify: `backend/app/enums/registry.py` (add an `EnumSpec` next to `toast_level`)
- Test: `tests/unit/test_media_resolution_enum.py`

- [ ] **Step 1: Write the failing test** at `tests/unit/test_media_resolution_enum.py`:

```python
"""media_resolution is a fixed enum; registry values pinned to the Literal."""

from typing import get_args

from backend.app.enums.registry import ENUM_REGISTRY
from backend.app.models.media import MediaResolution


def test_media_resolution_registry_matches_literal():
    spec = ENUM_REGISTRY["media_resolution"]
    assert spec.editable is False
    assert tuple(v.value for v in spec.values) == get_args(MediaResolution)


def test_media_resolution_values():
    assert get_args(MediaResolution) == ("low", "medium", "high")
```

- [ ] **Step 2: Run it, confirm FAIL** (ModuleNotFoundError / KeyError).
Run: `.venv/bin/python -m pytest tests/unit/test_media_resolution_enum.py -v`

- [ ] **Step 3: Create `backend/app/models/media.py`:**

```python
"""Media resolution — a fixed enum controlling how many tokens a clip's media
costs in a Gemini call (low/medium/high). Source of truth for the Literal;
the enum registry pins to it (test_media_resolution_enum)."""

from typing import Literal

MediaResolution = Literal["low", "medium", "high"]

DEFAULT_MEDIA_RESOLUTION: MediaResolution = "medium"
```

- [ ] **Step 4: Add the registry spec** in `backend/app/enums/registry.py`, immediately after the `toast_level` entry (mirror its shape):

```python
    "media_resolution": EnumSpec(
        key="media_resolution",
        name="Media resolutions",
        description="How much detail (and token cost) a clip's media gets in a Gemini call.",
        editable=False,
        values=(
            EnumValueSpec("low"),
            EnumValueSpec("medium"),
            EnumValueSpec("high"),
        ),
    ),
```
(Use the same value-spec constructor the file already uses — check whether `toast_level` uses `EnumValueSpec("info")` or a helper like `_m(...)`; match it exactly.)

- [ ] **Step 5: Run the test, confirm PASS (2).**

- [ ] **Step 6: Commit.**
```bash
git add backend/app/models/media.py backend/app/enums/registry.py tests/unit/test_media_resolution_enum.py
git commit -S -m "feat(resolution): add fixed media_resolution enum"
```

---

### Task 2: Thread media_resolution into the Gemini call

**Files:**
- Modify: `backend/app/services/gemini.py`
- Test: `tests/unit/test_gemini_media_resolution.py`

The current `GeminiService.annotate(*, file_ref, prompt, schema, model)` builds a config dict and calls `self._client.models.generate_content(...)`. Add a `media_resolution` param that, when set, maps `'low'|'medium'|'high'` to the SDK strings `MEDIA_RESOLUTION_LOW|MEDIUM|HIGH` and adds `"media_resolution"` to the config dict. When `None`, the config is unchanged (no key added).

- [ ] **Step 1: Write the failing test** at `tests/unit/test_gemini_media_resolution.py`:

```python
"""annotate() maps media_resolution into the generate_content config."""

from backend.app.services.gemini import GeminiService, _SDK_MEDIA_RESOLUTION


class _FakeModels:
    def __init__(self):
        self.last_config = None

    def generate_content(self, *, model, contents, config):
        self.last_config = config

        class _R:
            text = "{}"

            def model_dump(self):
                return {}

        return _R()


class _FakeClient:
    def __init__(self):
        self.models = _FakeModels()


def _svc():
    svc = GeminiService.__new__(GeminiService)  # bypass genai.Client construction
    svc._client = _FakeClient()
    return svc


def test_media_resolution_added_when_set():
    svc = _svc()
    svc.annotate(file_ref={"x": 1}, prompt="p", schema={}, model="m", media_resolution="high")
    assert svc._client.models.last_config["media_resolution"] == "MEDIA_RESOLUTION_HIGH"


def test_media_resolution_absent_when_none():
    svc = _svc()
    svc.annotate(file_ref={"x": 1}, prompt="p", schema={}, model="m")
    assert "media_resolution" not in svc._client.models.last_config


def test_sdk_map_covers_all_levels():
    assert _SDK_MEDIA_RESOLUTION == {
        "low": "MEDIA_RESOLUTION_LOW",
        "medium": "MEDIA_RESOLUTION_MEDIUM",
        "high": "MEDIA_RESOLUTION_HIGH",
    }
```

- [ ] **Step 2: Run it, confirm FAIL** (ImportError `_SDK_MEDIA_RESOLUTION` / TypeError unexpected kwarg).
Run: `.venv/bin/python -m pytest tests/unit/test_gemini_media_resolution.py -v`

- [ ] **Step 3: Edit `backend/app/services/gemini.py`.** Add the module-level map (near the top, after imports):

```python
# Our 'low'|'medium'|'high' → the google-genai MediaResolution enum string.
_SDK_MEDIA_RESOLUTION = {
    "low": "MEDIA_RESOLUTION_LOW",
    "medium": "MEDIA_RESOLUTION_MEDIUM",
    "high": "MEDIA_RESOLUTION_HIGH",
}
```
Change the `annotate` signature to add `media_resolution: str | None = None` (keyword-only, after `model`), and build the config so the key is added only when set:

```python
    def annotate(
        self,
        *,
        file_ref: dict[str, Any],
        prompt: str,
        schema: dict[str, Any],
        model: str,
        media_resolution: str | None = None,
    ) -> dict[str, Any]:
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": schema,
        }
        if media_resolution is not None:
            config["media_resolution"] = _SDK_MEDIA_RESOLUTION[media_resolution]
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[{"text": prompt}, file_ref],
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            raise _classify(exc) from exc

        text = getattr(response, "text", "")
        raw = response.model_dump() if hasattr(response, "model_dump") else {}
        return {"text": text, "raw": raw}
```

- [ ] **Step 4: Run the test, confirm PASS (3).**

- [ ] **Step 5: Commit.**
```bash
git add backend/app/services/gemini.py tests/unit/test_gemini_media_resolution.py
git commit -S -m "feat(resolution): pass media_resolution into the Gemini call"
```

---

### Task 3: Per-model default resolution is editable (repo + service)

**Files:**
- Modify: `backend/app/repositories/model_config.py` (add `set_resolution`)
- Modify: `backend/app/services/pricing_service.py` (add `set_resolution` + `default_resolution`)
- Test: extend `tests/unit/test_model_config_repo.py`, `tests/unit/test_pricing_service.py`

- [ ] **Step 1: Write failing tests.** In `tests/unit/test_model_config_repo.py` add:

```python
async def test_set_resolution_updates_only_resolution(db):
    repo = ModelConfigRepo()
    await repo.upsert_seed(db, _card("m1"), commit=True)
    await repo.set_resolution(db, "m1", "high", commit=True)
    row = await repo.get(db, "m1")
    assert row.default_media_resolution == "high"
    # rates untouched
    assert row.input_text_video_image_per_1m == 0.10
```
In `tests/unit/test_pricing_service.py` add:

```python
async def test_set_and_get_default_resolution(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    await svc.set_resolution("gemini-2.5-flash-lite", "low")
    assert await svc.default_resolution("gemini-2.5-flash-lite") == "low"


async def test_default_resolution_falls_back_to_medium_for_unknown(db):
    svc = _service(db)
    await svc.reconcile_seeds()
    assert await svc.default_resolution("not-a-model") == "medium"
```

- [ ] **Step 2: Run them, confirm FAIL.**
Run: `.venv/bin/python -m pytest tests/unit/test_model_config_repo.py tests/unit/test_pricing_service.py -v`

- [ ] **Step 3: Add `set_resolution` to `ModelConfigRepo`** (in `backend/app/repositories/model_config.py`):

```python
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
```

- [ ] **Step 4: Add to `PricingService`** (in `backend/app/services/pricing_service.py`):

```python
    async def set_resolution(self, model: str, resolution: str) -> None:
        """Admin edit of a model's default media resolution; refresh nothing in
        the rate cache (resolution isn't a rate), just persist."""
        await self._repo.set_resolution(self._db(), model, resolution, commit=True)

    async def default_resolution(self, model: str) -> str:
        """The model's default media resolution, or 'medium' if the model has no
        rate card (offline-safe DB lookup)."""
        row = await self._repo.get(self._db(), model)
        if row is None or row.removed:
            return "medium"
        return row.default_media_resolution
```

- [ ] **Step 5: Run the tests, confirm PASS.**

- [ ] **Step 6: Commit.**
```bash
git add backend/app/repositories/model_config.py backend/app/services/pricing_service.py \
        tests/unit/test_model_config_repo.py tests/unit/test_pricing_service.py
git commit -S -m "feat(resolution): editable per-model default resolution (repo+service)"
```

---

### Task 4: PromptVersion gains a nullable `media_resolution` override

**Files:**
- Create: `backend/migrations/0025_prompt_version_media_resolution.sql`
- Modify: `backend/app/models/prompt.py` (add field)
- Modify: `backend/app/repositories/prompts.py` (`_VERSION_COLS`, `_row_to_version`, create/clone/update threading)
- Test: `tests/unit/test_prompt_version_media_resolution.py` (or extend the existing prompts repo test)

- [ ] **Step 1: Write the failing test** at `tests/unit/test_prompt_version_media_resolution.py`. First inspect the existing prompts-repo test (`grep -rln "PromptsRepo\|create_with_initial_version" tests/`) and mirror its DB/fixture setup. The test must prove: a new prompt's v1 defaults `media_resolution` to `None`; `update_version(..., media_resolution="low")` on a draft persists it; `create_version` (clone) copies the source's `media_resolution`. Skeleton:

```python
import pytest
from backend.app.repositories.prompts import PromptsRepo

pytestmark = pytest.mark.asyncio


async def test_version_media_resolution_roundtrip(db):
    repo = PromptsRepo()
    prompt_id, vid = await repo.create_with_initial_version(
        db, name="p", description=None, body="b", target_map={},
        output_schema={"type": "object"}, model="gemini-2.5-flash-lite",
    )
    v = await repo.get_version(db, vid)
    assert v.media_resolution is None  # default

    await repo.update_version(
        db, vid, body="b2", target_map={}, output_schema={"type": "object"},
        model="gemini-2.5-flash-lite", media_resolution="low",
    )
    assert (await repo.get_version(db, vid)).media_resolution == "low"

    new_vid = await repo.create_version(db, prompt_id, from_version_id=vid)
    assert (await repo.get_version(db, new_vid)).media_resolution == "low"  # cloned
```
(Adapt `target_map`/`output_schema`/`model` arg shapes to whatever the existing repo signature requires — see the verbatim methods in the repo.)

- [ ] **Step 2: Run it, confirm FAIL.**
Run: `.venv/bin/python -m pytest tests/unit/test_prompt_version_media_resolution.py -v`

- [ ] **Step 3: Migration** `backend/migrations/0025_prompt_version_media_resolution.sql`:

```sql
-- 0025: optional per-prompt-version media-resolution override. NULL = use the
-- model's default_media_resolution. Versioned with the prompt (clones copy it).
-- See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md §2.
ALTER TABLE prompt_versions ADD COLUMN media_resolution TEXT;
```

- [ ] **Step 4: Model field.** In `backend/app/models/prompt.py`, add to `PromptVersion` (after `model: str`):
```python
    media_resolution: str | None = None
```

- [ ] **Step 5: Repo threading** in `backend/app/repositories/prompts.py`:
  - Add `media_resolution` to `_VERSION_COLS` (the SELECT column list) and to `_row_to_version` (map the new column to the field; it may be NULL).
  - `create_with_initial_version`: add `media_resolution` to the INSERT column list + values, defaulting `None` (add a `media_resolution: str | None = None` kwarg).
  - `create_version` (clone): add `media_resolution` to the INSERT, sourcing `src.media_resolution`.
  - `update_version`: add a `media_resolution: str | None = None` kwarg and include `media_resolution = ?` in the UPDATE SET.
  Keep `_now_iso()` / commit behavior identical. Match the existing column ordering carefully so positional value tuples line up.

- [ ] **Step 6: Run the test, confirm PASS.** Also run the existing prompts repo/service tests to confirm no break:
`.venv/bin/python -m pytest tests/unit -k "prompt" -q`

- [ ] **Step 7: Commit.**
```bash
git add backend/migrations/0025_prompt_version_media_resolution.sql backend/app/models/prompt.py \
        backend/app/repositories/prompts.py tests/unit/test_prompt_version_media_resolution.py
git commit -S -m "feat(resolution): per-prompt-version media_resolution override column"
```

---

### Task 5: Resolve effective resolution in the run path + capture in telemetry

**Files:**
- Create: `backend/app/services/resolution.py` (pure resolver)
- Modify: `backend/app/services/annotator.py` (`_process_item` resolves + passes; `_record_telemetry` records)
- Test: `tests/unit/test_resolution_resolver.py`; extend an annotator integration test for the telemetry capture.

- [ ] **Step 1: Write the failing resolver test** at `tests/unit/test_resolution_resolver.py`:

```python
from backend.app.services.resolution import resolve_media_resolution


def test_override_wins():
    assert resolve_media_resolution("high", "low") == "high"


def test_model_default_when_no_override():
    assert resolve_media_resolution(None, "low") == "low"


def test_medium_when_neither():
    assert resolve_media_resolution(None, None) == "medium"
```

- [ ] **Step 2: Run it, confirm FAIL.**
Run: `.venv/bin/python -m pytest tests/unit/test_resolution_resolver.py -v`

- [ ] **Step 3: Create `backend/app/services/resolution.py`:**

```python
"""Effective media-resolution resolution: per-prompt override beats the model
default, which beats the global 'medium' fallback. See cost-prediction spec §2."""

from backend.app.models.media import DEFAULT_MEDIA_RESOLUTION


def resolve_media_resolution(
    version_override: str | None, model_default: str | None
) -> str:
    return version_override or model_default or DEFAULT_MEDIA_RESOLUTION
```

- [ ] **Step 4: Run it, confirm PASS (3).**

- [ ] **Step 5: Wire into `annotator.py`.** In `_process_item`, before the `gemini.annotate(...)` call, resolve the effective resolution. The model default comes from the pricing service — `_process_item` must be able to read it. Determine how services reach `_process_item` (it receives explicit kwargs like `gemini`, repos). Add a `model_config_repo` (the `ModelConfigRepo`) kwarg to `_process_item` **and** to its single call site, then resolve:

```python
        from backend.app.services.resolution import resolve_media_resolution
        _mc = await model_config_repo.get(db, version.model)
        _model_default = _mc.default_media_resolution if _mc and not _mc.removed else None
        media_resolution = resolve_media_resolution(version.media_resolution, _model_default)
```
Pass it into the call:
```python
    result = await asyncio.to_thread(
        gemini.annotate,
        file_ref=file_ref,
        prompt=rendered_body,
        schema=version.output_schema,
        model=version.model,
        media_resolution=media_resolution,
    )
```
Thread `media_resolution` into BOTH `_record_telemetry(...)` calls (studio + annotation finalization) as a new kwarg `media_resolution_setting=media_resolution`.

  Find `_process_item`'s caller (`grep -n "_process_item(" backend/app/services/annotator.py`) and pass `model_config_repo=...`. The caller has access to the ctx/repos; use the same `ModelConfigRepo` instance the ctx holds (`ctx.model_config_repo`) or construct `ModelConfigRepo()` (it is stateless). Prefer threading `ctx.model_config_repo` from the run entry point; if the entry point has `ctx`, use `ctx.model_config_repo`.

- [ ] **Step 6: Record it.** In `_record_telemetry`, add a `media_resolution_setting: str | None = None` kwarg and set `media_resolution_setting=media_resolution_setting` in the `RunTelemetryRecord(...)` constructor.

- [ ] **Step 7: Test the capture.** Find an existing annotator/studio integration test that asserts on a written `run_telemetry` row (`grep -rln "run_telemetry\|RunTelemetryRepo\|_record_telemetry\|media_resolution_setting" tests/`). Extend it (or add a focused test) so that a run whose model defaults to (say) `high` writes `media_resolution_setting == "high"`, and a run whose prompt version overrides to `low` writes `"low"`. If the existing tests use a fake Gemini, assert the fake received the mapped `media_resolution` too. Run:
`.venv/bin/python -m pytest tests/ -k "annotator or telemetry or studio_run" -q`

- [ ] **Step 8: Commit.**
```bash
git add backend/app/services/resolution.py backend/app/services/annotator.py tests/unit/test_resolution_resolver.py <the_extended_test>
git commit -S -m "feat(resolution): resolve effective resolution per run + capture in telemetry"
```

---

### Task 6: Admin "Gemini models" tab — editable resolution dropdown

**Files:**
- Modify: `backend/app/templates/pages/_admin_models_table.html` (read-only cell → `<select>` when `has_card`)
- Modify: `backend/app/routes/pages/admin.py` (add `POST /admin/models/{model}/resolution`)
- Modify: `tests/walkthrough/scenarios/admin_models_rates.py` (assert the dropdown)
- Test: extend `tests/integration/test_admin_models_tab.py`

- [ ] **Step 1: Write the failing integration test.** In `tests/integration/test_admin_models_tab.py` add:

```python
def test_set_resolution_persists(client_and_tmp):  # adapt to the file's harness
    client, tmp_path = client_and_tmp
    r = client.post("/admin/models/gemini-2.5-flash-lite/resolution",
                    data={"media_resolution": "high"})
    assert r.status_code == 200
    body = client.get("/admin/models").text
    # the flash-lite row's select shows 'high' selected
    assert "high" in body
    import aiosqlite, asyncio
    async def _read():
        async with aiosqlite.connect(tmp_path / "app.db") as c:
            cur = await c.execute(
                "SELECT default_media_resolution FROM model_config WHERE model=?",
                ("gemini-2.5-flash-lite",))
            return (await cur.fetchone())[0]
    assert asyncio.run(_read()) == "high"


def test_resolution_unknown_model_404(client):  # adapt
    r = client.post("/admin/models/not-a-model/resolution", data={"media_resolution": "low"})
    assert r.status_code == 404
```
(Match the existing test harness/fixtures in that file — reuse its `_client` setup and the `tmp_path`-based DB path used by the durability tests already there.)

- [ ] **Step 2: Run it, confirm FAIL (404, route missing).**
Run: `.venv/bin/python -m pytest tests/integration/test_admin_models_tab.py -k resolution -v`

- [ ] **Step 3: Add the route** in `backend/app/routes/pages/admin.py` (near the other model routes), validating the model is a catalog member with a rate card, and the value is a valid resolution:

```python
@router.post("/admin/models/{model}/resolution", response_class=HTMLResponse)
async def admin_set_model_resolution(
    request: Request, model: str, media_resolution: str = Form(...)
):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    if media_resolution not in get_args(MediaResolution):
        raise HTTPException(422, f"bad media_resolution {media_resolution!r}")
    has_card = any(r.model == model for r in await ctx.pricing_service.rows())
    if not has_card:
        raise HTTPException(404, f"no rate card for {model!r}")
    await ctx.pricing_service.set_resolution(model, media_resolution)
    return await _models_response(request, ctx)
```
Add imports at the top of `admin.py`: `from typing import get_args` and `from backend.app.models.media import MediaResolution`.

- [ ] **Step 4: Editable dropdown** in `_admin_models_table.html`. Replace the read-only cell
`<td class="mono-cell">{{ r.default_media_resolution }}</td>` with a select that posts on change when the model has a card, else the existing dash. Use the shared `.txt` select styling (confirm a `<select class="txt">` is acceptable to the design-language guard; the enum tabs/other selects show the sanctioned class — match an existing `<select>` in the codebase):

```html
        <td>
          {% if r.has_card %}
          <select class="txt" name="media_resolution"
                  hx-post="/admin/models/{{ r.model }}/resolution"
                  hx-target="#admin-enum-region" hx-swap="innerHTML">
            {% for opt in ['low','medium','high'] %}
            <option value="{{ opt }}"{% if opt == r.default_media_resolution %} selected{% endif %}>{{ opt }}</option>
            {% endfor %}
          </select>
          {% else %}<span class="mono-cell">—</span>{% endif %}
        </td>
```
(HTMX posts the `<select>`'s own `name=media_resolution` on `change` by default for non-form controls? It does NOT auto-trigger on change for a bare select — add `hx-trigger="change"`.) Use:
`hx-post="/admin/models/{{ r.model }}/resolution" hx-trigger="change" hx-target="#admin-enum-region" hx-swap="innerHTML"`.
The select submits its own value because HTMX includes the triggering element's value. Verify the four rate inputs are NOT swept into this post (they would be, via the element's enclosing form — but there is no form; HTMX includes only the triggering element plus `hx-include`, which we omit, so only the select's `name`/value is sent). Keep the literal `['low','medium','high']` inline OR read from `window.APP_ENUMS.media_resolution` — inline is simpler and the guard test only pins the registry, not the template.

- [ ] **Step 5: Run the test, confirm PASS.** Then guards:
`.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py tests/integration/test_admin_models_tab.py -q`

- [ ] **Step 6: Update the walkthrough** `tests/walkthrough/scenarios/admin_models_rates.py`: add a step asserting the priced model row has a resolution `<select>` (e.g. `expect(p.locator(".admin-models select[name='media_resolution']").first).to_be_visible()`). Validate selectors against rendered HTML via TestClient (Chromium can't run in-sandbox); confirm the scenario imports and `run` is callable.

- [ ] **Step 7: Commit.**
```bash
git add backend/app/templates/pages/_admin_models_table.html backend/app/routes/pages/admin.py \
        tests/integration/test_admin_models_tab.py tests/walkthrough/scenarios/admin_models_rates.py
git commit -S -m "feat(admin): editable per-model default media resolution dropdown"
```

---

### Task 7: Regression + guards

**Files:** none (verification only), unless a guard surfaces a fix.

- [ ] **Step 1: Guards.**
```bash
.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py tests/unit/test_context_delegation.py tests/unit/test_enum_registry.py -q
.venv/bin/lint-imports
```
All pass. Fix any PR2-introduced violation (don't edit the guard).

- [ ] **Step 2: Full suite.**
```bash
.venv/bin/python -m pytest tests/unit tests/integration -q
```
All pass (target: 0 failures). Diagnose/fix any PR2-caused failure; report any that look pre-existing/unrelated rather than force-fixing.

- [ ] **Step 3: Commit** (only if a fix was needed).
```bash
git add -A && git commit -S -m "test: regression fixes for PR2 resolution work"
```

---

## Self-review notes (for the executor)

- **Spec coverage:** enum (§2) → T1; Gemini threading (§2) → T2; per-model default editable (§1 PR2 part) → T3+T6; per-prompt override column (§2) → T4; effective-resolution order + telemetry capture (§2) → T5. Deferred by design: cost-surface display (§5 → PR3), prompt-editor override control (later UI slice).
- **Type consistency:** `media_resolution` strings are `'low'|'medium'|'high'` everywhere (the `MediaResolution` Literal); the SDK mapping to `MEDIA_RESOLUTION_*` happens ONLY inside `gemini.py`. Telemetry stores the lowercase form in `media_resolution_setting`.
- **Offline safety:** `default_resolution`/`set_resolution` are DB-only (no network). The resolver is pure. No new live-ctx dependency.
- **ADR:** PR2 follows the spec; no new design decision unless T5's `model_config_repo` threading into `_process_item` proves contentious — if the run entry point makes that awkward, record a short ADR for how the model default reaches the run path.
