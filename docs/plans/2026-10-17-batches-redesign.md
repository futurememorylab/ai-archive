# Plan: batches redesign

Spec: `docs/specs/2026-10-17-batches-redesign-design.md`. Each step is test
first: write the failing test, run it red, implement, run it green.

1. **Migration + repo name.** `0032_jobs_batch_name.sql` (column + backfill).
   Tests: migration backfills single-clip, multi-clip run-group and
   uncached-clip batches; studio jobs stay NULL. `JobsRepo.create_job(...,
   batch_name=)` stores it; `rename_batch(job_ids, name)` updates every job;
   `list_batches` returns `batch_name` from the primary job.
2. **Default name on create.** `services/batch_names.py`:
   `default_batch_name(clip_names, n)` and `clean_batch_name()`. `POST
   /api/jobs` accepts `batch_name`; without one it derives it from
   `clip_cache`. Tests: unit (rules, trim, 120 cap, empty → error) and route.
3. **Rename route.** `POST /batches/rename {job_ids, name}` (`run`
   permission) → `{name}`; 422 on an empty name.
4. **Thumbnails query.** `JobsRepo.batch_preview_clips(job_ids_by_batch)`:
   first 5 clip ids + distinct clip count per batch in one statement
   (`chunked_in_clause`). Query-count test; extend the batches perf test.
5. **View model.** `batch_view` gains `name`, `section`, `headline`,
   `segments`, `legend`, `thumbs`, `more`. Unit tests per state (running,
   to review, failed only, write-back problem, syncing, stopped, done).
6. **Batch clip list.** `JobsRepo.batch_clip_rows(job_ids)` (one query:
   item status, error, awaiting review, write-back state, clip name) +
   `GET /batches/clips` rendering `_video_list.html`. Tests: route renders
   names, statuses, error text, ordering, the 50 cap and the search link.
7. **Templates + CSS.** Rewrite `_batches_table.html` (sections, rows,
   stack, rename, expand), trim the metric strip, add the Batch name field
   to the New batch modal and the search-page Annotate modal. CSS reuses
   `.thumb`, `.btn`, `.vlist`, tokens only. Route tests for the rendered
   markup.
8. **Search column.** "Drafts" → "Annotation" with the new labels; Batch
   column and filter show names (`JobsRepo.batch_names_for_jobs`).
   Update `test_clips_status_badge.py` and `test_batch_options.py`.
9. **Walkthroughs.** Update `batch-review-apply` and
   `cancel-running-batch` to the new markup and wording; add
   `batch-name-and-contents` (stack, rename, Show clips, the name in
   search). Run `/e2e` in assert mode.
10. **Gates.** ruff, format, lint-imports, erosion gate, `pytest -n auto`.
