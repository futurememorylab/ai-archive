# Accurate, resolution-aware cost prediction

**Date:** 2026-06-22
**Status:** Proposed
**Issues:** #68 (gemini token counting & media resolution) — foundation for #30 (usage & budget)

## Context

The app annotates video clips via Gemini and already captures token usage
and computed cost per run in `run_telemetry`, estimates pre-run cost in
`services/run_estimator.py`, and prices runs from a hardcoded `RATE_CARDS`
dict in `services/pricing.py`. Three gaps block both issue #68 and the
follow-on budget work (#30):

1. **The app never sets `media_resolution`.** Every Gemini call goes at the
   service default. Issue #68 asks us to empirically test how the
   `low`/`medium`/`high` resolution settings trade result quality against
   cost — impossible while the setting is neither controllable nor known
   to the estimator. The two Gemini docs the issue links describe token
   counting at two different layers: the
   [tokens page](https://ai.google.dev/gemini-api/docs/tokens) gives the
   *default* fixed rates (image 258 tokens / 768px tile, video 263 tokens/s,
   audio 32 tokens/s); the
   [media-resolution page](https://ai.google.dev/gemini-api/docs/media-resolution)
   describes the `media_resolution` *override* that changes those counts
   (e.g. Gemini 3 images at low/med/high ≈ 280/560/1120 tokens; video
   capped at 70/70/280 tokens per frame). The same clip can cost very
   different token amounts depending on model generation and resolution,
   so the estimator must become resolution-aware to stay accurate.

2. **Per-model config is hardcoded in `pricing.py`.** `RATE_CARDS` cannot
   be tuned without a deploy, and there is nowhere to store a per-model
   default resolution. There is currently **no DB table** holding per-model
   attributes — only the editable enum `gemini_generation_model` (bare
   model-name strings) and the code dict.

3. **Estimates are not as accurate as they can be, nor consistently
   surfaced.** `run_estimator` keys learned rates on `(model, kind)` and
   falls back to seed constants; it ignores resolution entirely. Users see
   a cost estimate on some surfaces but with no confidence framing, no
   resolution context, and no estimate-vs-actual feedback.

The goal of this spec (per the issue owner's direction): make cost
prediction **as accurate as possible for free**, **use it everywhere**,
and **make the user aware of it** — establishing the platform on which
#30's budget caps and always-present usage indicator are built.

## Alternatives

**Input-token prediction.** Considered (a) calling Gemini `countTokens`
before every run for exact input counts, (b) a pure analytical formula
from the documented rates, (c) empirical calibration only, (d) a hybrid.
**Decision: free hybrid (formula corrected by per-prompt/resolution
telemetry calibration) as the default path, plus an admin-enablable
`countTokens` "real cost" option** for an exact input estimate on demand.
`countTokens` is the most accurate for input but costs an API round trip
per clip and needs to be online — wrong as an always-on default, right as
an opt-in.

**Where the resolution setting lives.** Considered per-prompt-version,
global, per-run, and a per-model central store. **Decision: a per-model
default** stored centrally, optionally overridable per prompt version. A
per-model default matches the issue owner's mental model and means most
prompts need no extra configuration.

**Where per-model config is stored.** Considered keeping it in code (next
to `RATE_CARDS`), a new admin-editable DB table, or `app_meta` key-value.
**Decision: a new admin-editable `model_config` DB table**, eliminating
the hardcoded `RATE_CARDS`. A rate card is a structured record (multiple
numeric fields + resolution), so it does not fit the bare `enum_values`
table; it gets its own table and a dedicated Admin tab, seeded from the
current `RATE_CARDS` using the same reconcile-seeds + soft-delete idiom
the enums use.

**Historical cost on rate edits.** Considered snapshot-at-write,
effective-dated rate history, and live recompute. **Decision:
snapshot-at-write** — past `run_telemetry.cost_usd` is immutable (already
stored at write time); editing rates bumps `pricing_version` and only
affects future runs. Live recompute would silently rewrite past totals and
break budget/audit reproducibility.

**Quality comparison (#68 core).** Considered infra-only manual compare, a
built-in A/B view, and tagged sweep runs. **Decision: infra only** — ship
the resolution control, accurate estimation, and per-run resolution
capture; the human compares outputs across resolutions by eye. A deliberate
**calibration** action (below) lines those runs up.

## Decision

### 1. Per-model config → DB (`model_config` table)

Replace the hardcoded `RATE_CARDS` with an admin-editable table.

```
model_config(
  model TEXT PRIMARY KEY,            -- joins gemini_generation_model enum
  input_text_video_image_per_1m REAL NOT NULL,
  input_audio_per_1m REAL NOT NULL,
  input_cached_per_1m REAL NOT NULL,
  output_per_1m REAL NOT NULL,
  default_media_resolution TEXT NOT NULL,  -- 'low'|'medium'|'high'
  pricing_version TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  deleted_at TEXT                     -- soft-delete tombstone
)
```

- Seeded from today's `RATE_CARDS` at boot via a `reconcile_seeds()`-style
  pass mirroring `EnumService`, with soft-delete tombstones.
- `services/pricing.py` reads rates from this table (via a repo) instead of
  the code dict. `compute_cost()` keeps its signature; only the rate source
  moves. Pricing stays **DB-only and offline-safe** (a DB lookup, no
  network), consistent with `EnumService` on `CoreCtx`.
- **Admin "Gemini models" tab** (revised — see ADR 0114): rather than a
  *second* tab alongside the existing editable-enum tab for
  `gemini_generation_model`, the model catalog (the enum) and its per-model
  rate cards are merged into **one** tab. Its spine is the catalog (every
  selectable model); each row joins to its `model_config` rate card, which
  may be absent → a "no rate card" pill, and saving rates **creates** the
  card (an upsert). The catalog actions (make-default / enable-disable /
  delete) and the add-model row live inline; deleting a model removes it
  from both the catalog and `model_config`. The generic enum tab for that
  key is retired (the `/admin/enums/gemini_generation_model` route still
  works but is no longer linked). One row per model carries the four rate
  fields (editable) and a `default_media_resolution` dropdown (read-only in
  PR1; editable in PR2).
- Snapshot-at-write: saving a row bumps `pricing_version`
  (e.g. `edit-2026-06-22T10:30:00Z`) and `updated_at`. Existing
  `run_telemetry.cost_usd` / `pricing_version` rows are never rewritten.

### 2. `media_resolution` becomes real

- Add a **fixed** `Literal['low','medium','high']` enum `media_resolution`
  to `enums/registry.py` (`editable=False`), with a guard test pinning the
  registry values to `get_args(MediaResolution)`.
- Thread the resolution into the Gemini call in
  `services/gemini.py::annotate()` (which currently sends none), mapping to
  the SDK's `media_resolution` generation-config field.
- **Resolution resolution order** for a run: prompt-version override (if
  set) → the model's `model_config.default_media_resolution`.
- Persist the value used into the existing
  `run_telemetry.media_resolution_setting` column (reserved, currently
  NULL).
- Add an optional `media_resolution` override field to `PromptVersion`
  (nullable; null = use model default). Versioned with the prompt, so a
  prompt can pin a resolution reproducibly.

### 3. Resolution-aware estimator (free) + admin-enablable real cost

**Free default path (`run_estimator.py`)** *(shipped — PR3)*:
- Key the **learned rates** on `(model, kind, resolution)` instead of
  `(model, kind)`. The fallback chain became:
  prompt-hash + model + kind + resolution → model + kind + resolution →
  **seed constants (resolution-blind, see below)**.
- **Scope decision (ADR 0115):** the cold-start *seed constants* stay
  resolution-blind — only the *learned history* is resolution-keyed. Once
  ≥3 runs exist at a resolution, that resolution's real token rates take
  over; before then the (already-`rough`) seed estimate is resolution-blind.
  This softens the original "resolution-scaled seed constants" wording: a
  per-model-per-resolution seed table is fragile and generation-specific,
  and only affects the zero-history case. The history-keying is where the
  accuracy actually comes from.
- Input estimate = analytical formula corrected by the learned
  per-prompt/resolution multiplier from telemetry. Output estimate stays
  statistical (p50/p90) from history — output tokens cannot be known
  pre-call.
- This is **not a new estimation path** — it extends the existing chain and
  reuses `run_telemetry`. No parallel estimator. The effective resolution is
  resolved server-side in the estimate endpoint (same resolver as a run) and
  returned to the UI.
- Confidence (`rough`/`fair`/`good`) is now per `(prompt, resolution)`. A
  resolution with no samples reports `rough` until calibrated or exercised.

**Admin-enablable "real cost" estimate (`countTokens`):**
- An admin toggle (stored in `app_meta`, e.g. `real_cost_estimate=on`) gates
  *availability*. Default **off**.
- When enabled **and online**, the **batch-creation flow only** shows a
  "Get real estimate (uses API)" button. Clicking it calls Gemini
  `countTokens` for the batch's clips at the resolved resolution and shows
  **exact input cost** + the still-statistical output estimate, clearly
  labelled (`$X in (exact) + ~$Y out (est)`).
- It is **on-demand only** (never auto-fires on render — honors the
  no-eager-fetch-on-page-render rule), and **only on the batch-creation
  flow** (not the clips list or studio runs).
- Offline or clip-not-in-AI-store → the button is disabled with a clear
  reason; the free estimate is always shown as the baseline. Honors the
  cache-layer offline contract (graceful miss, names which layer is
  unavailable).
- This doubles as a validator for the free estimator (compare free input
  estimate vs `countTokens` ground truth).

### 4. Deliberate calibration (Admin "Prompts" tab)

- New **Admin "Prompts" tab** listing prompt versions, each with a
  **Calibrate** action.
- The **admin picks the 3 calibration clips** in the Calibrate dialog
  (representative of the footage that prompt runs on) — a fixed seeded set
  would not represent each prompt's real input. The selection reuses the
  existing clip-picker pattern.
- Calibrate runs **3 clips × {low, medium, high} × 2 repeats = 18 runs**
  through the existing annotator/batch machinery, showing the **projected
  calibration cost** (from the very estimator being calibrated) before the
  admin confirms.
- Repeats tighten the output-token variance; the sweep populates
  per-`(prompt, resolution)` history — the **only** way to seed estimates
  for the non-default resolutions a prompt would otherwise never exercise.
- Writes **ordinary `run_telemetry` rows** — it improves the *same*
  estimator, no separate store. A results panel shows **cost-by-resolution**
  and the **estimator confidence unlocked** (rough → fair → good). Output
  quality across resolutions is judged manually from the lined-up outputs.

### 5. "Used everywhere" + "user knows"

The resolution-aware free estimate replaces seed-based estimates on **every
existing cost surface**: the batch-creation modal, the batches list, the
clips page, and studio runs. Each surface shows:

- the **resolution in force** for the run,
- the estimate **with its confidence** (`rough`/`fair`/`good`),
- after the run, the **estimate-vs-actual delta**.

The always-present global usage indicator remains scoped to #30; this spec
lays its data foundation (accurate per-run cost, captured resolution,
DB-sourced pricing).

### 6. Guards / tests

- Registry↔`Literal` guard for `media_resolution`.
- A test pinning the `model_config` seed reconcile to the previous
  `RATE_CARDS` values (no silent pricing drift on migration).
- Query-count guard on the resolution-keyed estimator reads (no N+1 as
  clip count grows), per the performance discipline.
- `pricing.py` now DB-sourced and offline-safe — a test asserting
  `compute_cost` works with the DB repo and no network.
- Walkthrough scenario(s) updated/added for the new Admin "Models" and
  "Prompts" tabs, the resolution display on cost surfaces, and the
  (admin-enabled) real-cost button on the batch flow.

### 7. Implementation order (4 PRs)

The spec ships as four independently shippable, ordered slices:

1. **PR1 — Pricing to the DB.** `model_config` table + migration + repo +
   reconcile-from-`RATE_CARDS` seeds; `pricing.py` reads the DB; Admin
   "Models" tab; seed-drift guard test. No change to run behaviour.
2. **PR2 — Resolution becomes real** *(shipped)*. `media_resolution` fixed
   enum; thread into `gemini.py::annotate()`; nullable `PromptVersion`
   override (resolved override → model default → `medium`, invalid values
   ignored); capture into `run_telemetry.media_resolution_setting`; the
   admin "Gemini models" tab's default-resolution column became an editable
   dropdown. **Two pieces were deliberately moved out of PR2:** (a) "show
   the resolution in force on cost surfaces" → **PR3** (the estimate label
   is rendered from the estimator response, which PR3 makes
   resolution-aware — adding the label there avoids a half-wired step ahead
   of the thing that computes it); (b) the **prompt-editor UI control** for
   setting the per-prompt override → a later UI slice (PR2 wired the
   override end-to-end at the data/engine level — settable + tested via the
   version repo API; the per-*model* default is user-settable now via the
   admin dropdown).
3. **PR3 — Accurate estimates everywhere** *(shipped)*. Resolution-key the
   `run_estimator` learned-history chain (seeds stay resolution-blind, ADR
   0115); estimate-vs-actual delta on the batches list; show the resolution
   in force on the pre-run estimate labels (studio + batch modal); the N+1
   query-count guard updated (constant +1 model_config read, N=10==N=100).
4. **PR4 — Calibration + real cost.** Split into two slices:
   - **PR4a — Calibration** *(shipped — ADR 0116)*. Admin "Prompts" tab; pick
     3 clips → projected-cost confirm → a sweep of 3 resolutions × 2 repeats ×
     3 clips = 18 telemetry-only runs (`record_only` + `force_resolution` on the
     run path — no annotations/studio-runs/review-items written); per-resolution
     results panel (count / cost / confidence unlocked).
   - **PR4b — Real cost** *(pending)*. Admin-enablable `countTokens` real-cost
     button on the batch-creation flow.

## Consequences

- **Positive:** pre-run estimates become resolution-aware and improve with
  every run (organic or calibration); operators tune pricing and resolution
  without a deploy; the data foundation for #30 (budget caps, usage
  indicator) is in place; #68's empirical question is answerable (run a clip
  set at low/med/high, compare captured cost + outputs).
- **Cost:** calibration and "real cost" estimates are real Gemini calls;
  both surface their projected cost before spending and are bounded /
  opt-in.
- **Migration:** moving `RATE_CARDS` to the DB adds a table, repo, reconcile
  pass, and Admin tab; the seed test guards against pricing drift. Past
  telemetry is untouched (snapshot-at-write).
- **Negative / accepted:** "real cost" nails only the input side; output
  remains statistical. This is labelled, not hidden. No built-in quality A/B
  view — quality is judged manually (deliberate scope choice).

## Manual acceptance flows

1. **One merged "Gemini models" tab — catalog + pricing (ADR 0114).**
   Setup: app running, signed in as admin, open `/admin` → "Gemini models"
   tab. There is exactly ONE Gemini-model tab (no separate "Gemini
   generation models" enum tab).
   Actions: (a) change `gemini-2.5-flash-lite`'s `output_per_1m`; save;
   reload — the new value persists. (b) Find an unpriced catalog model
   (e.g. `gemini-3.5-flash`): it shows a "no rate card" pill; enter rates
   and save — the pill disappears and the card now exists. (c) Add a model
   via the add-row, then delete it — it leaves both the catalog and
   `model_config`. (d) Make a different model the default — the star moves.
   Expected: all four behaviours hold; the other (non-Gemini) editable-enum
   tabs are unchanged.

2. **Resolution is applied and captured.**
   Setup: a prompt with no resolution override, its model defaulting to
   `high` (from flow 1). Run a one-clip batch.
   Expected: the Gemini call is made at `high`; the resulting
   `run_telemetry` row has `media_resolution_setting = 'high'`; the batch
   surfaces show "high" as the resolution in force.

3. **Per-prompt override beats the model default.**
   Setup: edit a prompt version, set its `media_resolution` override to
   `low`. Run a one-clip batch.
   Expected: the run executes at `low` (override wins over the model's
   `high` default); telemetry records `low`.

4. **Free estimate is resolution-aware and confidence-framed.**
   Setup: open the batch-creation modal for a clip set, with the prompt's
   resolution set to `medium`.
   Expected: the modal shows a cost estimate, the resolution `medium`, and a
   confidence label; switching the prompt's resolution to `high` (and
   reopening) changes the estimate.

5. **Estimate-vs-actual feedback.**
   Setup: run a batch for which a pre-run estimate was shown.
   Expected: after completion, the batch view shows the actual cost and the
   delta vs the estimate.

6. **Calibration seeds the estimator (Prompts tab).**
   Setup: a brand-new prompt version with no run history; open `/admin` →
   "Prompts" tab. Actions: click **Calibrate**; confirm at the shown
   projected cost.
   Expected: 18 runs execute (3 clips × low/med/high × 2); a results panel
   shows cost-by-resolution; reopening the batch-creation modal for that
   prompt now shows a higher-confidence estimate (rough → fair/good), and
   the non-default resolutions now have estimates too.

7. **Admin-enablable real cost (batch flow only).**
   Setup: in `/admin`, enable "real cost estimate". Open the batch-creation
   flow for a clip set whose clips are in the AI store, online.
   Actions: click "Get real estimate (uses API)".
   Expected: the surface shows exact input cost + estimated output cost,
   labelled as such. With the toggle off, the button is absent. Offline (or
   for clips not in the AI store), the button is disabled with a clear
   reason and the free estimate still shows. The button appears on the
   batch-creation flow only — not on the clips list or studio runs.

8. **Pricing edits do not rewrite history.**
   Setup: note an existing completed batch's cost. In "Models", change that
   model's rates and save.
   Expected: the old batch's cost is unchanged; a new run uses the new rates
   and records the bumped `pricing_version`.
