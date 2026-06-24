# Calibration Sweep (PR4a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An Admin "Prompts" tab where an admin picks 3 clips and runs a calibration sweep — 3 clips × {low, medium, high} × 2 repeats = 18 real Gemini runs — that writes ONLY `run_telemetry` rows (no annotations/studio-runs/review-items), populating per-resolution estimator history. A results panel shows cost-by-resolution and the confidence each resolution has unlocked.

**Architecture:** Reuse the existing annotator run path with two new flags on `run_job`/`_process_item`: `force_resolution` (drive a specific resolution, bypassing the resolver) and `record_only` (after the Gemini call, record telemetry + mark the item done, but skip the finalize writes — no studio-runs/review-items/annotations). Calibration creates 6 jobs (3 resolutions × 2 repeats), each with the 3 clips, each launched in the background with its `force_resolution` and `record_only=True`. The results panel reads `run_telemetry` grouped by `media_resolution_setting` for the prompt version.

**Tech Stack:** FastAPI, aiosqlite, Jinja/HTMX/Alpine, `google-genai` (Vertex), the shared clip-picker (`clipPicker.js` + `_clip_picker_*.html`), pytest.

**Scope notes:**
- **4b (the `countTokens` real-cost button) is deferred** — not in this plan.
- Calibration makes **real Gemini API calls** when invoked (admin action, behind a projected-cost confirm). Tests use a FAKE Gemini — no spend in CI/dev.
- The projected pre-confirm cost is approximate: one estimate × 6 (the estimate is resolution-blind at the prompt's default until history exists). Good enough for a "this will cost roughly $X" confirm.

**Conventions:** Tests run with `.venv/bin/python -m pytest <path> -v` (Python 3.12). Commits SSH-signed: `git commit -S`. Stay on branch `claude/relaxed-archimedes-ii1l2t`. Verbatim current code for every touched surface was gathered; signatures below match the live code.

---

### Task A1: `force_resolution` + `record_only` through the run path

**Files:**
- Modify: `backend/app/services/annotator.py` (`run_job`, `_process_item`)
- Modify: `backend/app/routes/jobs.py` (`_run_in_bg`, `start_job_in_background`)
- Modify: `backend/app/routes/studio.py` (`_run_in_bg` — keep defaults so behaviour is unchanged)
- Test: extend `tests/integration/test_annotator_telemetry.py`

`run_job` currently ends its `_process_item(...)` call with the existing kwargs. Add two parameters and thread them down. In `_process_item`, `force_resolution` overrides the resolver; `record_only` replaces the `_finalize_studio`/`_finalize_annotation` branch with a telemetry-only path.

- [ ] **Step 1: Write failing tests** in `tests/integration/test_annotator_telemetry.py` (reuse the file's existing `run_job` + `FakeGeminiCapturing` + seeded-DB harness — read the existing `test_media_resolution_setting_from_model_default` and `test_media_resolution_override_beats_model_default` for the exact setup):

```python
@pytest.mark.asyncio
async def test_force_resolution_overrides_resolver(...):
    # model default 'high', version override 'low' — but force 'medium' wins.
    # set model_config default to high, version override to low, then run with force_resolution="medium"
    # assert FakeGemini received media_resolution == "medium" and telemetry media_resolution_setting == "medium"
    ...

@pytest.mark.asyncio
async def test_record_only_writes_telemetry_but_no_studio_runs_or_reviews(...):
    # run a studio-kind job with record_only=True
    # assert: exactly one run_telemetry row exists (status ok, with media_resolution_setting),
    #         AND studio_runs table has NO row for this job/clip,
    #         AND review_items has NO row for this job/clip.
    ...
```
Adapt the setup to the harness (it builds jobs via `jobs_repo.create_job`, drives `run_job(...)` with all the repos + a fake Gemini; pass the new `force_resolution=`/`record_only=` kwargs). To assert "no studio_runs/review_items", query those tables directly on the test DB (`SELECT COUNT(*) ... WHERE job_id = ?`).

- [ ] **Step 2: Run, confirm FAIL** (unexpected kwargs).
Run: `.venv/bin/python -m pytest tests/integration/test_annotator_telemetry.py -k "force_resolution or record_only" -v`

- [ ] **Step 3: `run_job` signature + threading.** Add `force_resolution: str | None = None` and `record_only: bool = False` to `run_job`'s keyword-only params (after `only_clip_ids`). Pass both into the `_process_item(...)` call:
```python
                model_config_repo=model_config_repo,
                event_bus=event_bus,
                topic=topic,
                force_resolution=force_resolution,
                record_only=record_only,
```

- [ ] **Step 4: `_process_item` — force_resolution + record_only.** Add `force_resolution: str | None = None` and `record_only: bool = False` to its keyword-only params. Change the resolution resolution so a forced value wins:
```python
        from backend.app.services.resolution import resolve_media_resolution

        if force_resolution is not None:
            media_resolution = force_resolution
        else:
            _mc = await model_config_repo.get(db, version.model)
            _model_default = _mc.default_media_resolution if _mc and not _mc.removed else None
            media_resolution = resolve_media_resolution(version.media_resolution, _model_default)
```
Then replace the `if kind == "studio": _finalize_studio(...) else: _finalize_annotation(...)` block so `record_only` short-circuits to telemetry-only:
```python
        ai_store_kind = getattr(ai_store, "id", None)
        if record_only:
            # Calibration: record the telemetry row (the estimator's history)
            # but write NO studio-runs / review-items / annotations.
            await jobs_repo.update_item_status(db, item.id, "done")
            await event_bus.publish(topic, {"item_id": item.id, "status": "done"})
            await _record_telemetry(
                db,
                run_telemetry_repo,
                telemetry_ctx,
                kind="studio",
                item=item,
                version=version,
                status="ok",
                result=result,
                duration_s=elapsed_s,
                capture=capture,
                est=est,
                ai_store_kind=ai_store_kind,
                media_resolution_setting=media_resolution,
            )
        elif kind == "studio":
            await _finalize_studio(... media_resolution_setting=media_resolution)
        else:
            await _finalize_annotation(... media_resolution_setting=media_resolution)
```
(Keep the existing `_finalize_studio`/`_finalize_annotation` calls exactly as they are — only wrap them in the new `if record_only / elif / else`. Confirm `item.id` status value "done" matches what `_finalize_*` uses for a successful item — read the finalize code; if it's a different string e.g. "applied"/"for_review"/"completed", use the neutral one the schema allows, e.g. "done"; the job_items.status CHECK constraint — verify the allowed values and pick the success value the existing studio path sets.)

- [ ] **Step 5: jobs.py launch helpers.** `_run_in_bg(ctx, job_id, *, only_clip_ids=None)` and `start_job_in_background(core, live, job_id, *, only_clip_ids=None)` gain `force_resolution: str | None = None` and `record_only: bool = False`, passed into `run_job(...)` and forwarded from `start_job_in_background` → `_run_in_bg`. (Studio's `_run_in_bg` in `studio.py` — leave its `run_job(...)` call as-is; defaults keep behaviour unchanged.)

- [ ] **Step 6: Run the new tests + the existing annotator/telemetry suite, confirm PASS.**
```
.venv/bin/python -m pytest tests/integration/test_annotator_telemetry.py -v
.venv/bin/python -m pytest tests/ -k "annotator or studio or jobs" -q
```

- [ ] **Step 7: Commit.**
```bash
git add backend/app/services/annotator.py backend/app/routes/jobs.py backend/app/routes/studio.py tests/integration/test_annotator_telemetry.py
git commit -S -m "feat(calibration): force_resolution + record_only flags on the run path"
```

---

### Task A2: Telemetry stats-by-resolution reader + confidence helper

**Files:**
- Modify: `backend/app/repositories/run_telemetry.py` (add `stats_by_resolution`)
- Create: `backend/app/services/calibration.py` (a tiny `confidence_for_samples` helper)
- Test: extend `tests/integration/test_run_telemetry_repo.py`; new `tests/unit/test_calibration_confidence.py`

- [ ] **Step 1: Failing tests.** In `tests/integration/test_run_telemetry_repo.py` (reuse the `_insert_run` helper added in PR3):
```python
@pytest.mark.asyncio
async def test_stats_by_resolution(db):
    repo = RunTelemetryRepo()
    await _insert_run(db, prompt_version_id=5, media_kind="video", status="ok",
                      media_resolution_setting="low", cost_usd=0.10)
    await _insert_run(db, prompt_version_id=5, media_kind="video", status="ok",
                      media_resolution_setting="low", cost_usd=0.20)
    await _insert_run(db, prompt_version_id=5, media_kind="video", status="ok",
                      media_resolution_setting="high", cost_usd=1.00)
    stats = await repo.stats_by_resolution(db, prompt_version_id=5)
    assert stats["low"] == {"count": 2, "cost_usd": pytest.approx(0.30)}
    assert stats["high"] == {"count": 1, "cost_usd": pytest.approx(1.00)}
```
And `tests/unit/test_calibration_confidence.py`:
```python
from backend.app.services.calibration import confidence_for_samples

def test_confidence_thresholds():
    assert confidence_for_samples(0) == "rough"
    assert confidence_for_samples(2) == "rough"
    assert confidence_for_samples(3) == "fair"
    assert confidence_for_samples(9) == "fair"
    assert confidence_for_samples(10) == "good"
```

- [ ] **Step 2: Run, confirm FAIL.**

- [ ] **Step 3: Add `stats_by_resolution`** to `RunTelemetryRepo`:
```python
    async def stats_by_resolution(
        self, conn: aiosqlite.Connection, *, prompt_version_id: int
    ) -> dict[str | None, dict]:
        """{media_resolution_setting: {"count": n, "cost_usd": total}} for ok
        runs of a prompt version — powers the calibration results panel."""
        cur = await conn.execute(
            "SELECT media_resolution_setting, COUNT(*), COALESCE(SUM(cost_usd), 0) "
            "FROM run_telemetry WHERE prompt_version_id = ? AND status = 'ok' "
            "GROUP BY media_resolution_setting",
            (prompt_version_id,),
        )
        out: dict[str | None, dict] = {}
        for res, count, cost in await cur.fetchall():
            out[res] = {"count": int(count), "cost_usd": float(cost)}
        return out
```

- [ ] **Step 4: Create `backend/app/services/calibration.py`:**
```python
"""Calibration helpers. The confidence a resolution has 'unlocked' mirrors the
estimator's sample thresholds (run_estimator._MIN_SAMPLES / _GOOD_SAMPLES)."""

from backend.app.services.run_estimator import _GOOD_SAMPLES, _MIN_SAMPLES


def confidence_for_samples(n: int) -> str:
    if n >= _GOOD_SAMPLES:
        return "good"
    if n >= _MIN_SAMPLES:
        return "fair"
    return "rough"
```

- [ ] **Step 5: Run the tests, confirm PASS.**
`.venv/bin/python -m pytest tests/integration/test_run_telemetry_repo.py -k resolution tests/unit/test_calibration_confidence.py -v`

- [ ] **Step 6: Commit.**
```bash
git add backend/app/repositories/run_telemetry.py backend/app/services/calibration.py \
        tests/integration/test_run_telemetry_repo.py tests/unit/test_calibration_confidence.py
git commit -S -m "feat(calibration): stats-by-resolution reader + confidence helper"
```

---

### Task A3: Admin "Prompts" tab — list versions + results panel

**Files:**
- Modify: `backend/app/routes/pages/admin.py` (add `_prompts_view`, `_prompts_response`, `GET /admin/prompts`)
- Create: `backend/app/templates/pages/_admin_prompts_table.html`
- Modify: `backend/app/templates/pages/admin.html` (add the "Prompts" tab link)
- Test: extend `tests/integration/` admin tab tests (new `tests/integration/test_admin_prompts_tab.py`)

- [ ] **Step 1: Failing test** `tests/integration/test_admin_prompts_tab.py` (reuse the admin `_client` harness from `tests/integration/test_admin_models_tab.py`):
```python
def test_prompts_tab_lists_versions(...):
    # seed a prompt + production version (via PromptsRepo.create_with_initial_version + set production),
    # GET /admin/prompts → 200, body contains the prompt name and a "Calibrate" control.
    ...
```
(Adapt setup to the harness; create a prompt through `ctx.prompts_repo` on the test app's DB, or via the existing prompt-creation route. Keep it minimal — just enough that one version row renders.)

- [ ] **Step 2: Run, confirm FAIL (404).**

- [ ] **Step 3: Route + view in `admin.py`.** Add (near the models routes):
```python
async def _prompts_view(ctx) -> dict:
    prompts = await ctx.prompts_repo.list_active(ctx.db)
    rows = []
    for p in prompts:
        _p, versions = await ctx.prompts_repo.get_with_versions(ctx.db, p.id)
        for v in versions:
            stats = await ctx.run_telemetry_repo.stats_by_resolution(ctx.db, prompt_version_id=v.id)
            per_res = {
                res: {
                    "count": s["count"],
                    "cost_usd": s["cost_usd"],
                    "confidence": confidence_for_samples(s["count"]),
                }
                for res, s in stats.items() if res is not None
            }
            rows.append({
                "prompt_name": p.name, "version_id": v.id, "version_num": v.version_num,
                "state": v.state, "model": v.model, "per_res": per_res,
            })
    return {"rows": rows}


async def _prompts_response(request: Request, ctx):
    return templates.TemplateResponse(request, "pages/_admin_prompts_table.html", await _prompts_view(ctx))


@router.get("/admin/prompts", response_class=HTMLResponse)
async def admin_prompts_table(request: Request):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    return await _prompts_response(request, ctx)
```
Add imports: `from backend.app.services.calibration import confidence_for_samples`. (Guard against an N+1 here being a problem only if there are many versions — acceptable for an admin page; if the existing query-count guards complain, batch the stats read. For now per-version is fine — this is an admin-only page, not a hot path.)

- [ ] **Step 4: Template `backend/app/templates/pages/_admin_prompts_table.html`** — a `.admin-prompts` wrapper, a `.meta` description, and an `.admin-table`: one row per version showing prompt name · v#, state, model, the per-resolution `count / cost / confidence` (a compact cell — e.g. `low: 6 runs · $0.30 · fair`), and a **Calibrate** button that opens the calibrate dialog (Task A4 wires the dialog; for now the button is a placeholder `type="button"` with a `data-version-id`). Reuse `.admin-table`/`.mono-cell`/`.meta`/`.pill` and the `.btn` system — no hand-rolled classes (design-language guard).

- [ ] **Step 5: Tab link in `admin.html`** — after the "Gemini models" tab `<a>` and before the `{% for d in definitions ... %}` loop:
```html
    <a class="ctab{% if active == 'prompts' %} active{% endif %}"
       href="/admin/prompts"
       hx-get="/admin/prompts"
       hx-target="#admin-enum-region"
       hx-swap="innerHTML"
       hx-push-url="false">Prompts</a>
```

- [ ] **Step 6: Run the test + guards, confirm PASS.**
```
.venv/bin/python -m pytest tests/integration/test_admin_prompts_tab.py tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py tests/integration/test_admin_enums.py -q
```

- [ ] **Step 7: Commit.**
```bash
git add backend/app/routes/pages/admin.py backend/app/templates/pages/_admin_prompts_table.html \
        backend/app/templates/pages/admin.html tests/integration/test_admin_prompts_tab.py
git commit -S -m "feat(admin): Prompts tab with per-resolution calibration results"
```

---

### Task A4: Calibrate dialog (pick 3 clips) + projected cost + launch sweep

**Files:**
- Modify: `backend/app/templates/pages/_admin_prompts_table.html` (the Calibrate modal: clip picker + projected cost + confirm) — or a new `_admin_calibrate_modal.html` included by it
- Modify: `backend/app/templates/pages/admin.html` (load `clipPicker.js` + ensure the picker partials/endpoint are reachable on the admin page)
- Modify: `backend/app/routes/pages/admin.py` (projected-cost endpoint + launch endpoint)
- Modify: `backend/app/routes/jobs.py` only if a shared helper is cleaner (otherwise self-contained in admin.py)
- Test: `tests/integration/test_admin_calibrate.py`

This is the largest task — it reuses the shared clip picker (`_clip_picker_main.html` + `_clip_picker_basket.html` + `static/clipPicker.js`, which fetches rows from `/batches/picker`) inside an admin modal, gated to EXACTLY 3 clips, with a projected cost, that POSTs to a launch endpoint creating the 6 calibration jobs.

- [ ] **Step 1: Failing launch-endpoint test** `tests/integration/test_admin_calibrate.py`. Drive the LIVE-ctx test app (the walkthrough/integration harness installs a fake Gemini + fake archive — see how `test_annotator_telemetry.py` or the walkthrough sets up `live_ctx`). Assert: `POST /admin/prompts/{version_id}/calibrate` with `clip_ids=[c1,c2,c3]` (3 cached clips) → 200/started; and that it created **6 jobs** (3 resolutions × 2 repeats) each with 3 items, tagged `run_group` starting `calibration:`. Use `ctx.jobs_repo` to count jobs by run_group. (Assert job creation, not full run completion — the background runs use the fake Gemini; you MAY await completion and assert 18 telemetry rows with the 3 resolutions if the harness makes that deterministic, otherwise assert the 6 jobs + their force_resolution wiring.)
Also test the guards: non-3 clip count → 422; missing version → 404; offline (no live ctx) → 503.

- [ ] **Step 2: Run, confirm FAIL.**

- [ ] **Step 3: Launch endpoint** in `admin.py`:
```python
import time as _time

CALIBRATION_RESOLUTIONS = ("low", "medium", "high")
CALIBRATION_REPEATS = 2


@router.post("/admin/prompts/{version_id}/calibrate", response_class=HTMLResponse)
async def admin_calibrate(request: Request, version_id: int, clip_ids: list[int] = Form(...)):
    require_role(request, "admin")
    core = get_core_ctx(request)
    live = request.app.state.live_ctx
    if live is None:
        raise HTTPException(503, "Gemini offline — calibration needs the live API")
    if len(clip_ids) != 3:
        raise HTTPException(422, "calibration needs exactly 3 clips")
    try:
        await core.prompts_repo.get_version(core.db, version_id)
    except LookupError:
        raise HTTPException(404, "prompt version not found") from None
    run_group = f"calibration:{version_id}:{int(_time.time())}"
    for res in CALIBRATION_RESOLUTIONS:
        for _ in range(CALIBRATION_REPEATS):
            job_id = await core.jobs_repo.create_job(
                core.db, prompt_version_id=version_id, clip_ids=clip_ids,
                kind="studio", run_group=run_group,
            )
            start_job_in_background(core, live, job_id, force_resolution=res, record_only=True)
    return await _prompts_response(request, core)
```
(Import `start_job_in_background` from `backend.app.routes.jobs`. `Form(...)` for `clip_ids` accepts repeated form fields; the dialog posts `clip_ids` as multiple values via `hx-vals` or a form. Confirm FastAPI parses `list[int] = Form(...)` from repeated fields — it does.)

- [ ] **Step 4: Projected-cost endpoint** (advisory, for the dialog) in `admin.py`:
```python
@router.post("/admin/prompts/{version_id}/calibrate/estimate")
async def admin_calibrate_estimate(request: Request, version_id: int, body: dict = Body(...)):
    require_role(request, "admin")
    ctx = get_core_ctx(request)
    clip_ids = [int(c) for c in body.get("clip_ids", [])]
    est = await estimate_for_clip_ids(
        ctx.db, clip_cache_repo=ctx.clip_cache_repo, run_telemetry_repo=ctx.run_telemetry_repo,
        prompts_repo=ctx.prompts_repo, model_config_repo=ctx.model_config_repo,
        provider_id=ctx.settings.archive_provider, clip_ids=clip_ids, prompt_version_id=version_id,
    )
    runs = len(CALIBRATION_RESOLUTIONS) * CALIBRATION_REPEATS
    p50 = est["cost_usd_p50"]
    return {"projected_cost_usd": (p50 * runs) if p50 is not None else None, "runs": runs * len(clip_ids)}
```
(Import `estimate_for_clip_ids` + `Body`. This returns JSON for the dialog's JS.)

- [ ] **Step 5: The Calibrate modal** in the prompts template. Use `{{ ui.modal(...) }}` (design-language §9) with an Alpine component spreading `window.clipPickerCore()` (the same one batches.html uses), including `_clip_picker_main.html` + `_clip_picker_basket.html`. The confirm button is enabled only when `selCount() === 3`; on open it knows the `version_id`; a small `x-effect` POSTs the selected ids to `.../calibrate/estimate` and shows "Projected: ~$X over N runs"; Confirm POSTs `clip_ids` to `.../calibrate` (HTMX, targeting `#admin-enum-region`). Reuse the batches modal's clip-picker wiring as the reference — do NOT hand-roll a second picker.

- [ ] **Step 6: Admin page wiring** in `admin.html` — load `static/clipPicker.js` (and any CSS the picker needs) on the admin page so the modal's Alpine component resolves. Confirm `/batches/picker` (the picker's row endpoint) is reachable for an admin user (it should be a normal authenticated clips endpoint). If the picker needs page-context (e.g. `media_cache`), pass it into the admin template context the same way batches.html provides it.

- [ ] **Step 7: Run tests + guards, confirm PASS.**
```
.venv/bin/python -m pytest tests/integration/test_admin_calibrate.py tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py -q
```

- [ ] **Step 8: Walkthrough.** Add/extend a walkthrough scenario for the Prompts tab: open `/admin` → Prompts, open the Calibrate dialog, confirm the picker renders and the projected-cost line appears (selector-validate against rendered HTML; the actual sweep needs the live fake-Gemini harness — assert the dialog UI, not 18 real runs, in the walkthrough). Confirm the scenario imports + `run` is callable.

- [ ] **Step 9: Commit.**
```bash
git add backend/app/routes/pages/admin.py backend/app/templates/pages/_admin_prompts_table.html \
        backend/app/templates/pages/admin.html backend/app/routes/jobs.py \
        tests/integration/test_admin_calibrate.py <walkthrough scenario>
git commit -S -m "feat(admin): calibrate dialog — pick 3 clips, projected cost, launch sweep"
```

---

### Task A5: Regression + guards + ADR

- [ ] **Step 1: Guards + lint.**
```bash
.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py tests/unit/test_context_delegation.py -q
.venv/bin/lint-imports
```

- [ ] **Step 2: Full suite.**
```bash
.venv/bin/python -m pytest tests/unit tests/integration -q
```
All pass. Fix any PR4a-caused failure; report anything pre-existing.

- [ ] **Step 3: ADR.** Add `docs/adr/0116-calibration-record-only-run-path.md` (MADR-lite) documenting the `record_only` decision: calibration reuses the studio run path with writes suppressed (telemetry-only) rather than a new run-kind/migration, and `force_resolution` drives the sweep. Update `docs/decisions.md`. Mark PR4a "shipped" in the cost-prediction spec's implementation-order §7; note 4b (real-cost button) still pending.

- [ ] **Step 4: Commit.**
```bash
git add docs/adr/0116-calibration-record-only-run-path.md docs/decisions.md docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md
git commit -S -m "docs: ADR 0116 + spec amendment for calibration (PR4a)"
```

---

## Self-review notes (for the executor)

- **Spec coverage (§4 calibration):** Admin Prompts tab + Calibrate action → A3/A4; admin picks 3 clips → A4 (shared picker); 3×{low,med,high}×2=18 runs → A4 launch (6 jobs × 3 clips) with A1's `force_resolution`; projected cost before confirm → A4; writes ordinary run_telemetry only (no pollution) → A1 `record_only`; cost-by-resolution + confidence results panel → A2 + A3.
- **Type consistency:** resolutions are `'low'|'medium'|'high'` strings; `force_resolution` bypasses the resolver; telemetry `media_resolution_setting` stores the forced value; `kind="studio"` on the telemetry row (calibration is a studio-style record).
- **No pollution:** `record_only` skips `_finalize_studio`/`_finalize_annotation` entirely — assert in A1 that studio_runs/review_items stay empty.
- **Offline safety:** calibration LAUNCH needs LiveCtx (real Gemini) → 503 offline; the Prompts tab + results panel + projected estimate are CoreCtx/DB-only (offline-safe).
- **Real spend:** the only real-API path is the admin-triggered sweep, behind a projected-cost confirm. Tests use a fake Gemini.
- **ADR:** the `record_only` reuse-vs-new-kind decision is genuinely a design call → ADR 0116 (A5).
