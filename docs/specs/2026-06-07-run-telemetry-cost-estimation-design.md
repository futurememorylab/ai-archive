# Run Telemetry & Cost Estimation — Design

**Date:** 2026-06-07
**Status:** Approved design, Phase 1 scoped for implementation

## Overview

Capture complete, queryable usage data (tokens, cost, media and prompt
attribution) for every Gemini run, and use that history to show an
honest pre-run cost estimate before launching studio or annotation
jobs. The data model is event-shaped so a future phone-home pipeline
(Phase 2) can ship every record collected since day one to a
vendor-run collector without a schema rewrite.

### Goals

1. **Pre-run cost estimate in the UI** — accurate enough to ground
   customer quotes, shown as a p50–p90 range with a stated confidence.
2. **Correct historical capture** — today the studio path undercounts
   billable output (thinking tokens missing) and `cost_usd` is a
   hardcoded `0.0`; the annotation path buries usage in `raw_response`
   JSON with no queryable columns.
3. **Future-proof attribution** — every record stands alone (media
   descriptors, prompt hash, archive id, install id) so it stays
   meaningful even when read outside the install that produced it.

### Non-goals (Phase 1)

- No cloud transmission: no flusher, collector service, BigQuery, or
  telemetry credentials. The local table doubles as the outbox so
  Phase 2 backfills everything.
- No budget enforcement (`monthly_budget_usd` warn/block) — deferred
  until an admin console exists to configure it.
- Not billing-grade: estimates and capture inform quotes and
  reporting; they do not reconcile invoices.
- No `count_tokens` pre-flight calls and no remote rate-card fetching.

## Background: current state

- `studio_run` has `tokens_in`, `tokens_out`, `cost_usd` columns
  (migration `0013_studio.sql`). `_finalize_studio`
  (`services/annotator.py`) fills tokens from
  `usageMetadata.promptTokenCount` / `candidatesTokenCount`;
  `cost_usd` is hardcoded `0.0`.
- `candidatesTokenCount` excludes `thoughtsTokenCount` (Gemini 2.5
  thinking tokens, billed at the output rate) — `tokens_out` is wrong
  whenever thinking is active.
- `_finalize_annotation` stores the full `raw_response` (containing
  `usageMetadata`) but extracts nothing.
- `GeminiService.annotate` already returns `response.model_dump()` as
  `result["raw"]`, so all usage fields are available at finalize time
  with no SDK changes.
- `media_kind.py` classifies image vs. time-based only (by extension).
- There are no app users and no admin console.

## Phase 1 design

### 1. Capture fix (`services/annotator.py`, both finalize paths)

Extract from `usageMetadata` at finalize time, in **both**
`_finalize_studio` and `_finalize_annotation`:

| Field | Source |
|---|---|
| `tokens_in` | `promptTokenCount` |
| `tokens_in_text` / `_video` / `_audio` / `_image` | `promptTokensDetails` per-modality breakdown |
| `tokens_out` | `candidatesTokenCount` |
| `tokens_thinking` | `thoughtsTokenCount` |
| `tokens_cached` | `cachedContentTokenCount` |
| `finish_reason` | first candidate's `finishReason` |

`studio_run.tokens_out` changes meaning to **billable output**
(`candidatesTokenCount + thoughtsTokenCount`) so the studio UI stops
under-reporting. `studio_run.cost_usd` is computed via the pricing
module (section 5) instead of `0.0`.

Field names in `usageMetadata` may be camelCase or snake_case
depending on SDK serialization; the extractor must accept both (the
existing code reads camelCase from `model_dump()` — verify against a
real response during implementation).

### 2. Migration `0016_run_telemetry.sql`

One row per model call, written by both finalize paths. Proper columns
(the estimator queries them); the Phase 2 wire envelope is a
serialization concern, not a storage one.

```sql
CREATE TABLE app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- install_id: uuid4 generated on first startup, stored here.

CREATE TABLE run_telemetry (
  id                    INTEGER PRIMARY KEY,
  occurred_at           TEXT NOT NULL,          -- UTC ISO, finalize time
  install_id            TEXT NOT NULL,
  app_version           TEXT,
  kind                  TEXT NOT NULL CHECK (kind IN ('studio','annotation')),

  -- attribution
  archive_id            TEXT,                   -- provider name + host, e.g. 'catdv:192.168.1.41'
  user_ref              TEXT,                   -- NULL until app auth exists
  job_id                INTEGER,
  clip_id               INTEGER,
  clip_name             TEXT,

  -- prompt identity
  prompt_version_id     INTEGER,
  prompt_hash           TEXT,                   -- sha256 of TEMPLATE body (version.body)
  schema_hash           TEXT,                   -- sha256 of response schema JSON
  prompt_chars_rendered INTEGER,                -- rendered prompt length
  model                 TEXT NOT NULL,

  -- media descriptors (event must stand alone; clip_id may be junk elsewhere)
  media_kind            TEXT,                   -- image|audio|video|video+audio
  media_duration_secs   REAL,
  media_width           INTEGER,
  media_height          INTEGER,
  media_fps             REAL,
  media_bytes           INTEGER,
  media_ext             TEXT,
  media_resolution_setting TEXT,                -- gemini mediaResolution (default today)
  preprocess            TEXT,                   -- NULL now; e.g. 'img-compress-v1' later

  -- provider context
  vertex_project        TEXT,
  vertex_location       TEXT,
  ai_store_kind         TEXT,                   -- gcs | gemini-files

  -- outcome + usage actuals
  status                TEXT NOT NULL CHECK (status IN ('ok','error')),
  error_class           TEXT,                   -- exception class name only, never the message
  finish_reason         TEXT,                   -- STOP | MAX_TOKENS | SAFETY | ...
  attempt_count         INTEGER,
  duration_s            REAL,                   -- wall clock of the model call
  tokens_in             INTEGER,
  tokens_in_text        INTEGER,
  tokens_in_video       INTEGER,
  tokens_in_audio       INTEGER,
  tokens_in_image       INTEGER,
  tokens_cached         INTEGER,
  tokens_out            INTEGER,                -- candidatesTokenCount (raw)
  tokens_thinking       INTEGER,
  cost_usd              REAL,                   -- NULL when model unknown to rate card
  pricing_version       TEXT,

  -- estimate at enqueue (est-vs-actual is one query)
  est_tokens_in         INTEGER,
  est_tokens_out_p50    INTEGER,
  est_tokens_out_p90    INTEGER,
  est_cost_usd_p50      REAL,
  est_cost_usd_p90      REAL,
  est_confidence        TEXT,                   -- good | fair | rough

  -- output shape ("what the customer got")
  output_chars          INTEGER,
  review_item_count     INTEGER,

  -- forward-compat + dormant Phase 2 outbox
  attrs                 TEXT,                   -- JSON, escape valve for future fields
  sent_at               TEXT,                   -- dormant until Phase 2 flusher
  send_attempts         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_run_telemetry_estimator
  ON run_telemetry (prompt_hash, model, media_kind, status);
CREATE INDEX idx_run_telemetry_unsent
  ON run_telemetry (sent_at) WHERE sent_at IS NULL;
```

Rows are kept forever (~1 KB/run); pruning would destroy estimator
history. A repository (`repositories/run_telemetry.py`) owns inserts
and the estimator's aggregate reads. The telemetry insert must
**never fail the run**: wrap in `try/except Exception`, log, continue.

Deliberately **not** widening `annotations`: telemetry has its own
lifecycle and one table gives the estimator a single query path across
both run kinds.

### 3. `media_kind.py` extension

Extend the existing single-source-of-truth classifier from
image-vs-rest to `image | audio | video | video+audio`:

- `image`: existing `IMAGE_EXTS` logic, unchanged behavior for
  existing callers (`is_image_path` stays).
- `audio`: extension set (`.wav .mp3 .aac .m4a .flac .aiff .ogg`).
- `video` vs `video+audio`: CatDV clip metadata where it reports audio
  presence; default to `video+audio` when unknown (the conservative
  estimate — overestimates by ~10%, never under).

### 4. Prompt hashing

`sha256(version.body)` — the **template**, not the rendered prompt
(`_render_prompt` injects per-clip duration text; hashing rendered
output would make every clip's hash unique and kill dedup/keying).
`schema_hash` = sha256 of the canonical-JSON-serialized response
schema. Computed once per finalize, stored on the row. Keys the
estimator's history so it survives prompt-version renumbering, and is
the Phase 2 cross-install dedup key.

### 5. Pricing module (`services/pricing.py`)

In-repo rate card, per model:

```python
RateCard(
    input_text_video_image_per_1m: float,
    input_audio_per_1m: float,
    input_cached_per_1m: float,
    output_per_1m: float,            # candidates + thinking, same rate
    tier_threshold_tokens: int | None,   # 2.5 Pro's 200k split
    rates_above_tier: ... | None,
    source_url: str,
)
PRICING_VERSION = "2026-06"   # bump when rates change
```

One pure function:

```python
compute_cost(usage: TokenUsage, model: str) -> tuple[float | None, str]
```

- Unknown model → `(None, PRICING_VERSION)` + a logged warning. Never
  raises; tokens are stored regardless so cost is recomputable later.
- **Rates must be read from the current Vertex AI pricing page at
  implementation time** — the `source_url` per entry records where.
  Do not trust remembered numbers.
- Rate-card updates ship with app releases.

### 6. Estimator service (`services/run_estimator.py`)

**Input tokens — deterministic, branched on `media_kind`:**

| Kind | Formula |
|---|---|
| video+audio | `duration × k_va` (seed ~300 tok/s default res) |
| video | `duration × k_v` (seed ~258 tok/s) |
| audio | `duration × k_a` (seed ~32 tok/s) |
| image | `ceil(w/768) × ceil(h/768) × 258`; unknown dims → 1 tile |
| always add | `prompt_chars_rendered/4 + schema_chars/4` |

Seed constants live in the estimator module and **self-calibrate**:
median of `tokens_in_<modality> / media_duration_secs` over the most
recent ≤50 `status='ok'` rows per (model, media_kind,
media_resolution_setting). Calibration automatically absorbs future
changes (image compression, resolution settings) with no estimator
rewrite. The exact seed values must be sanity-checked against one real
run's actuals during implementation.

**Output tokens — a distribution, never a point.** All output
statistics use **billable output** = `tokens_out + tokens_thinking`
(thinking bills at the output rate; estimating from candidates alone
would systematically under-quote). Fallback chain; first level with
≥3 samples wins:

1. `(prompt_hash, model, media_kind)` — p50/p90 of
   output-tokens-per-media-second (per-image for stills)
2. `(model, media_kind)` global
3. seed constant → confidence `rough`

Rows with `finish_reason = 'MAX_TOKENS'` are **excluded** from output
statistics (truncated runs drag estimates down — the wrong direction
for quotes). Error rows are excluded from all statistics.

**API:**

```python
async def estimate_run(db, clips, prompt_version) -> RunEstimate
# RunEstimate: tokens_in, tokens_out_p50, tokens_out_p90,
#              cost_usd_p50, cost_usd_p90, confidence, per_clip[]
```

Confidence: `good` = level-1 match with ≥10 samples, `fair` = ≥3
samples at level 1 or 2, `rough` = seed fallback. All reads are
aggregate SQL over `run_telemetry` — one query per fallback level, not
per clip (N+1 guard applies; see Testing).

The estimator is fully offline: no network calls, ever.

### 7. UI: estimate at run launch

Surfaces: the **studio run flow** and the **annotation job launch
flow** (wherever a set of clips + a prompt version is confirmed before
running). New endpoint:

```
POST /api/runs/estimate   {clip_ids: [...], prompt_version_id: N}
  -> {tokens_in, cost_usd_p50, cost_usd_p90, confidence, n_samples, per_clip[]}
```

Rendered line (shared `_ui.html` conventions, tokens not raw hex, a
small `fmtUsd` helper in `format.js` alongside `fmtBytes`):

> Estimated: **$0.42 – $0.68** · 12 clips · ~3.1M tokens in ·
> confidence: good (84 prior runs)

Failure to estimate (e.g. endpoint error) renders nothing and never
blocks the run button — estimation is advisory. The chosen estimate is
stamped into each run's `est_*` columns at enqueue so est-vs-actual
accuracy is a single SQL query.

### Settings additions

| Setting | Default | Note |
|---|---|---|
| *(none required for Phase 1)* | | `install_id` is generated, not configured; archive/vertex context comes from existing settings |

No telemetry endpoint/token/mode settings until Phase 2.

## Phase 2 (designed, deferred)

**Trigger: first external customer deployment is scheduled.** Nothing
in Phase 1 blocks it; the local table-as-outbox backfills all history
recorded since Phase 1 shipped.

- **Wire format:** envelope `{event_type, schema_version,
  occurred_at, install_id, customer_id, app_version, attrs}` + typed
  payload. `run_telemetry` rows serialize to `event_type='run'`.
  The wire idempotency key is derived at send time as
  `"{install_id}:{id}"` — `(install_id, id)` is already a globally
  unique, naturally ordered key with no per-insert UNIQUE-index overhead.
  `event_type='prompt_version'` is emitted once per locally-new prompt
  version (hash, chars, model always; name/body/schema only in `full`
  mode) — exact prompt text crosses the wire once per install, runs
  reference it by hash.
- **Redaction policy:** `telemetry_mode: full | redacted` —
  starred/sensitive fields (`clip_name`, prompt body/name, `user_ref`)
  nulled in `redacted`. Schema is always the superset; trust upgrades
  are config changes.
- **Client flusher:** background task (sync_engine idiom): batches
  `sent_at IS NULL` rows, POSTs with per-customer bearer token,
  exponential backoff capped ~1h, retries forever, silent, never
  blocks the product. Kill switch `telemetry_enabled` (outbox still
  accumulates locally when off).
- **Collector:** small FastAPI app in this repo (`collector/`),
  deployed to Cloud Run (scale-to-zero; effectively $0 at this
  volume). Validates envelope strictly, stores unknown payloads
  permissively. Token→customer_id mapping is server-side; client
  claims are ignored. Inserts to BigQuery `telemetry.events`
  (envelope columns + JSON payload, partitioned by date, clustered by
  customer/event_type); views dedupe on the derived `{install_id}:{id}`
  key and unpack hot types (`runs`, `prompt_versions`,
  `estimate_accuracy`).
- **Budget limits:** `monthly_budget_usd` + `budget_mode: off | warn |
  block` — month-to-date SUM over local telemetry + the new run's
  estimate; confirm dialog or refusal. Deferred until an admin console
  exists to configure it. Server-side cross-install limits come later
  still.

## Error handling

- Telemetry insert failures: `except Exception` → log → continue; a
  run must never fail because bookkeeping did.
- `usageMetadata` absent/partial: store zeros/NULLs, note the anomaly
  in `attrs`; never raise.
- Unknown model in rate card: `cost_usd = NULL`, logged warning.
- `error_class` stores the exception **class name only** —
  messages can contain paths/hosts and belong in logs, not telemetry.
- Estimator with empty history: seed constants, `rough` confidence —
  never an error.
- Estimate endpoint failure: UI omits the estimate line; run launch
  unaffected.

## Testing

TDD throughout (house rule). Key cases:

- **Pricing:** known model math (incl. modality split + cached +
  thinking), unknown model → `(None, version)`, tier split if a tiered
  model is in the card.
- **Capture:** both finalize paths write a `run_telemetry` row;
  thinking tokens included in `studio_run.tokens_out`; camelCase and
  snake_case `usageMetadata` both parse; missing usage → zeros + run
  still finalizes.
- **Prompt hashing:** template-not-rendered (two clips, same version →
  same hash); schema change → new `schema_hash`.
- **media_kind:** extension matrix incl. unknown → `video+audio`
  default; existing `is_image_path` callers unaffected.
- **Estimator:** branch per media kind; fallback chain order;
  MAX_TOKENS and error rows excluded; calibration shifts `k` after
  seeded history; zero history → rough.
- **N+1 guard:** `assert_query_count` on `estimate_run` — same
  statement count for 10 vs 100 clip ids (ADR 0046 pattern).
- **Telemetry insert failure:** patched repo raising → run finalizes
  `ok`, error logged.

## Manual acceptance flows

1. **Estimate appears with zero history.** Setup: fresh DB (no
   `run_telemetry` rows), studio folder with ≥2 video clips, any
   prompt version. Action: open the studio run flow, select the clips.
   Expected: an estimate line renders with a $ range and
   `confidence: rough`; the Run button is enabled regardless.
2. **Studio run records correct telemetry.** Setup: flow 1 completed;
   CatDV + GCS online. Action: run the studio job; after completion
   inspect `run_telemetry` (e.g. `sqlite3 data.db "SELECT kind, model,
   tokens_in, tokens_out, tokens_thinking, cost_usd, media_kind,
   prompt_hash FROM run_telemetry ORDER BY id DESC LIMIT 1"`).
   Expected: one row per clip, `kind='studio'`, non-zero `tokens_in`,
   `cost_usd` non-NULL and plausible, `prompt_hash` 64 hex chars,
   `media_kind` correct; the studio UI's token display still works and
   now includes thinking tokens.
3. **Annotation run records telemetry too.** Setup: an annotation
   (non-studio) job for ≥1 clip. Action: run it. Expected: a
   `run_telemetry` row with `kind='annotation'` and the same field
   quality as flow 2; annotation review UI unaffected.
4. **Confidence improves with history.** Setup: flows 2–3 done ≥3
   times with the same prompt version on video clips. Action: open the
   run flow again with that prompt. Expected: confidence is `fair` or
   `good`, the range is tighter than flow 1, and the line shows the
   prior-run count.
5. **Estimates work offline.** Setup: stop/disconnect CatDV (or run
   with the offline provider). Action: open the run flow for cached
   clips. Expected: the estimate line still renders from local history
   — no network errors, no missing UI.
6. **Failed run is captured as error.** Setup: force a model failure
   (e.g. invalid model name on a prompt version). Action: run.
   Expected: `run_telemetry` row with `status='error'`,
   `error_class` set to the exception class name, `cost_usd` NULL or
   0; the job UI shows its normal error state.
7. **Adjacent surfaces still work.** Action: browse clips list, open a
   clip detail, open an old studio run. Expected: no regressions —
   pages render, token counts on old studio runs still display
   (historic rows keep their old `tokens_out` meaning; only new rows
   include thinking).
