# 0057. Run telemetry — local-first with deferred cloud pipeline

**Date:** 2026-06-07
**Status:** Accepted

## Context

The run-telemetry feature (spec: `docs/specs/2026-06-07-run-telemetry-cost-estimation-design.md`,
plan: `docs/plans/2026-06-07-run-telemetry-phase1.md`) instruments every
studio and annotation run with token counts, cost estimates, and prompt
identity, then uses the accumulated data for pre-call cost estimation.
Six inter-related design calls were made during implementation.

## Alternatives and Decisions

### 1. Local-first telemetry; cloud pipeline deferred

The spec described a phone-home pipeline (Cloud Run collector + BigQuery)
to aggregate cross-install telemetry. Building it now would mean operating
infra for a product still finding its shape.

**Alternatives considered:**

- Vendor GCS bucket with a create-only service-account key distributed with
  the app — rejected: key distribution is a management chore, and there is
  no server-side validation or revocation path.
- Build the Cloud Run collector now — rejected: infrastructure ahead of need.

**Decision:** Telemetry is stored only in the local SQLite DB (Phase 1).
The `run_telemetry` table carries `sent_at` and `send_attempts` columns
from day one so it doubles as the future outbound queue (outbox pattern).
When a cloud collector is eventually wired (Phase 2), a flush job can
backfill all history recorded since Phase 1 with no schema change.

### 2. One `run_telemetry` table for both run kinds

Studio runs and annotation runs both produce token counts and cost
estimates. Widening `studio_run` and `annotations` with those columns was
the path of least resistance.

**Alternative considered:** Dedicated columns on the existing tables —
rejected because telemetry has its own lifecycle (send state, schema
evolution beyond token counts) and the estimator needs a single query
path regardless of run kind.

**Decision:** A single `run_telemetry` table. Each row carries a `kind`
column (`∈ {'studio','annotation'}`) plus `clip_id` and `job_id` to
identify the run; there is no surrogate `run_id` column. Both run kinds
write one row; the estimator queries one table.

### 3. Prompt identity = SHA-256 of the template body

Cost estimation improves when rows for "the same prompt" cluster. Two
options for defining identity: the raw template body (`version.body`) or
the rendered per-clip prompt (template + interpolated clip metadata).

**Alternative considered:** Hash the rendered prompt — rejected: rendering
injects per-clip duration and other variable text, so rendered hashes
virtually never collide across clips. Cross-install dedup would be dead
on arrival.

**Decision:** `prompt_hash = sha256(version.body)` (stored as the 64-char
hex digest in the `prompt_hash` column). All clips for a given prompt
version share the same hash; cross-install comparison (Phase 2) is
meaningful.

### 4. Billable output = `candidates + thinking` everywhere

The previous `studio_run.tokens_out` stored only `candidatesTokenCount`.
`thoughtsTokenCount` bills at the output rate but was excluded.

**Alternative considered:** Track thinking separately — adds a column but
doesn't change the estimator's cost formula; the cost formula still needs
to sum them.

**Decision:** For new rows, `tokens_out = candidatesTokenCount + thoughtsTokenCount`.
Historic rows keep the old candidates-only meaning (no backfill; the
difference is small and the column carries no `sent_at` obligations).
The change is documented in the schema migration comment so future readers
know the semantic shift at the row boundary.

### 5. Estimates stamped immediately pre-call, not at enqueue

The spec (§1) implied estimates should be written when a job is enqueued.
Enqueue-time estimates would require plumbing the estimate through job
items and touching the `jobs` schema.

**Alternative considered:** Enqueue-time stamping as specified — rejected:
more schema churn for identical blindness to the actual outcome (neither
enqueue nor pre-call knows the real token count before the call).

**Decision:** `est_tokens_out` / `est_cost_usd` are stamped immediately
before the API call, inside `_process_item`. This is a deliberate
deviation from the spec wording. Serial per-job processing means clip
N+1's estimate legitimately sees clip N's actuals — that is intended
calibration, not leakage.

### 6. Budget limits deferred

The spec described a `monthly_budget_usd` config knob and a pre-flight
guard that blocks runs when projected spend exceeds the budget.

**Reason for deferral:** No admin console exists to configure the limit,
and adding it as a bare env-var would have no UI surface.

**Decision:** Deferred. The estimator already computes per-clip and
per-run projected cost via `estimate_clips` / `estimate_for_clip_ids`
in `services/run_estimator.py`. A budget guard is a thin wrapper around
`estimate_clips` + a local SUM of `run_telemetry.cost_usd` for the
current calendar month — cheap to add once the admin console exists.

## Consequences

- `run_telemetry` table is live from Phase 1; the cloud flush path
  (Phase 2) backfills without migration. The Phase-2 wire idempotency
  key is derived at send time as `"{install_id}:{id}"` — `(install_id,
  id)` is globally unique and naturally ordered with no per-insert
  UNIQUE-index overhead; no separate `event_id` column is needed.
- `studio_run.tokens_out` has a semantic boundary at the Phase 1 migration;
  the migration comment records the change.
- The estimator (`services/run_estimator.py`) queries one table for both
  run kinds, using `prompt_hash` to find comparable historical rows.
- Cost estimates use the correct billable token sum from day one; historic
  undercounts in `studio_run` are not corrected (acceptable — they are not
  used for estimates, only for dashboards that do not exist yet).
- Budget enforcement (Phase 3) is a thin addition: the estimator returns
  the estimate; the guard compares it to a configured limit. Nothing in
  Phase 1 blocks this.
- `media_width`, `media_height`, and `media_resolution_setting` are
  schema-reserved columns but are not yet populated: `CanonicalClip` does
  not carry reliable pixel dimensions today, and only one Gemini media
  resolution is in use. The estimator's image-tile arithmetic and
  resolution-aware calibration paths activate automatically once a
  dimension source is wired.
