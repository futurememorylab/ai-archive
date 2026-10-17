# Durable logs of human edits and generation cost

Two records we want to keep forever, for later learning:

1. **Every human change to an AI draft** (accept, reject, reset, edit), so the
   archive's annotation style can be learned from how people correct Gemini.
2. **Every Gemini call's cost**, so cost estimation can be learned from real
   spend.

Both stay in the app's SQLite database (no new service).

## Audit: what was already captured

| What | Where | Durable? |
|---|---|---|
| Gemini's raw output per annotation run | `annotations.raw_response` / `structured_output` | yes |
| Draft items (AI proposal + human edit + decision) | `review_items` | **no**: `edited_value` is overwritten per edit, and a re-run or restore `DELETE`s every un-applied row, human decisions included |
| Published state per publish, with author and diff vs parent | `clip_versions` | yes |
| CatDV write payload / response | `write_log` | yes |
| Who made a decision | nowhere | **missing** |
| Per-call tokens, cost, estimate, model, prompt hash, media facts | `run_telemetry` (annotation, studio, calibration) | yes |
| Cost of a call that returned non-JSON / truncated output | `run_telemetry`, but `cost_usd` forced to NULL | **missing**: tokens were billed, spend totals skipped them |
| Gemini Live audio sessions | nowhere (audio goes browser ↔ Google) | **missing** |
| Live session summary call | nowhere | **missing** |

## Changes

### `review_edit_log` (migration 0032)

An append-only table, one row per human change to a draft item. It is written
inside `ReviewItemsRepo.set_decision`, in the same commit as the draft update,
so every caller (the decision route, bulk "apply-batch") is covered and a
change can never land in the draft without landing in the log.

Each row carries: `occurred_at`, `author` (signed-in email), `action`
(`decision` | `bulk_accept` | `backfill`), the item (`review_item_id`, clip,
`kind`, `target_identifier`), the AI's `proposed_value`, `decision_before/after`,
`edited_before/after`, and the run context copied at write time
(`annotation_id` or `studio_run_id`, `prompt_version_id`, `model`). There is no
FK to `review_items`, because those rows are deleted on re-run.

A call that changes nothing (same decision, same value) writes no row.
The migration backfills one `backfill` row for every existing draft item a
human already touched (decision not pending, or an edited value).

### Cost of failed outputs

`_record_telemetry` now keeps `cost_usd` on an error row whenever Gemini
returned a response (non-JSON, truncated). Only a call that raised before any
response keeps NULL, since there is no usage to price. Estimator statistics are
unaffected: they read `status='ok'` rows only.

### Gemini Live (migration 0033)

`run_telemetry.kind` now allows `live` (table rebuilt; SQLite cannot alter a
CHECK). Two rows per Live session:

- `call=audio_session`: the browser sums the `usageMetadata` it receives on
  the Live WebSocket and sends it with the transcript; the server records it
  once per session (a second transcript post is ignored). The last message's
  own copy is kept in `attrs.last_usage`, so the record stays correct whether
  Gemini's per-message counts are per turn or running totals.
- `call=summary`: the server-side summary `generateContent` call's usage.

Live rows leave `media_kind` and `prompt_version_id` NULL, so they count in
spend totals (Usage tab) but never feed the annotation estimator.
`extract_usage` also reads the Live API's `responseTokenCount`.

## Not covered

- Calls that fail before a response (quota, network, auth) have no token
  counts; the row exists with `cost_usd` NULL.
- Live models are not on the rate card yet, so Live rows have tokens but
  `cost_usd` NULL until a rate is added. Tokens are stored, so cost can be
  recomputed later.
- Edits made directly in CatDV, outside this app.
- Items restored from a published version (`/restore`) carry the published
  value as `proposed_value`, not Gemini's original. Gemini's original is in
  `annotations.structured_output` for that `annotation_id`.

## Example analysis queries

```sql
-- Human corrections per prompt version (style learning)
SELECT prompt_version_id, kind, target_identifier, proposed_value, edited_after, author
FROM review_edit_log
WHERE edited_after IS NOT NULL AND decision_after = 'accepted';

-- Rejection rate per field and model
SELECT model, target_identifier,
       AVG(decision_after = 'rejected') AS reject_rate, COUNT(*) AS n
FROM review_edit_log GROUP BY model, target_identifier;

-- Estimate vs actual per model and resolution (cost learning)
SELECT model, media_resolution_setting, SUM(est_cost_usd_p50), SUM(cost_usd), COUNT(*)
FROM run_telemetry WHERE kind != 'live' GROUP BY 1, 2;
```

## Manual acceptance flows

1. Open a clip with a fresh draft. Edit a field's text, then accept it. In
   SQLite, `SELECT * FROM review_edit_log ORDER BY id DESC LIMIT 2` shows the
   edit and the accept, with your email as `author`, the prompt version and
   model filled in, and the AI's original text in `proposed_value`.
2. Reject a marker, then click Annotate again on the same clip. After the run,
   the marker's row is gone from `review_items` but its `rejected` row is still
   in `review_edit_log`.
3. On the Clips list, select a clip and use the bulk accept & apply. Its items appear in
   `review_edit_log` with `action = 'bulk_accept'`.
4. Start a Live session on a clip, talk for a few turns, stop it. In
   `run_telemetry` there is a `kind = 'live'` row with `attrs` containing
   `"call": "audio_session"` and non-zero `tokens_in`, and after the summary
   appears, a second row with `"call": "summary"`.
5. Run a Studio prompt whose output fails to parse (e.g. a schema the model
   can't satisfy). Its `run_telemetry` row is `status = 'error'`,
   `error_class = 'NonJsonOutput'`, with a non-NULL `cost_usd`, and the Usage
   tab's month spend includes it.
