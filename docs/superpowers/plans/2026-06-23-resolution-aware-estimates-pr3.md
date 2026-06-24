# Accurate, Resolution-Aware Estimates (PR3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make pre-run estimates resolution-aware (learned rates keyed on `(model, kind, resolution)`), surface the resolution-in-force on the estimate UI, and show an estimate-vs-actual delta on the batches list — without reintroducing an N+1.

**Architecture:** `run_telemetry` already records `media_resolution_setting` (PR2) and both `est_*` and actual `cost_usd` per run. PR3 (1) adds `media_resolution` to the two estimator history reads + the estimator's fallback chain `(prompt_hash, model, kind, res) → (model, kind, res) → seeds`; (2) resolves the effective resolution server-side in the estimate endpoint (override → model default → 'medium', same resolver as a run) and returns it; (3) shows it on the estimate label; (4) adds an `est_cost_sums_by_job` reader so the batches list can show estimated-vs-actual.

**Tech Stack:** FastAPI, aiosqlite (SQLite), Jinja/HTMX/Alpine + vanilla JS, pytest, the `assert_query_count` N+1 guard.

**Scope decisions (deliberate):**
- **Resolution-keyed *history*, resolution-blind *seeds*.** Cold-start (zero history) seeds stay as today; once ≥3 runs exist at a resolution, that resolution's real rates take over. Softens spec §3's "resolution-scaled seed constants" — a per-model-per-resolution seed table is fragile and only affects the already-"rough" cold-start case. Note this in the ADR if it proves contentious.
- **Resolution display on the pre-run surfaces** (batch modal, studio header); **estimate-vs-actual on the batches list**. The clips page keeps showing actual-only (its column is historical per-clip spend).

**Conventions:** Tests run with `.venv/bin/python -m pytest <path> -v` (Python 3.12). Commits SSH-signed: `git commit -S`. Next free migration number is **0026** (highest is 0025). Stay on branch `claude/relaxed-archimedes-ii1l2t`. No JS unit runner exists — JS changes are covered by careful review + the Playwright walkthrough (selector-validated; the headed assert needs Chromium, unavailable in this sandbox).

---

### Task 1: Resolution-key the telemetry history reads (repo + index)

**Files:**
- Modify: `backend/app/repositories/run_telemetry.py` (`recent_input_ratios`, `recent_output_rates`)
- Create: `backend/migrations/0026_run_telemetry_resolution_index.sql`
- Test: extend `tests/integration/test_run_telemetry_repo.py`

Both readers gain an optional `media_resolution: str | None = None`. When provided, add `media_resolution_setting = ?` to the WHERE clause (and its param). When None, behaviour is unchanged (no filter) — keeps any other caller working.

- [ ] **Step 1: Write failing tests.** First read `tests/integration/test_run_telemetry_repo.py` to match its fixtures (how it inserts `run_telemetry` rows — likely via `RunTelemetryRepo().insert(db, RunTelemetryRecord(...))`). Add tests proving the filter:

```python
@pytest.mark.asyncio
async def test_recent_output_rates_filtered_by_resolution(db):
    repo = RunTelemetryRepo()
    # two ok video runs: one at 'high', one at 'low', different output rates
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="high",
                      media_duration_secs=10.0, tokens_out=1000, tokens_thinking=0)
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="low",
                      media_duration_secs=10.0, tokens_out=100, tokens_thinking=0)
    high = await repo.recent_output_rates(db, model="m", media_kind="video", media_resolution="high")
    low = await repo.recent_output_rates(db, model="m", media_kind="video", media_resolution="low")
    assert high == [100.0]   # 1000/10
    assert low == [10.0]     # 100/10
    both = await repo.recent_output_rates(db, model="m", media_kind="video")  # no filter
    assert sorted(both) == [10.0, 100.0]


@pytest.mark.asyncio
async def test_recent_input_ratios_filtered_by_resolution(db):
    repo = RunTelemetryRepo()
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="high",
                      media_duration_secs=10.0, tokens_in_video=2000)
    await _insert_run(db, model="m", media_kind="video", media_resolution_setting="low",
                      media_duration_secs=10.0, tokens_in_video=500)
    assert await repo.recent_input_ratios(db, model="m", media_kind="video", media_resolution="high") == [200.0]
    assert await repo.recent_input_ratios(db, model="m", media_kind="video", media_resolution="low") == [50.0]
```
Write a `_insert_run(db, **overrides)` helper (or reuse the file's existing one) that inserts a `RunTelemetryRecord` with sensible defaults (`occurred_at`, `install_id`, `kind='annotation'`, `model`, `status='ok'`, and the overrides). Mirror how the existing repo tests build a record.

- [ ] **Step 2: Run, confirm FAIL** (unexpected kwarg `media_resolution`).
Run: `.venv/bin/python -m pytest tests/integration/test_run_telemetry_repo.py -k resolution -v`

- [ ] **Step 3: Edit `recent_input_ratios`** — add `media_resolution: str | None = None` (keyword-only), and after the existing WHERE conditions add the optional filter. The method currently inlines its WHERE in an f-string; extend it to append `AND media_resolution_setting = ?` and the param when set. Concretely, change the body to build params + an optional clause:

```python
    async def recent_input_ratios(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
        media_resolution: str | None = None,
        limit: int = 50,
    ) -> list[float]:
        col_expr = {
            "video+audio": "COALESCE(tokens_in_video, 0) + COALESCE(tokens_in_audio, 0)",
            "video": "COALESCE(tokens_in_video, 0)",
            "audio": "COALESCE(tokens_in_audio, 0)",
            "image": "COALESCE(tokens_in_image, 0)",
        }.get(media_kind, "COALESCE(tokens_in_video, 0)")
        res_clause = " AND media_resolution_setting = ?" if media_resolution is not None else ""
        params: list = [model, media_kind]
        if media_resolution is not None:
            params.append(media_resolution)
        params.append(limit)
        cur = await conn.execute(
            f"SELECT CAST(({col_expr}) AS REAL) / media_duration_secs "
            "FROM run_telemetry "
            "WHERE model = ? AND media_kind = ? AND status = 'ok' "
            f"AND COALESCE(media_duration_secs, 0) > 0 AND ({col_expr}) > 0{res_clause} "
            "ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [r[0] for r in await cur.fetchall()]
```

- [ ] **Step 4: Edit `recent_output_rates`** — it already builds a `where`/`params` list. Add `media_resolution: str | None = None` (keyword-only) and, alongside the existing `prompt_hash` optional clause, append `media_resolution_setting = ?` when set:

```python
        if media_resolution is not None:
            where.append("media_resolution_setting = ?")
            params.append(media_resolution)
```
(Place it before the `params.append(limit)` line, same as the `prompt_hash` block.)

- [ ] **Step 5: Index migration** `backend/migrations/0026_run_telemetry_resolution_index.sql` (additive — leave the existing index in place):
```sql
-- 0026: resolution-aware estimator index. The estimator now filters on
-- media_resolution_setting (PR3), so add it to the covering index ahead of
-- status/prompt_hash. Additive; the 0016 index stays for any resolution-blind
-- read. See docs/specs/2026-06-22-accurate-resolution-aware-cost-prediction-design.md §3.
CREATE INDEX idx_run_telemetry_estimator_res
  ON run_telemetry (model, media_kind, media_resolution_setting, status, prompt_hash);
```

- [ ] **Step 6: Run the new tests + the full repo suite, confirm PASS.**
`.venv/bin/python -m pytest tests/integration/test_run_telemetry_repo.py -v`

- [ ] **Step 7: Commit.**
```bash
git add backend/app/repositories/run_telemetry.py backend/migrations/0026_run_telemetry_resolution_index.sql tests/integration/test_run_telemetry_repo.py
git commit -S -m "feat(estimator): resolution-filter the telemetry history reads + index"
```

---

### Task 2: Resolution-key the estimator + resolve effective resolution

**Files:**
- Modify: `backend/app/services/run_estimator.py` (`estimate_clips`, `estimate_for_clip_ids`)
- Modify: `backend/app/routes/jobs.py` (`estimate_job` passes `model_config_repo`)
- Test: extend `tests/unit/test_run_estimator.py`; update `tests/integration/test_estimate_query_count.py`

- [ ] **Step 1: Write failing unit tests.** In `tests/unit/test_run_estimator.py`, extend `FakeRepo` to key on resolution and add tests. The fake's readers gain a `media_resolution` kwarg; canned data keys on `(media_kind, prompt_hash, media_resolution)`:

```python
class FakeRepo:
    def __init__(self, input_ratios=None, output_rates=None):
        # input_ratios keyed by (media_kind, media_resolution)
        # output_rates keyed by (media_kind, prompt_hash or "*", media_resolution)
        self.input_ratios = input_ratios or {}
        self.output_rates = output_rates or {}

    async def recent_input_ratios(self, conn, *, model, media_kind, media_resolution=None, limit=50):
        return self.input_ratios.get((media_kind, media_resolution), [])

    async def recent_output_rates(self, conn, *, model, media_kind, prompt_hash=None, media_resolution=None, limit=50):
        return self.output_rates.get((media_kind, prompt_hash or "*", media_resolution), [])
```
Add tests:
```python
@pytest.mark.asyncio
async def test_estimate_uses_resolution_keyed_history():
    repo = FakeRepo(output_rates={("video+audio", "*", "low"): [5.0] * 5,
                                  ("video+audio", "*", "high"): [50.0] * 5})
    low = await estimate_clips(None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="low")
    high = await estimate_clips(None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="high")
    assert low.tokens_out_p50 == 300    # 60s * 5/s
    assert high.tokens_out_p50 == 3000  # 60s * 50/s


@pytest.mark.asyncio
async def test_resolution_with_no_history_falls_back_to_seeds_rough():
    repo = FakeRepo(output_rates={("video+audio", "*", "high"): [50.0] * 5})
    est = await estimate_clips(None, repo, [VIDEO], prompt_body="", schema={}, model="m", media_resolution="low")
    assert est.confidence == "rough"  # no 'low' history → seeds
```
(Keep all EXISTING tests passing — they call `estimate_clips(...)` without `media_resolution`; the new param defaults to None which the fake treats as the `(kind, prompt_hash, None)` key. To keep the existing canned-data tests valid, update the existing `FakeRepo` canned keys in those tests to use `None` as the resolution slot — i.e. `("video+audio", "HASH")` becomes `("video+audio", "HASH", None)`. Do that mechanically for every existing `output_rates`/`input_ratios` literal in the file.)

- [ ] **Step 2: Run, confirm FAIL.**
Run: `.venv/bin/python -m pytest tests/unit/test_run_estimator.py -v`

- [ ] **Step 3: Thread `media_resolution` through `estimate_clips`.** Add `media_resolution: str | None = None` as a keyword param. Pass it into BOTH repo reads:
```python
            ratios = await repo.recent_input_ratios(
                conn, model=model, media_kind=kind, media_resolution=media_resolution
            )
            ...
        rates = await repo.recent_output_rates(
            conn, model=model, media_kind=kind, prompt_hash=p_hash, media_resolution=media_resolution
        )
        ...
            rates = await repo.recent_output_rates(
                conn, model=model, media_kind=kind, media_resolution=media_resolution
            )
```
(The fallback chain keeps the same two output reads — L1 prompt_hash+resolution, L2 resolution — just with the extra kwarg. Query count per kind is unchanged.)

- [ ] **Step 4: Resolve effective resolution in `estimate_for_clip_ids`.** It must receive a `model_config_repo` to read the model default. Add a `model_config_repo` keyword param; after loading `version`, resolve:
```python
    from backend.app.services.resolution import resolve_media_resolution

    version = await prompts_repo.get_version(conn, prompt_version_id)
    _mc = await model_config_repo.get(conn, version.model)
    _model_default = _mc.default_media_resolution if _mc and not _mc.removed else None
    media_resolution = resolve_media_resolution(version.media_resolution, _model_default)
```
Pass `media_resolution=media_resolution` into the `estimate_clips(...)` call, and add it to the returned dict:
```python
    return {
        ...
        "n_unknown": n_unknown,
        "media_resolution": media_resolution,
    }
```

- [ ] **Step 5: Update the route** `backend/app/routes/jobs.py::estimate_job` to pass `model_config_repo=ctx.model_config_repo` into `estimate_for_clip_ids(...)`.

- [ ] **Step 6: Update the query-count test** `tests/integration/test_estimate_query_count.py`. The estimate now does one extra `model_config_repo.get` per call (constant, not per-clip), so the expected count goes 5 → 6. Change both `assert_query_count(db, 5)` to `assert_query_count(db, 6)` and update the breakdown comment to include "+1 model_config default-resolution read". The N=10 vs N=100 equality (the actual N+1 guard) is the point — keep both assertions. (That test builds a CoreCtx-like setup; ensure it has a `model_config_repo` — it likely constructs repos directly; pass `model_config_repo=ModelConfigRepo()`.)

- [ ] **Step 7: Run all estimator tests + the query guard, confirm PASS.**
```
.venv/bin/python -m pytest tests/unit/test_run_estimator.py tests/integration/test_estimate_query_count.py -v
```

- [ ] **Step 8: Commit.**
```bash
git add backend/app/services/run_estimator.py backend/app/routes/jobs.py \
        tests/unit/test_run_estimator.py tests/integration/test_estimate_query_count.py
git commit -S -m "feat(estimator): resolution-aware estimates (resolve + key history on resolution)"
```

---

### Task 3: Show resolution-in-force on the pre-run estimate surfaces

**Files:**
- Modify: `backend/app/templates/pages/batches.html` (`estimateLabel()`, `refreshEstimate()` merge)
- Modify: `backend/app/static/studioStore.js` (`estimateLabel` build)
- Test: walkthrough scenario(s) if present (selector-validated)

The estimate response now carries `media_resolution` (Task 2). Surface it.

- [ ] **Step 1: Studio header** (`backend/app/static/studioStore.js`) — single clip/version → one resolution. Change the label build:
```javascript
        this.estimateLabel =
          `~${fmtUsd(e.cost_usd_p50)}–${fmtUsd(e.cost_usd_p90)} (${e.confidence}) · ${e.media_resolution} res`;
```

- [ ] **Step 2: Batch modal** (`backend/app/templates/pages/batches.html`). `refreshEstimate()` merges multiple per-(prompt,kind) parts. Collect the distinct resolutions across parts and carry the result onto `this.estimate`:
```javascript
          const resolutions = new Set();
          for (const p of parts) {
            ...
            if (p.media_resolution) resolutions.add(p.media_resolution);
          }
          this.estimate = {
            ...,
            media_resolution: resolutions.size === 1 ? [...resolutions][0]
              : (resolutions.size === 0 ? null : "mixed"),
          };
```
Then in `estimateLabel()` append it when present:
```javascript
        if (e.media_resolution) s += ` · ${e.media_resolution} res`;
```

- [ ] **Step 3: Manual/selector check.** There is no JS unit runner. Boot the app via the walkthrough/TestClient harness and confirm the studio/batches estimate endpoints return `media_resolution` (already covered by Task 2's API), and that the template/JS reference `e.media_resolution`. If a studio or batches walkthrough scenario exists (`grep -rln "estimate\|studio\|batch" tests/walkthrough/scenarios/`), update it to assert the resolution appears in the estimate label; validate selectors against rendered HTML (Chromium unavailable in sandbox). If no scenario covers this, note it (a JS-only change has no Python test; rely on review + the API test from Task 2).

- [ ] **Step 4: Commit.**
```bash
git add backend/app/templates/pages/batches.html backend/app/static/studioStore.js <any walkthrough scenario>
git commit -S -m "feat(estimator): show resolution-in-force on the estimate labels"
```

---

### Task 4: Estimate-vs-actual delta on the batches list

**Files:**
- Modify: `backend/app/repositories/run_telemetry.py` (add `est_cost_sums_by_job`)
- Modify: `backend/app/routes/batches.py` (compute est alongside actual)
- Modify: `backend/app/templates/pages/_batches_table.html` (show est + delta)
- Test: extend `tests/integration/test_run_telemetry_repo.py`; a batches-route/render test if one exists.

- [ ] **Step 1: Failing repo test.** In `tests/integration/test_run_telemetry_repo.py` add (mirroring the existing `cost_sums_by_job` test if present):
```python
@pytest.mark.asyncio
async def test_est_cost_sums_by_job(db):
    repo = RunTelemetryRepo()
    await _insert_run(db, job_id=7, model="m", media_kind="video", status="ok",
                      cost_usd=0.20, est_cost_usd_p50=0.15)
    await _insert_run(db, job_id=7, model="m", media_kind="video", status="ok",
                      cost_usd=0.10, est_cost_usd_p50=0.05)
    sums = await repo.est_cost_sums_by_job(db, [7])
    assert sums[7] == pytest.approx(0.20)  # 0.15 + 0.05
```

- [ ] **Step 2: Run, confirm FAIL.**

- [ ] **Step 3: Add `est_cost_sums_by_job`** to `RunTelemetryRepo` — a copy of `cost_sums_by_job` summing `est_cost_usd_p50` instead of `cost_usd`:
```python
    async def est_cost_sums_by_job(
        self, conn: aiosqlite.Connection, job_ids: list[int]
    ) -> dict[int, float]:
        """{job_id: total est_cost_usd_p50}. Powers the batches-list
        estimate-vs-actual delta."""
        out: dict[int, float] = {}
        for fragment, params in chunked_in_clause((j,) for j in job_ids):
            cur = await conn.execute(
                f"SELECT job_id, COALESCE(SUM(est_cost_usd_p50), 0) "
                f"FROM run_telemetry WHERE job_id IN ({fragment}) "
                f"GROUP BY job_id",
                tuple(params),
            )
            for jid, total in await cur.fetchall():
                out[int(jid)] = out.get(int(jid), 0.0) + float(total)
        return out
```
(Confirm `chunked_in_clause` is already imported in the file — `cost_sums_by_job` uses it.)

- [ ] **Step 4: Route** `backend/app/routes/batches.py` — next to the existing `cost_sums_by_job` block, compute estimates and attach:
```python
    est_by_job = await ctx.run_telemetry_repo.est_cost_sums_by_job(ctx.db, all_job_ids)
    for v in views:
        spent = sum(cost_by_job.get(jid, 0.0) for jid in v["job_ids"])
        est = sum(est_by_job.get(jid, 0.0) for jid in v["job_ids"])
        v["cost_usd"] = spent if spent else None
        v["est_cost_usd"] = est if est else None
```

- [ ] **Step 5: Template** `backend/app/templates/pages/_batches_table.html` — the cost cell currently `<td class="bt-cost mono">{{ b.cost_usd|usd }}</td>`. Show actual with the estimate + delta when both exist (keep it compact; reuse the `usd` filter):
```html
      <td class="bt-cost mono">
        {{ b.cost_usd|usd }}
        {% if b.cost_usd is not none and b.est_cost_usd %}
          <span class="muted" title="estimated (p50) vs actual">(est {{ b.est_cost_usd|usd }})</span>
        {% endif %}
      </td>
```
(Use the existing `usd` filter and a sanctioned `.muted` class — confirm `.muted` exists; the estimate label templates already use it. Keep the delta readable; do NOT hand-roll new CSS classes — the design-language guard will catch it.)

- [ ] **Step 6: Run repo test + any batches render test + guards.**
```
.venv/bin/python -m pytest tests/integration/test_run_telemetry_repo.py -k "cost" tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py -q
.venv/bin/python -m pytest tests/ -k batches -q
```

- [ ] **Step 7: Commit.**
```bash
git add backend/app/repositories/run_telemetry.py backend/app/routes/batches.py \
        backend/app/templates/pages/_batches_table.html tests/integration/test_run_telemetry_repo.py
git commit -S -m "feat(estimator): estimate-vs-actual on the batches list"
```

---

### Task 5: Regression + guards

- [ ] **Step 1: Guards + lint.**
```bash
.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py tests/unit/test_context_delegation.py -q
.venv/bin/lint-imports
.venv/bin/python -m pytest tests/integration/test_estimate_query_count.py tests/integration/test_clips_page_perf.py -q   # N+1 guards
```
All pass; fix any PR3-introduced violation (don't edit the guard).

- [ ] **Step 2: Full suite.**
```bash
.venv/bin/python -m pytest tests/unit tests/integration -q
```
All pass. Diagnose/fix any PR3-caused failure; report anything that looks pre-existing rather than force-fixing.

- [ ] **Step 3: Commit** (only if a fix was needed).
```bash
git add -A && git commit -S -m "test: regression fixes for PR3 estimator work"
```

---

## Self-review notes (for the executor)

- **Spec coverage:** resolution-key estimator (§3) → T1+T2; estimate-vs-actual delta (§3/§5) → T4; resolution-in-force on cost surfaces (§5, moved from PR2) → T3; N+1 guard (§3) → T2 (updated count) + T5. Deferred: resolution-scaled cold-start seeds (documented scope decision).
- **Type consistency:** `media_resolution` is the `'low'|'medium'|'high'` string throughout; the repo readers and `estimate_clips` take it as `str | None` (None = no filter / unknown). The estimate dict and JS use `media_resolution`.
- **N+1:** the resolution filter is an added WHERE clause, not an extra query — per-kind query count is unchanged; the only new query is one `model_config.get` per estimate (constant). The query-count test goes 5→6 and still asserts N=10 == N=100.
- **Offline safety:** all reads are local SQLite aggregates; `default_resolution`/resolver are DB-only. No new network path.
- **JS testing gap:** Task 3 is vanilla JS with no unit runner — covered by the Task 2 API test (the endpoint returns `media_resolution`) + review + walkthrough selector validation. Call this out in the final review.
- **ADR:** if the resolution-blind-seeds decision or the estimate-vs-actual display shape warrants it, add a short ADR at the end (cross-ref the cost-prediction spec §3).
