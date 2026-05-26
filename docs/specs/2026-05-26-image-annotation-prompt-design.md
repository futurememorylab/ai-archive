# Image annotation prompt (no timestamps, identical storage/indexing)

**Date:** 2026-05-26
**Status:** Approved (design)

## Problem

With image clips now viewable and annotatable (see
`2026-05-26-image-clip-support-design.md`), running the default prompt
("Scene markers + Czech summary + era") on a still produces nonsensical
`scenes` with `in`/`out` timecodes — a still has no timeline. Image clips
need a prompt that omits scenes/timestamps while still producing the same
Czech summary + era metadata.

**Hard requirement (from the user):** image-annotation output must be
**stored and indexed identically** to video-annotation output — same
tables, same full-text index, same review-item shape — so downstream
search/indexing treats both the same. The only difference is the absence
of markers.

## Goals

- Image clips are annotated with a prompt that returns **no scenes and no
  timestamps**, only the Czech summary + decade + years.
- That output is stored and indexed **byte-for-byte the same way** as
  video output (same `annotations` row, same `annotations_fts`, same
  `review_items` kinds/targets).
- The Annotate dropdown on an image clip offers only image-appropriate
  prompts; on a video clip, only video-appropriate prompts. Generic
  (`any`) prompts show for both.
- Ship a seeded default image prompt.

## Non-goals

- Region/bounding-box annotation on images (whole-image only).
- Bulk annotation across mixed-kind selections (the per-clip flow sends a
  single `clip_id`; no mixed-kind UI exists).
- Changing the `annotations` / `annotations_fts` / `review_items` schema
  (the invariant below is satisfied without touching them).
- Changing how the existing video prompt behaves.

## The storage/indexing invariant (why the caveat is satisfied)

Annotation storage and indexing operate on structures that the image
prompt **reuses unchanged**:

- **`annotations` table** — `AnnotationsRepo.insert` writes
  `structured_output`, `raw_response`, `clip_snapshot`,
  `prompt_version_id`, etc. Generic; identical for both kinds.
- **`annotations_fts`** — populated by an `AFTER INSERT` trigger
  (migration 0001) indexing `clip_name, prompt_used, structured_output,
  raw_response`. Because the image annotation's `structured_output` holds
  the same `summary_cz`/`decade`/`years` text, it is full-text indexed
  exactly like video.
- **`review_items`** — produced by `target_map.expand`. The image prompt
  uses the **same target identifiers**: `summary_cz → note
  pragafilm.popis.materialu`, `decade → field
  pragafilm.dekáda.natočení`, `years → field pragafilm.rok.natočení`.
  Same `kind`/`target_identifier`/`proposed_value` columns, same indexes.
- **`embeddings`** — table exists but nothing currently populates it from
  annotations; when wired, it will read the same `structured_output`, so
  images are covered with no extra work.

**Invariant:** the image prompt's `output_schema` and `target_map` equal
the video prompt's **minus the `scenes → markers` entry**, with every
shared key and target byte-identical (and the timecode-only
`evidence_secs` arrays dropped). This is the single rule that guarantees
identical storage and indexing; it is enforced by the seed content, not
by code branches.

## Design

### 1. Prompt `media_kind`

Add `media_kind` to the **`prompts`** table (the stable identity, not the
per-version content): `TEXT NOT NULL DEFAULT 'any'` with
`CHECK (media_kind IN ('video','image','any'))`.

- **Migration 0011** adds the column and backfills **all existing rows to
  `'video'`** (the only seeded prompt today is the scene-marker prompt,
  which is video-only; any pre-existing custom prompts were authored
  against the scene schema, so `video` is the safe classification).
- New prompts created via the UI default to `'any'` (column default).
- `media_kind` lives on `prompts`, not `prompt_versions`: a prompt's media
  applicability is stable across its versions.

### 2. Model + repo + API

- `Prompt` model (`backend/app/models/prompt.py`) gains
  `media_kind: Literal["video","image","any"] = "any"`.
- `PromptsRepo`:
  - `create_with_initial_version(...)` gains a `media_kind="any"` param,
    written to the `prompts` row.
  - All read paths that build a `Prompt`/prompt-list dict
    (`list`, `get`, and whatever `/api/prompts` uses) select and return
    `media_kind`.
- `/api/prompts` response includes `media_kind` per prompt.

### 3. Seeded image prompt

- Add `backend/seeds/image_template.json` (full content below), with
  `"media_kind": "image"`.
- Add `"media_kind": "video"` to the existing
  `backend/seeds/default_template.json`.
- `seed_default_prompt` (`backend/app/seed.py`) passes
  `media_kind=data.get("media_kind", "any")` into
  `create_with_initial_version`.
- The FastAPI lifespan/startup seeds the image template too (a second
  `seed_default_prompt` call pointed at `image_template.json`), idempotent
  by name like the existing seeders.

Image prompt content (`backend/seeds/image_template.json`):

```json
{
  "name": "Image description + era (Czech)",
  "description": "Describes a still photograph in Czech and classifies the era. Default seeded prompt for image clips.",
  "media_kind": "image",
  "prompt": "You are annotating an archival still photograph from a Czech private archive — a digitised monochrome photo, typically 1920s–1950s. Describe the photograph in 2–4 Czech sentences (who/what/where is visible), and classify the era from visual cues (clothing, vehicles, technology). There is no video and no timeline — do not return scenes or timestamps. Return JSON matching the schema.",
  "output_schema": {
    "type": "object",
    "required": ["summary_cz", "decade", "years"],
    "properties": {
      "summary_cz": { "type": "object", "required": ["value"],
        "properties": { "value": { "type": "string" } } },
      "decade": { "type": "object", "required": ["value"],
        "properties": { "value": { "type": "string",
          "enum": ["20.léta", "30.léta", "40.léta", "50.léta", "60.léta"] } } },
      "years": { "type": "array", "items": { "type": "string" } }
    }
  },
  "target_map": {
    "summary_cz": { "kind": "note",  "target": "pragafilm.popis.materialu", "mode": "append" },
    "decade":     { "kind": "field", "identifier": "pragafilm.dekáda.natočení" },
    "years":      { "kind": "field", "identifier": "pragafilm.rok.natočení" }
  },
  "model": "gemini-2.5-pro"
}
```

### 4. Annotate dropdown filtering

- `clip_detail.html` initialises `clipAnnotate({{ clip.id }})`; add the
  clip kind: `clipAnnotate({{ clip.id }}, "{{ clip.kind }}")`.
- `clipAnnotate.js` stores `clipKind` and extends its existing
  `/api/prompts?archived=0` `.filter(...)` to also keep prompts where
  `p.media_kind === this.clipKind || p.media_kind === "any"`.
- Result: an image clip's dropdown lists only `image`/`any` prompts; a
  video clip's lists only `video`/`any`. The single-clip job flow can no
  longer submit a scene prompt against a still.

### 5. Prompt editor `media_kind` selector

- The prompt create/edit UI (prompts pages) gets a `media_kind` selector
  (`video` / `image` / `any`, default `any`), so operators can author
  kind-specific custom prompts. The create/version route + repo carry the
  value through.

## Data flow (image annotation)

```
image clip detail → Annotate dropdown (filtered to image/any prompts)
  → pick "Image description + era (Czech)"
  → POST /api/jobs {prompt_version_id, clip_ids:[id]}
  → annotator.run_job → resolve {id}.jpg → Gemini (no scenes requested;
     _render_prompt already skips the duration anchor at duration==0)
  → structured_output {summary_cz, decade, years}
  → annotations row (+ FTS trigger) ; expand → review_items (note + 2 fields)
  → identical storage/indexing to a video annotation, minus markers
```

## Error handling / edge cases

- Backfill correctness: migration sets existing prompts → `video`; a fresh
  DB seeds the video prompt as `video` and the image prompt as `image`.
- If no `image`/`any` production prompt exists, an image clip's dropdown
  shows the existing "No production prompts" empty state — acceptable; the
  seed guarantees at least the image default exists.
- `media_kind` is validated by the DB `CHECK` and the pydantic `Literal`.

## Testing

- **Migration 0011**: applying it adds `media_kind`, backfills existing
  prompts to `video`, and the `CHECK` rejects invalid values. (Follow the
  existing `tests/integration/test_migration_*.py` pattern.)
- **Seed**: `seed_default_prompt` persists `media_kind` from JSON; seeding
  the image template creates a prompt with `media_kind="image"` and a
  target_map without a `markers` entry; idempotent on re-run.
- **Repo/API**: `create_with_initial_version(media_kind=...)` round-trips;
  `/api/prompts` returns `media_kind` for each prompt.
- **Storage/indexing invariant** (the caveat): given a structured output
  `{summary_cz, decade, years}` and the image target_map,
  `target_map.expand` emits exactly one note + two field review items and
  **no marker items**; an inserted image annotation is found by
  `AnnotationsRepo.search` (FTS) on summary text — same as video.
- **Dropdown filter** (`clipAnnotate.js`): unit/DOM-level check that an
  `image` clip keeps only `image`/`any` prompts and drops `video` ones,
  and vice-versa.
- **Model**: `Prompt.media_kind` defaults to `any`; invalid value rejected.

## Out of scope / follow-up

- The `media_prefetcher` "cannot start a transaction within a transaction"
  error observed during live testing is a **separate pre-existing
  concurrency bug** (one shared aiosqlite connection across the prefetcher
  loop, request handlers, and the annotation worker). It is newly *exposed*
  because image annotation now reaches the worker, but it is not caused by
  this design. Track and fix it via systematic-debugging separately.
