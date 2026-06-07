# Run Telemetry & Cost Estimation (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture complete per-run Gemini usage (tokens incl. thinking, modality split, cost) into a local `run_telemetry` table and surface a pre-run cost estimate (p50–p90 range + confidence) in the batches modal and studio header.

**Architecture:** A pure capture module (`telemetry_capture.py`) turns a finished Gemini response into a `RunTelemetryRecord`; the annotator inserts it via `RunTelemetryRepo` on every finalize (ok and error). A pure pricing module computes `cost_usd`. The estimator reads aggregate history from the same table (deterministic input formula + history-derived output distribution). The table carries dormant outbox columns (`sent_at`, `send_attempts`) for the deferred Phase 2 cloud pipeline.

**Tech Stack:** Python 3.12/3.13, FastAPI, aiosqlite, pydantic, Alpine.js (no build step), pytest + pytest-asyncio.

**Spec:** `docs/specs/2026-06-07-run-telemetry-cost-estimation-design.md`

**Branch:** `feat/run-telemetry-phase1` (already created; spec committed).

**Known deviation from spec (record in ADR at the end):** the spec says est_* fields are stamped "at enqueue". We stamp them in `_process_item` *immediately before the Gemini call* instead — same blindness to the outcome, but no `jobs` schema change and no plumbing of estimates through job items. The UI estimate (batches modal / studio header) is computed separately at selection time and is display-only.

**House rules that bind every task:** TDD (failing test first), `except Exception` never `BaseException`, no sync fs I/O in `async def`, repos never import services, routes never import httpx, batched reads via `chunked_in_clause` for key lists.

---

## File structure

| File | Status | Responsibility |
|---|---|---|
| `backend/migrations/0016_run_telemetry.sql` | create | `app_meta` + `run_telemetry` tables + indexes |
| `backend/app/models/telemetry.py` | create | `RunTelemetryRecord`, `TelemetryCtx` |
| `backend/app/media_kind.py` | modify | add audio exts + `classify_media_kind()` |
| `backend/app/services/telemetry_capture.py` | create | `TokenUsage`, `extract_usage`, `extract_finish_reason`, `prompt_hash`, `schema_hash` |
| `backend/app/services/pricing.py` | create | `RateCard`, `RATE_CARDS`, `PRICING_VERSION`, `compute_cost` |
| `backend/app/repositories/app_meta.py` | create | `get_or_create_install_id` |
| `backend/app/repositories/run_telemetry.py` | create | `RunTelemetryRepo`: insert + aggregate reads |
| `backend/app/repositories/clip_cache.py` | modify | add batched `get_many_by_ids` |
| `backend/app/services/run_estimator.py` | create | `ClipEstimateInput`, `RunEstimate`, `estimate_clips`, `estimate_for_clip_ids` |
| `backend/app/services/annotator.py` | modify | capture + record in both finalize paths; pre-call estimate; billable tokens_out fix |
| `backend/app/context.py` | modify | `run_telemetry_repo` field + `telemetry_ctx` + LiveCtx delegators |
| `backend/app/routes/jobs.py` | modify | thread new args into `run_job`; `POST /api/jobs/estimate` |
| `backend/app/routes/studio.py` | modify | thread new args into `run_job` |
| `backend/app/static/format.js` | modify | `fmtUsd` |
| `backend/app/templates/pages/batches.html` | modify | estimate line in new-batch modal |
| `backend/app/static/studioStore.js` + `backend/app/templates/pages/_studio_header.html` | modify | estimate next to Run button |
| `docs/adr/00XX-run-telemetry-local-first.md` + `docs/decisions.md` | create/modify | record design calls |

Tests:

| File | Covers |
|---|---|
| `tests/integration/test_run_telemetry_migration.py` | migration + install_id |
| `tests/unit/test_media_kind_classify.py` | classifier matrix |
| `tests/unit/test_telemetry_capture.py` | usage extraction + hashing |
| `tests/unit/test_pricing.py` | cost math |
| `tests/integration/test_run_telemetry_repo.py` | insert + aggregates |
| `tests/integration/test_annotator_telemetry.py` | end-to-end capture in worker |
| `tests/integration/test_clip_cache_get_many.py` | batched read + query count |
| `tests/unit/test_run_estimator.py` | branches, fallback, exclusions |
| `tests/integration/test_estimate_query_count.py` | estimator N+1 guard |

Run all commands from the repo root with the project venv active (`python3.12`/`3.13` — never 3.14).

---

### Task 1: Migration `0016_run_telemetry.sql` + `app_meta` repo

**Files:**
- Create: `backend/migrations/0016_run_telemetry.sql`
- Create: `backend/app/repositories/app_meta.py`
- Test: `tests/integration/test_run_telemetry_migration.py`

- [ ] **Step 1.0: Check the migration number is free**

Run: `ls backend/migrations/ | tail -3` and `git log --oneline -3 origin/main -- backend/migrations/ 2>/dev/null`
Expected: highest existing migration is `0015_jobs_run_group.sql`. If a parallel branch took `0016`, use the next free number and adjust every reference below.

- [ ] **Step 1.1: Write the failing test**

```python
# tests/integration/test_run_telemetry_migration.py
"""Migration 0016: run_telemetry + app_meta exist; install_id is stable."""

import pytest

from backend.app.repositories.app_meta import get_or_create_install_id


@pytest.mark.asyncio
async def test_run_telemetry_table_exists(db):
    cur = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name IN ('run_telemetry', 'app_meta')"
    )
    names = {r[0] for r in await cur.fetchall()}
    assert names == {"run_telemetry", "app_meta"}


@pytest.mark.asyncio
async def test_run_telemetry_required_columns(db):
    cur = await db.execute("PRAGMA table_info(run_telemetry)")
    cols = {r[1] for r in await cur.fetchall()}
    required = {
        "event_id", "occurred_at", "install_id", "app_version", "kind",
        "archive_id", "user_ref", "job_id", "clip_id", "clip_name",
        "prompt_version_id", "prompt_hash", "schema_hash",
        "prompt_chars_rendered", "model",
        "media_kind", "media_duration_secs", "media_width", "media_height",
        "media_fps", "media_bytes", "media_ext", "media_resolution_setting",
        "preprocess", "vertex_project", "vertex_location", "ai_store_kind",
        "status", "error_class", "finish_reason", "attempt_count",
        "duration_s", "tokens_in", "tokens_in_text", "tokens_in_video",
        "tokens_in_audio", "tokens_in_image", "tokens_cached", "tokens_out",
        "tokens_thinking", "cost_usd", "pricing_version",
        "est_tokens_in", "est_tokens_out_p50", "est_tokens_out_p90",
        "est_cost_usd_p50", "est_cost_usd_p90", "est_confidence",
        "output_chars", "review_item_count",
        "attrs", "sent_at", "send_attempts",
    }
    missing = required - cols
    assert not missing, f"missing columns: {missing}"


@pytest.mark.asyncio
async def test_install_id_created_once_and_stable(db):
    a = await get_or_create_install_id(db)
    b = await get_or_create_install_id(db)
    assert a == b
    assert len(a) == 36  # uuid4 canonical form
```

- [ ] **Step 1.2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_run_telemetry_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: backend.app.repositories.app_meta` (and table assertions would fail).

- [ ] **Step 1.3: Write the migration**

```sql
-- backend/migrations/0016_run_telemetry.sql
-- 0016: run telemetry + app_meta. One run_telemetry row per Gemini call,
-- written by both annotator finalize paths. Doubles as the (dormant)
-- Phase-2 outbox via sent_at/send_attempts. Rows are kept forever —
-- they are the estimator's history (~1 KB/run).
-- See docs/specs/2026-06-07-run-telemetry-cost-estimation-design.md.

CREATE TABLE app_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE run_telemetry (
  id                    INTEGER PRIMARY KEY,
  event_id              TEXT NOT NULL UNIQUE,
  occurred_at           TEXT NOT NULL,
  install_id            TEXT NOT NULL,
  app_version           TEXT,
  kind                  TEXT NOT NULL CHECK (kind IN ('studio','annotation')),

  archive_id            TEXT,
  user_ref              TEXT,
  job_id                INTEGER,
  clip_id               INTEGER,
  clip_name             TEXT,

  prompt_version_id     INTEGER,
  prompt_hash           TEXT,
  schema_hash           TEXT,
  prompt_chars_rendered INTEGER,
  model                 TEXT NOT NULL,

  media_kind            TEXT,
  media_duration_secs   REAL,
  media_width           INTEGER,
  media_height          INTEGER,
  media_fps             REAL,
  media_bytes           INTEGER,
  media_ext             TEXT,
  media_resolution_setting TEXT,
  preprocess            TEXT,

  vertex_project        TEXT,
  vertex_location       TEXT,
  ai_store_kind         TEXT,

  status                TEXT NOT NULL CHECK (status IN ('ok','error')),
  error_class           TEXT,
  finish_reason         TEXT,
  attempt_count         INTEGER,
  duration_s            REAL,
  tokens_in             INTEGER,
  tokens_in_text        INTEGER,
  tokens_in_video       INTEGER,
  tokens_in_audio       INTEGER,
  tokens_in_image       INTEGER,
  tokens_cached         INTEGER,
  tokens_out            INTEGER,
  tokens_thinking       INTEGER,
  cost_usd              REAL,
  pricing_version       TEXT,

  est_tokens_in         INTEGER,
  est_tokens_out_p50    INTEGER,
  est_tokens_out_p90    INTEGER,
  est_cost_usd_p50      REAL,
  est_cost_usd_p90      REAL,
  est_confidence        TEXT,

  output_chars          INTEGER,
  review_item_count     INTEGER,

  attrs                 TEXT,
  sent_at               TEXT,
  send_attempts         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_run_telemetry_estimator
  ON run_telemetry (prompt_hash, model, media_kind, status);
CREATE INDEX idx_run_telemetry_unsent
  ON run_telemetry (sent_at) WHERE sent_at IS NULL;
```

- [ ] **Step 1.4: Write the app_meta repo**

```python
# backend/app/repositories/app_meta.py
"""app_meta — tiny key/value store for install-scoped facts.

Today it holds exactly one key: ``install_id``, a uuid4 generated on
first read and stable for the lifetime of the data dir. Telemetry rows
carry it so records stay attributable if they ever leave this machine
(Phase 2 collector). Repos are leaves: no service imports here.
"""

import uuid

import aiosqlite

_INSTALL_ID_KEY = "install_id"


async def get_or_create_install_id(conn: aiosqlite.Connection) -> str:
    cur = await conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_INSTALL_ID_KEY,)
    )
    row = await cur.fetchone()
    if row is not None:
        return row[0]
    value = str(uuid.uuid4())
    # INSERT OR IGNORE + re-read guards the (unlikely) concurrent first call.
    await conn.execute(
        "INSERT OR IGNORE INTO app_meta(key, value) VALUES (?, ?)",
        (_INSTALL_ID_KEY, value),
    )
    await conn.commit()
    cur = await conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_INSTALL_ID_KEY,)
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]
```

- [ ] **Step 1.5: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_run_telemetry_migration.py -v`
Expected: 3 PASS.

- [ ] **Step 1.6: Commit**

```bash
git add backend/migrations/0016_run_telemetry.sql backend/app/repositories/app_meta.py tests/integration/test_run_telemetry_migration.py
git commit -m "feat(telemetry): run_telemetry + app_meta tables, install_id"
```

---

### Task 2: `media_kind` four-way classifier

**Files:**
- Modify: `backend/app/media_kind.py`
- Test: `tests/unit/test_media_kind_classify.py`

- [ ] **Step 2.1: Write the failing test**

```python
# tests/unit/test_media_kind_classify.py
"""classify_media_kind: image | audio | video | video+audio.

Unknown audio presence on a video defaults to video+audio — the
conservative estimate (slightly over, never under). Existing
is_image_path behavior must not change.
"""

import pytest

from backend.app.media_kind import classify_media_kind, is_image_path


@pytest.mark.parametrize(
    ("path", "has_audio", "expected"),
    [
        ("clips/a.jpg", None, "image"),
        ("clips/a.PNG", None, "image"),
        ("clips/a.wav", None, "audio"),
        ("clips/a.MP3", None, "audio"),
        ("clips/a.mov", None, "video+audio"),   # unknown audio → conservative
        ("clips/a.mp4", True, "video+audio"),
        ("clips/a.mp4", False, "video"),
        (None, None, "video+audio"),            # nothing known → conservative
        ("noext", None, "video+audio"),
    ],
)
def test_classify(path, has_audio, expected):
    assert classify_media_kind(path, has_audio=has_audio) == expected


def test_is_image_path_unchanged():
    assert is_image_path("x.jpeg") is True
    assert is_image_path("x.mov") is False
    assert is_image_path(None) is False
```

- [ ] **Step 2.2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_media_kind_classify.py -v`
Expected: FAIL — `ImportError: cannot import name 'classify_media_kind'`.

- [ ] **Step 2.3: Extend `media_kind.py`**

Append to the existing file (keep `IMAGE_EXTS` and `is_image_path` untouched):

```python
AUDIO_EXTS: frozenset[str] = frozenset(
    {".wav", ".mp3", ".aac", ".m4a", ".flac", ".aiff", ".aif", ".ogg"}
)


def classify_media_kind(path: str | None, *, has_audio: bool | None = None) -> str:
    """Classify media as ``image | audio | video | video+audio``.

    Extension-first, like ``is_image_path`` (CatDV's own flags are
    unreliable — see module docstring). ``has_audio`` refines the
    video case when the caller knows it; when unknown we default to
    ``video+audio`` — the conservative choice for token estimation
    (overestimates by the audio track, never underestimates).
    """
    if is_image_path(path):
        return "image"
    if path and PurePosixPath(path).suffix.lower() in AUDIO_EXTS:
        return "audio"
    if has_audio is False:
        return "video"
    return "video+audio"
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_media_kind_classify.py -v`
Expected: all PASS.

- [ ] **Step 2.5: Run the existing callers' tests for regressions**

Run: `python -m pytest tests/ -k "media_kind or thumbnail or proxy_resolver" -q`
Expected: all PASS (no existing behavior changed).

- [ ] **Step 2.6: Commit**

```bash
git add backend/app/media_kind.py tests/unit/test_media_kind_classify.py
git commit -m "feat(telemetry): four-way media kind classifier"
```

---

### Task 3: Models + capture module (usage extraction, hashing)

**Files:**
- Create: `backend/app/models/telemetry.py`
- Create: `backend/app/services/telemetry_capture.py`
- Test: `tests/unit/test_telemetry_capture.py`

- [ ] **Step 3.1: Write the failing test**

```python
# tests/unit/test_telemetry_capture.py
"""extract_usage handles camelCase AND snake_case usageMetadata,
modality details, thinking/cached tokens; hashes are template-stable."""

from backend.app.services.telemetry_capture import (
    TokenUsage,
    extract_finish_reason,
    extract_usage,
    prompt_hash,
    schema_hash,
)

CAMEL = {
    "usageMetadata": {
        "promptTokenCount": 1000,
        "candidatesTokenCount": 200,
        "thoughtsTokenCount": 50,
        "cachedContentTokenCount": 10,
        "promptTokensDetails": [
            {"modality": "TEXT", "tokenCount": 100},
            {"modality": "VIDEO", "tokenCount": 800},
            {"modality": "AUDIO", "tokenCount": 100},
        ],
    },
    "candidates": [{"finishReason": "STOP"}],
}

SNAKE = {
    "usage_metadata": {
        "prompt_token_count": 1000,
        "candidates_token_count": 200,
        "thoughts_token_count": 50,
        "cached_content_token_count": 10,
        "prompt_tokens_details": [
            {"modality": "TEXT", "token_count": 100},
            {"modality": "IMAGE", "token_count": 900},
        ],
    },
    "candidates": [{"finish_reason": "MAX_TOKENS"}],
}


def test_extract_camel():
    u = extract_usage(CAMEL)
    assert u == TokenUsage(
        tokens_in=1000, tokens_in_text=100, tokens_in_video=800,
        tokens_in_audio=100, tokens_in_image=0,
        tokens_cached=10, tokens_out=200, tokens_thinking=50,
    )
    assert extract_finish_reason(CAMEL) == "STOP"


def test_extract_snake():
    u = extract_usage(SNAKE)
    assert u.tokens_in == 1000
    assert u.tokens_in_image == 900
    assert u.tokens_thinking == 50
    assert extract_finish_reason(SNAKE) == "MAX_TOKENS"


def test_extract_missing_usage_is_zeros():
    u = extract_usage({})
    assert u.tokens_in == 0 and u.tokens_out == 0 and u.tokens_thinking == 0
    assert extract_finish_reason({}) is None


def test_billable_out():
    assert extract_usage(CAMEL).billable_out == 250


def test_prompt_hash_is_template_stable():
    # Same template → same hash regardless of how it gets rendered later.
    h1 = prompt_hash("describe scenes")
    h2 = prompt_hash("describe scenes")
    assert h1 == h2 and len(h1) == 64
    assert prompt_hash("describe scenes!") != h1


def test_schema_hash_key_order_insensitive():
    assert schema_hash({"a": 1, "b": 2}) == schema_hash({"b": 2, "a": 1})
```

- [ ] **Step 3.2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_telemetry_capture.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3.3: Write the model**

```python
# backend/app/models/telemetry.py
"""Telemetry domain models.

``RunTelemetryRecord`` mirrors the run_telemetry table 1:1 — one row
per Gemini call. ``TelemetryCtx`` is the install-scoped constant
context (built once at boot) threaded into the annotator so the
record builder never imports settings.
"""

from pydantic import BaseModel


class TelemetryCtx(BaseModel):
    install_id: str
    app_version: str | None = None
    archive_id: str | None = None
    vertex_project: str | None = None
    vertex_location: str | None = None


class RunTelemetryRecord(BaseModel):
    event_id: str
    occurred_at: str
    install_id: str
    app_version: str | None = None
    kind: str  # 'studio' | 'annotation'

    archive_id: str | None = None
    user_ref: str | None = None
    job_id: int | None = None
    clip_id: int | None = None
    clip_name: str | None = None

    prompt_version_id: int | None = None
    prompt_hash: str | None = None
    schema_hash: str | None = None
    prompt_chars_rendered: int | None = None
    model: str

    media_kind: str | None = None
    media_duration_secs: float | None = None
    media_width: int | None = None
    media_height: int | None = None
    media_fps: float | None = None
    media_bytes: int | None = None
    media_ext: str | None = None
    media_resolution_setting: str | None = None
    preprocess: str | None = None

    vertex_project: str | None = None
    vertex_location: str | None = None
    ai_store_kind: str | None = None

    status: str  # 'ok' | 'error'
    error_class: str | None = None
    finish_reason: str | None = None
    attempt_count: int | None = None
    duration_s: float | None = None
    tokens_in: int | None = None
    tokens_in_text: int | None = None
    tokens_in_video: int | None = None
    tokens_in_audio: int | None = None
    tokens_in_image: int | None = None
    tokens_cached: int | None = None
    tokens_out: int | None = None
    tokens_thinking: int | None = None
    cost_usd: float | None = None
    pricing_version: str | None = None

    est_tokens_in: int | None = None
    est_tokens_out_p50: int | None = None
    est_tokens_out_p90: int | None = None
    est_cost_usd_p50: float | None = None
    est_cost_usd_p90: float | None = None
    est_confidence: str | None = None

    output_chars: int | None = None
    review_item_count: int | None = None

    attrs: dict | None = None
```

- [ ] **Step 3.4: Write the capture module**

```python
# backend/app/services/telemetry_capture.py
"""Pure functions that turn a finished Gemini response into telemetry
facts: token usage (camelCase or snake_case ``usageMetadata``),
finish reason, and the prompt/schema identity hashes.

Hashes are computed over the prompt TEMPLATE (``version.body``), never
the rendered prompt — ``_render_prompt`` injects per-clip duration
text, so rendered hashes would never collide across clips and the
cross-install dedup key would be useless.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TokenUsage:
    tokens_in: int = 0
    tokens_in_text: int = 0
    tokens_in_video: int = 0
    tokens_in_audio: int = 0
    tokens_in_image: int = 0
    tokens_cached: int = 0
    tokens_out: int = 0       # candidatesTokenCount, raw
    tokens_thinking: int = 0  # thoughtsTokenCount — billed as output

    @property
    def billable_out(self) -> int:
        return self.tokens_out + self.tokens_thinking


def _get(d: dict, camel: str, snake: str) -> Any:
    return d.get(camel) if d.get(camel) is not None else d.get(snake)


def _int(v: Any) -> int:
    return int(v or 0)


def extract_usage(raw: dict[str, Any]) -> TokenUsage:
    usage = _get(raw or {}, "usageMetadata", "usage_metadata") or {}
    by_modality: dict[str, int] = {}
    details = _get(usage, "promptTokensDetails", "prompt_tokens_details") or []
    for entry in details:
        modality = str(entry.get("modality") or "").upper()
        count = _int(_get(entry, "tokenCount", "token_count"))
        by_modality[modality] = by_modality.get(modality, 0) + count
    return TokenUsage(
        tokens_in=_int(_get(usage, "promptTokenCount", "prompt_token_count")),
        tokens_in_text=by_modality.get("TEXT", 0),
        tokens_in_video=by_modality.get("VIDEO", 0),
        tokens_in_audio=by_modality.get("AUDIO", 0),
        tokens_in_image=by_modality.get("IMAGE", 0),
        tokens_cached=_int(
            _get(usage, "cachedContentTokenCount", "cached_content_token_count")
        ),
        tokens_out=_int(_get(usage, "candidatesTokenCount", "candidates_token_count")),
        tokens_thinking=_int(_get(usage, "thoughtsTokenCount", "thoughts_token_count")),
    )


def extract_finish_reason(raw: dict[str, Any]) -> str | None:
    candidates = (raw or {}).get("candidates") or []
    if not candidates:
        return None
    reason = _get(candidates[0] or {}, "finishReason", "finish_reason")
    return str(reason) if reason else None


def prompt_hash(template_body: str) -> str:
    return hashlib.sha256(template_body.encode("utf-8")).hexdigest()


def schema_hash(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 3.5: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_telemetry_capture.py -v`
Expected: all PASS.

- [ ] **Step 3.6: Commit**

```bash
git add backend/app/models/telemetry.py backend/app/services/telemetry_capture.py tests/unit/test_telemetry_capture.py
git commit -m "feat(telemetry): usage extraction + prompt/schema hashing"
```

---

### Task 4: Pricing module

**Files:**
- Create: `backend/app/services/pricing.py`
- Test: `tests/unit/test_pricing.py`

- [ ] **Step 4.1: Look up the CURRENT Vertex AI Gemini prices**

Open https://cloud.google.com/vertex-ai/generative-ai/pricing and note, for `gemini-2.5-flash-lite`, `gemini-2.5-flash`, and `gemini-2.5-pro` (per 1M tokens): text/image/video input, audio input, cached input, output. The numbers in Step 4.3 are **placeholders shaped like mid-2025 prices** — replace them with what the page says today, and put the page URL + date in each entry's `source_url`. The tests below deliberately use an injected rate card so they don't break when real rates change.

- [ ] **Step 4.2: Write the failing test**

```python
# tests/unit/test_pricing.py
"""compute_cost math against an injected rate card; unknown model → None."""

import pytest

from backend.app.services.pricing import (
    PRICING_VERSION,
    RateCard,
    compute_cost,
)
from backend.app.services.telemetry_capture import TokenUsage

CARD = RateCard(
    input_text_video_image_per_1m=0.10,
    input_audio_per_1m=0.30,
    input_cached_per_1m=0.025,
    output_per_1m=0.40,
    source_url="https://example.test/pricing",
)


def test_cost_math_modality_split():
    usage = TokenUsage(
        tokens_in=1_000_000, tokens_in_text=100_000, tokens_in_video=800_000,
        tokens_in_audio=100_000, tokens_cached=0,
        tokens_out=100_000, tokens_thinking=100_000,
    )
    cost, version = compute_cost(usage, "any-model", card=CARD)
    # (100k + 800k) * 0.10/1M + 100k * 0.30/1M + 200k * 0.40/1M
    assert cost == pytest.approx(0.09 + 0.03 + 0.08)
    assert version == PRICING_VERSION


def test_cached_tokens_billed_at_cached_rate():
    usage = TokenUsage(
        tokens_in=1_000_000, tokens_in_text=1_000_000, tokens_cached=400_000,
    )
    cost, _ = compute_cost(usage, "any-model", card=CARD)
    # 600k fresh text at 0.10 + 400k cached at 0.025
    assert cost == pytest.approx(0.06 + 0.01)


def test_no_modality_detail_falls_back_to_total():
    usage = TokenUsage(tokens_in=1_000_000, tokens_out=0)
    cost, _ = compute_cost(usage, "any-model", card=CARD)
    assert cost == pytest.approx(0.10)


def test_unknown_model_returns_none():
    usage = TokenUsage(tokens_in=1000)
    cost, version = compute_cost(usage, "model-that-does-not-exist")
    assert cost is None
    assert version == PRICING_VERSION
```

- [ ] **Step 4.3: Run to verify it fails, then write the module**

Run: `python -m pytest tests/unit/test_pricing.py -v` → FAIL (module not found).

```python
# backend/app/services/pricing.py
"""Vertex Gemini rate card + cost computation.

Rates are per **1M tokens**, split the way Gemini bills: text/image/
video input share one rate, audio input is higher, cached input is
discounted, and output (candidates + thinking) is one rate. Rates ship
with app releases; tokens are always stored alongside cost so history
is recomputable when the card was stale (spec §5).

UPDATE RATES from https://cloud.google.com/vertex-ai/generative-ai/pricing
and bump PRICING_VERSION whenever they change.
"""

import logging
from dataclasses import dataclass

from backend.app.services.telemetry_capture import TokenUsage

log = logging.getLogger(__name__)

PRICING_VERSION = "2026-06"


@dataclass(frozen=True)
class RateCard:
    input_text_video_image_per_1m: float
    input_audio_per_1m: float
    input_cached_per_1m: float
    output_per_1m: float
    source_url: str


# !!! Verify against the pricing page before committing (Step 4.1) !!!
RATE_CARDS: dict[str, RateCard] = {
    "gemini-2.5-flash-lite": RateCard(
        input_text_video_image_per_1m=0.10,
        input_audio_per_1m=0.30,
        input_cached_per_1m=0.025,
        output_per_1m=0.40,
        source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    ),
    "gemini-2.5-flash": RateCard(
        input_text_video_image_per_1m=0.30,
        input_audio_per_1m=1.00,
        input_cached_per_1m=0.075,
        output_per_1m=2.50,
        source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    ),
    "gemini-2.5-pro": RateCard(
        input_text_video_image_per_1m=1.25,
        input_audio_per_1m=1.25,
        input_cached_per_1m=0.31,
        output_per_1m=10.00,
        source_url="https://cloud.google.com/vertex-ai/generative-ai/pricing",
    ),
}


def compute_cost(
    usage: TokenUsage, model: str, *, card: RateCard | None = None
) -> tuple[float | None, str]:
    """Cost in USD for one call, or (None, version) when the model is
    not in the card. Never raises — a missing rate must not fail a run."""
    if card is None:
        card = RATE_CARDS.get(model)
    if card is None:
        log.warning("pricing: no rate card for model %r; cost_usd=NULL", model)
        return None, PRICING_VERSION

    audio = usage.tokens_in_audio
    detailed = (
        usage.tokens_in_text + usage.tokens_in_video
        + usage.tokens_in_image + audio
    )
    # Modality detail can be absent (older responses) — fall back to the
    # total at the text/video rate.
    non_audio = (detailed - audio) if detailed else usage.tokens_in
    cached = min(usage.tokens_cached, non_audio)
    fresh_non_audio = non_audio - cached

    cost = (
        fresh_non_audio * card.input_text_video_image_per_1m
        + audio * card.input_audio_per_1m
        + cached * card.input_cached_per_1m
        + usage.billable_out * card.output_per_1m
    ) / 1_000_000
    return cost, PRICING_VERSION
```

- [ ] **Step 4.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_pricing.py -v`
Expected: all PASS.

- [ ] **Step 4.5: Commit**

```bash
git add backend/app/services/pricing.py tests/unit/test_pricing.py
git commit -m "feat(telemetry): pricing rate card + compute_cost"
```

---

### Task 5: `RunTelemetryRepo` — insert + estimator aggregates

**Files:**
- Create: `backend/app/repositories/run_telemetry.py`
- Test: `tests/integration/test_run_telemetry_repo.py`

- [ ] **Step 5.1: Write the failing test**

```python
# tests/integration/test_run_telemetry_repo.py
"""RunTelemetryRepo: insert round-trip; aggregate reads exclude
MAX_TOKENS and error rows (they would poison estimates)."""

import uuid

import pytest

from backend.app.models.telemetry import RunTelemetryRecord
from backend.app.repositories.run_telemetry import RunTelemetryRepo


def _rec(**over) -> RunTelemetryRecord:
    base = dict(
        event_id=str(uuid.uuid4()),
        occurred_at="2026-06-07T12:00:00+00:00",
        install_id="inst-1",
        kind="studio",
        model="gemini-2.5-flash-lite",
        status="ok",
        media_kind="video+audio",
        media_duration_secs=10.0,
        prompt_hash="h" * 64,
        tokens_in=3000, tokens_in_video=2900,
        tokens_out=100, tokens_thinking=20,
        finish_reason="STOP",
        attrs={"note": "test"},
    )
    base.update(over)
    return RunTelemetryRecord(**base)


@pytest.mark.asyncio
async def test_insert_roundtrip(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec())
    cur = await db.execute(
        "SELECT kind, model, tokens_in, tokens_thinking, attrs FROM run_telemetry"
    )
    row = await cur.fetchone()
    assert row[0] == "studio" and row[1] == "gemini-2.5-flash-lite"
    assert row[2] == 3000 and row[3] == 20
    assert '"note"' in row[4]


@pytest.mark.asyncio
async def test_input_ratios_only_ok_rows_with_media_tokens(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(tokens_in_video=2900, media_duration_secs=10.0))
    await repo.insert(db, _rec(status="error"))          # excluded
    await repo.insert(db, _rec(tokens_in_video=0))        # excluded (no signal)
    ratios = await repo.recent_input_ratios(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio"
    )
    assert ratios == [290.0]


@pytest.mark.asyncio
async def test_output_rates_exclude_max_tokens_and_filter_by_hash(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(tokens_out=100, tokens_thinking=20))   # 12/s
    await repo.insert(db, _rec(finish_reason="MAX_TOKENS"))           # excluded
    await repo.insert(db, _rec(prompt_hash="x" * 64, tokens_out=500)) # other prompt
    rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio",
        prompt_hash="h" * 64,
    )
    assert rates == [12.0]
    all_rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="video+audio",
    )
    assert sorted(all_rates) == [12.0, 52.0]


@pytest.mark.asyncio
async def test_output_rates_images_are_per_item(db):
    repo = RunTelemetryRepo()
    await repo.insert(db, _rec(
        media_kind="image", media_duration_secs=None,
        tokens_out=800, tokens_thinking=0,
    ))
    rates = await repo.recent_output_rates(
        db, model="gemini-2.5-flash-lite", media_kind="image",
    )
    assert rates == [800.0]
```

- [ ] **Step 5.2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_run_telemetry_repo.py -v`
Expected: FAIL — module not found.

- [ ] **Step 5.3: Write the repo**

```python
# backend/app/repositories/run_telemetry.py
"""RunTelemetryRepo — one row per Gemini call (both run kinds).

Local history for the estimator AND the dormant Phase-2 outbox
(sent_at / send_attempts). Rows are never deleted. Aggregate readers
return small float lists (LIMIT-bounded); percentile math happens in
Python — SQLite has no percentile function and the lists are ≤50 rows.

Stats hygiene (spec §6): only status='ok' rows count; output stats
also exclude finish_reason='MAX_TOKENS' (truncated runs would drag
the output estimate down — the wrong direction for customer quotes).
Output rates use BILLABLE output (tokens_out + tokens_thinking).
"""

import json

import aiosqlite

from backend.app.models.telemetry import RunTelemetryRecord

_COLS = [
    "event_id", "occurred_at", "install_id", "app_version", "kind",
    "archive_id", "user_ref", "job_id", "clip_id", "clip_name",
    "prompt_version_id", "prompt_hash", "schema_hash",
    "prompt_chars_rendered", "model",
    "media_kind", "media_duration_secs", "media_width", "media_height",
    "media_fps", "media_bytes", "media_ext", "media_resolution_setting",
    "preprocess", "vertex_project", "vertex_location", "ai_store_kind",
    "status", "error_class", "finish_reason", "attempt_count", "duration_s",
    "tokens_in", "tokens_in_text", "tokens_in_video", "tokens_in_audio",
    "tokens_in_image", "tokens_cached", "tokens_out", "tokens_thinking",
    "cost_usd", "pricing_version",
    "est_tokens_in", "est_tokens_out_p50", "est_tokens_out_p90",
    "est_cost_usd_p50", "est_cost_usd_p90", "est_confidence",
    "output_chars", "review_item_count", "attrs",
]


class RunTelemetryRepo:
    async def insert(
        self, conn: aiosqlite.Connection, rec: RunTelemetryRecord
    ) -> int:
        data = rec.model_dump()
        data["attrs"] = json.dumps(data["attrs"]) if data["attrs"] else None
        placeholders = ", ".join("?" for _ in _COLS)
        cur = await conn.execute(
            f"INSERT INTO run_telemetry({', '.join(_COLS)}) VALUES ({placeholders})",
            tuple(data[c] for c in _COLS),
        )
        rid = cur.lastrowid
        assert rid is not None
        await conn.commit()
        return rid

    async def recent_input_ratios(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
        limit: int = 50,
    ) -> list[float]:
        """tokens_in_<media>/second for recent ok runs — calibrates the
        deterministic input constant. Picks the modality column matching
        the media kind."""
        col = {
            "video+audio": "tokens_in_video + tokens_in_audio",
            "video": "tokens_in_video",
            "audio": "tokens_in_audio",
            "image": "tokens_in_image",
        }.get(media_kind, "tokens_in_video")
        cur = await conn.execute(
            f"SELECT CAST(({col}) AS REAL) / media_duration_secs "
            "FROM run_telemetry "
            "WHERE model = ? AND media_kind = ? AND status = 'ok' "
            f"AND COALESCE(media_duration_secs, 0) > 0 AND ({col}) > 0 "
            "ORDER BY id DESC LIMIT ?",
            (model, media_kind, limit),
        )
        return [r[0] for r in await cur.fetchall()]

    async def recent_output_rates(
        self,
        conn: aiosqlite.Connection,
        *,
        model: str,
        media_kind: str,
        prompt_hash: str | None = None,
        limit: int = 50,
    ) -> list[float]:
        """Billable output per media-second (per-item for images) for
        recent ok, non-truncated runs."""
        bill = "(COALESCE(tokens_out,0) + COALESCE(tokens_thinking,0))"
        value = (
            f"CAST({bill} AS REAL)"
            if media_kind == "image"
            else f"CAST({bill} AS REAL) / media_duration_secs"
        )
        where = [
            "model = ?", "media_kind = ?", "status = 'ok'",
            "COALESCE(finish_reason,'') != 'MAX_TOKENS'",
            f"{bill} > 0",
        ]
        params: list = [model, media_kind]
        if media_kind != "image":
            where.append("COALESCE(media_duration_secs, 0) > 0")
        if prompt_hash is not None:
            where.append("prompt_hash = ?")
            params.append(prompt_hash)
        params.append(limit)
        cur = await conn.execute(
            f"SELECT {value} FROM run_telemetry WHERE {' AND '.join(where)} "
            "ORDER BY id DESC LIMIT ?",
            tuple(params),
        )
        return [r[0] for r in await cur.fetchall()]
```

- [ ] **Step 5.4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_run_telemetry_repo.py -v`
Expected: all PASS.

- [ ] **Step 5.5: Commit**

```bash
git add backend/app/repositories/run_telemetry.py tests/integration/test_run_telemetry_repo.py
git commit -m "feat(telemetry): run_telemetry repo with estimator aggregates"
```

---

### Task 6: Estimator service

**Files:**
- Create: `backend/app/services/run_estimator.py`
- Test: `tests/unit/test_run_estimator.py`

- [ ] **Step 6.1: Write the failing test**

```python
# tests/unit/test_run_estimator.py
"""Estimator: deterministic input per media kind; output distribution
with fallback chain; confidence labels; seeds when no history.

Uses a fake repo so no DB is needed — the repo contract is covered by
tests/integration/test_run_telemetry_repo.py.
"""

import pytest

from backend.app.services.run_estimator import (
    ClipEstimateInput,
    estimate_clips,
)


class FakeRepo:
    """recent_* return canned lists keyed by (media_kind, prompt_hash or '*')."""

    def __init__(self, input_ratios=None, output_rates=None):
        self.input_ratios = input_ratios or {}
        self.output_rates = output_rates or {}

    async def recent_input_ratios(self, conn, *, model, media_kind, limit=50):
        return self.input_ratios.get(media_kind, [])

    async def recent_output_rates(
        self, conn, *, model, media_kind, prompt_hash=None, limit=50
    ):
        return self.output_rates.get((media_kind, prompt_hash or "*"), [])


VIDEO = ClipEstimateInput(clip_id=1, media_kind="video+audio", duration_secs=60.0)
IMAGE = ClipEstimateInput(clip_id=2, media_kind="image", duration_secs=None)


@pytest.mark.asyncio
async def test_zero_history_uses_seeds_and_is_rough():
    est = await estimate_clips(
        None, FakeRepo(), [VIDEO],
        prompt_body="p" * 400, schema={"type": "object"},
        model="gemini-2.5-flash-lite",
    )
    # 60s * seed 300 tok/s = 18000 media tokens + prompt/schema chars/4 > 0
    assert est.tokens_in > 18000
    assert est.confidence == "rough"
    assert est.tokens_out_p90 >= est.tokens_out_p50 > 0


@pytest.mark.asyncio
async def test_input_calibration_overrides_seed():
    repo = FakeRepo(input_ratios={"video+audio": [250.0] * 10})
    est = await estimate_clips(
        None, repo, [VIDEO],
        prompt_body="", schema={}, model="m",
    )
    assert 60 * 250 <= est.tokens_in <= 60 * 250 + 50  # calibrated, +schema/prompt≈0


@pytest.mark.asyncio
async def test_prompt_level_history_wins_and_confidence_good():
    repo = FakeRepo(output_rates={
        ("video+audio", "HASH"): [10.0] * 12,   # level 1: 12 samples
        ("video+audio", "*"): [99.0] * 50,      # level 2 would say 99/s
    })
    est = await estimate_clips(
        None, repo, [VIDEO],
        prompt_body="body", schema={}, model="m", prompt_hash_override="HASH",
    )
    assert est.tokens_out_p50 == 600  # 60s * p50(10/s)
    assert est.confidence == "good"


@pytest.mark.asyncio
async def test_fallback_to_model_level_is_fair():
    repo = FakeRepo(output_rates={
        ("video+audio", "HASH"): [10.0],          # only 1 sample — below min 3
        ("video+audio", "*"): [20.0, 20.0, 20.0], # level 2 wins
    })
    est = await estimate_clips(
        None, repo, [VIDEO],
        prompt_body="body", schema={}, model="m", prompt_hash_override="HASH",
    )
    assert est.tokens_out_p50 == 1200
    assert est.confidence == "fair"


@pytest.mark.asyncio
async def test_image_unknown_dims_one_tile_and_per_item_output():
    repo = FakeRepo(output_rates={("image", "*"): [500.0, 600.0, 700.0]})
    est = await estimate_clips(
        None, repo, [IMAGE],
        prompt_body="", schema={}, model="m",
    )
    assert est.tokens_in >= 258           # 1 tile minimum
    assert est.tokens_out_p50 == 600      # per-item median


@pytest.mark.asyncio
async def test_unknown_model_cost_is_none_but_tokens_present():
    est = await estimate_clips(
        None, FakeRepo(), [VIDEO],
        prompt_body="", schema={}, model="no-such-model",
    )
    assert est.tokens_in > 0
    assert est.cost_usd_p50 is None and est.cost_usd_p90 is None
```

- [ ] **Step 6.2: Run to verify it fails**

Run: `python -m pytest tests/unit/test_run_estimator.py -v` → FAIL (module not found).

- [ ] **Step 6.3: Write the estimator**

```python
# backend/app/services/run_estimator.py
"""Pre-run cost estimator (spec §6).

Input tokens are deterministic arithmetic per media kind, seeded with
documented constants and self-calibrated from local history (median of
actual per-second token ratios). Output tokens are the uncertain part:
a p50/p90 distribution from history with a fallback chain —
(prompt_hash, model, kind) → (model, kind) → seed. All statistics use
BILLABLE output (candidates + thinking) and exclude MAX_TOKENS rows
(enforced in RunTelemetryRepo). Fully offline: aggregate SQL only,
never a network call. Query count is per media-kind group, never per
clip (ADR 0046).
"""

from dataclasses import dataclass

from backend.app.services.pricing import RATE_CARDS, compute_cost
from backend.app.services.telemetry_capture import (
    TokenUsage,
    prompt_hash as _prompt_hash,
    schema_hash as _schema_hash,  # noqa: F401  (Phase 2 wire identity)
)

# Seed constants — sanity-check against one real run's usageMetadata
# during implementation (spec §6). Calibration replaces them as soon as
# 3+ runs of the same (model, kind) exist.
SEED_INPUT_TOKENS_PER_SEC = {
    "video+audio": 300.0,
    "video": 258.0,
    "audio": 32.0,
}
IMAGE_TILE_TOKENS = 258
SEED_OUTPUT_TOKENS_PER_SEC = 15.0
SEED_OUTPUT_TOKENS_PER_IMAGE = 700.0
_MIN_SAMPLES = 3
_GOOD_SAMPLES = 10
CHARS_PER_TOKEN = 4.0


@dataclass(frozen=True)
class ClipEstimateInput:
    clip_id: int
    media_kind: str            # image | audio | video | video+audio
    duration_secs: float | None
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class RunEstimate:
    tokens_in: int
    tokens_out_p50: int
    tokens_out_p90: int
    cost_usd_p50: float | None
    cost_usd_p90: float | None
    confidence: str            # good | fair | rough
    n_samples: int
    n_clips: int


def _pct(values: list[float], q: float) -> float:
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[idx]


def _image_tiles(width: int | None, height: int | None) -> int:
    if not width or not height:
        return 1
    return max(1, -(-width // 768)) * max(1, -(-height // 768))


async def estimate_clips(
    conn,
    repo,
    clips: list[ClipEstimateInput],
    *,
    prompt_body: str,
    schema: dict,
    model: str,
    prompt_hash_override: str | None = None,
) -> RunEstimate:
    p_hash = prompt_hash_override or _prompt_hash(prompt_body)
    prompt_tokens = (len(prompt_body) + len(str(schema))) / CHARS_PER_TOKEN

    # One repo round per distinct media kind, NOT per clip.
    kinds = {c.media_kind for c in clips}
    input_ratio: dict[str, float] = {}
    out_rates: dict[str, list[float]] = {}
    out_level: dict[str, int] = {}
    for kind in kinds:
        ratios = await repo.recent_input_ratios(conn, model=model, media_kind=kind)
        if len(ratios) >= _MIN_SAMPLES:
            input_ratio[kind] = _pct(ratios, 0.5)
        rates = await repo.recent_output_rates(
            conn, model=model, media_kind=kind, prompt_hash=p_hash
        )
        if len(rates) >= _MIN_SAMPLES:
            out_rates[kind], out_level[kind] = rates, 1
            continue
        rates = await repo.recent_output_rates(conn, model=model, media_kind=kind)
        if len(rates) >= _MIN_SAMPLES:
            out_rates[kind], out_level[kind] = rates, 2
        else:
            out_rates[kind], out_level[kind] = [], 3

    tokens_in = prompt_tokens * len(clips)
    out_p50 = 0.0
    out_p90 = 0.0
    n_samples = 0
    worst_level = 1
    for c in clips:
        if c.media_kind == "image":
            tokens_in += _image_tiles(c.width, c.height) * IMAGE_TILE_TOKENS
            rates = out_rates[c.media_kind]
            if rates:
                out_p50 += _pct(rates, 0.5)
                out_p90 += _pct(rates, 0.9)
            else:
                out_p50 += SEED_OUTPUT_TOKENS_PER_IMAGE
                out_p90 += SEED_OUTPUT_TOKENS_PER_IMAGE * 2
        else:
            dur = float(c.duration_secs or 0.0)
            k = input_ratio.get(
                c.media_kind,
                SEED_INPUT_TOKENS_PER_SEC.get(c.media_kind, 300.0),
            )
            tokens_in += dur * k
            rates = out_rates[c.media_kind]
            if rates:
                out_p50 += dur * _pct(rates, 0.5)
                out_p90 += dur * _pct(rates, 0.9)
            else:
                out_p50 += dur * SEED_OUTPUT_TOKENS_PER_SEC
                out_p90 += dur * SEED_OUTPUT_TOKENS_PER_SEC * 2
        n_samples = max(n_samples, len(out_rates[c.media_kind]))
        worst_level = max(worst_level, out_level[c.media_kind])

    if worst_level == 1 and n_samples >= _GOOD_SAMPLES:
        confidence = "good"
    elif worst_level <= 2 and n_samples >= _MIN_SAMPLES:
        confidence = "fair"
    else:
        confidence = "rough"

    def _cost(out_tokens: float) -> float | None:
        if model not in RATE_CARDS:
            return None
        # Approximate the modality split: media tokens at the video rate
        # bucket (correct for video/image; audio clips are billed higher —
        # route their share through the audio bucket).
        audio_secs = sum(
            float(c.duration_secs or 0.0) for c in clips if c.media_kind == "audio"
        )
        audio_tokens = audio_secs * SEED_INPUT_TOKENS_PER_SEC["audio"]
        usage = TokenUsage(
            tokens_in=int(tokens_in),
            tokens_in_text=int(prompt_tokens * len(clips)),
            tokens_in_video=int(tokens_in - prompt_tokens * len(clips) - audio_tokens),
            tokens_in_audio=int(audio_tokens),
            tokens_out=int(out_tokens),
        )
        cost, _version = compute_cost(usage, model)
        return cost

    return RunEstimate(
        tokens_in=int(tokens_in),
        tokens_out_p50=int(out_p50),
        tokens_out_p90=int(out_p90),
        cost_usd_p50=_cost(out_p50),
        cost_usd_p90=_cost(out_p90),
        confidence=confidence,
        n_samples=n_samples,
        n_clips=len(clips),
    )
```

- [ ] **Step 6.4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_run_estimator.py -v`
Expected: all PASS. If `test_input_calibration_overrides_seed` is off by the prompt-token allowance, adjust the assertion bounds — the contract is "calibrated ratio used, seed ignored".

- [ ] **Step 6.5: Commit**

```bash
git add backend/app/services/run_estimator.py tests/unit/test_run_estimator.py
git commit -m "feat(telemetry): pre-run estimator with history calibration"
```

---

### Task 7: Wire context + annotator capture (the integration core)

**Files:**
- Modify: `backend/app/context.py`
- Modify: `backend/app/services/annotator.py`
- Modify: `backend/app/routes/jobs.py` (threading only)
- Modify: `backend/app/routes/studio.py` (threading only)
- Test: `tests/integration/test_annotator_telemetry.py`

- [ ] **Step 7.1: Write the failing test**

Mirror the fakes from `tests/integration/test_annotator_worker.py` (FakeResolver, FakeAIStore, FakeArchive — copy them; they are small) and add a FakeGemini that returns `usageMetadata` with thinking tokens. Key assertions: a `run_telemetry` row per processed clip on **both** paths, billable `tokens_out` on `studio_run`, non-NULL `cost_usd`, error rows captured.

```python
# tests/integration/test_annotator_telemetry.py
"""Both finalize paths write run_telemetry; studio_run.tokens_out is
billable (candidates + thinking); cost_usd computed; errors recorded."""

import datetime as dt
import json
from pathlib import Path

import pytest

from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.repositories.studio_runs import StudioRunsRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus

# --- fakes: copy FakeResolver / FakeAIStore / FakeArchive verbatim from
# tests/integration/test_annotator_worker.py (they are stable test
# doubles; a shared tests/fakes module refactor is out of scope). ---
from tests.integration.test_annotator_worker import (  # type: ignore
    FakeAIStore,
    FakeArchive,
    FakeResolver,
)

USAGE = {
    "promptTokenCount": 3000,
    "candidatesTokenCount": 100,
    "thoughtsTokenCount": 40,
    "promptTokensDetails": [
        {"modality": "TEXT", "tokenCount": 100},
        {"modality": "VIDEO", "tokenCount": 2800},
        {"modality": "AUDIO", "tokenCount": 100},
    ],
}


class FakeGemini:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def annotate(self, *, file_ref, prompt, schema, model):
        if self.fail:
            raise RuntimeError("boom")
        return {
            "text": json.dumps({"scenes": []}),
            "raw": {"usageMetadata": USAGE, "candidates": [{"finishReason": "STOP"}]},
        }


TCTX = TelemetryCtx(
    install_id="inst-test",
    archive_id="catdv:test",
    vertex_project="p",
    vertex_location="europe-west3",
)


async def _setup(db, tmp_path, *, kind=None):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None, body="describe scenes",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"}, model="gemini-2.5-flash-lite",
    )
    jobs = JobsRepo()
    f = tmp_path / "c1.mov"
    f.write_bytes(b"fake")
    job_id = await jobs.create_job(
        db, prompt_version_id=vid, clip_ids=[101], kind=kind
    )
    if kind == "studio":
        sruns = StudioRunsRepo()
        rid = await sruns.create_pending(
            db, prompt_version_id=vid, clip_id=101, model="gemini-2.5-flash-lite"
        )
        await sruns.attach_job(db, rid, job_id=job_id)
    return job_id, f


def _run_kwargs(db, files, gemini):
    return dict(
        db=db, archive=FakeArchive({101: {"name": "clip 101"}}),
        proxy_resolver=FakeResolver(files), ai_store=FakeAIStore(),
        gemini=gemini, event_bus=EventBus(),
        annotations_repo=AnnotationsRepo(), review_items_repo=ReviewItemsRepo(),
        jobs_repo=JobsRepo(), prompts_repo=PromptsRepo(),
        studio_runs_repo=StudioRunsRepo(),
        run_telemetry_repo=RunTelemetryRepo(), telemetry_ctx=TCTX,
    )


@pytest.mark.asyncio
async def test_annotation_path_records_telemetry(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind=None)
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    cur = await db.execute(
        "SELECT kind, status, tokens_in, tokens_out, tokens_thinking, "
        "tokens_in_video, cost_usd, prompt_hash, media_kind, install_id, "
        "est_tokens_in, finish_reason, clip_name FROM run_telemetry"
    )
    rows = await cur.fetchall()
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == "annotation" and r[1] == "ok"
    assert (r[2], r[3], r[4], r[5]) == (3000, 100, 40, 2800)
    assert r[6] is not None and r[6] > 0          # cost computed
    assert len(r[7]) == 64                        # prompt_hash of TEMPLATE
    assert r[8] == "video+audio"
    assert r[9] == "inst-test"
    assert r[10] is not None and r[10] > 0        # est stamped pre-call
    assert r[11] == "STOP"
    assert r[12] == "clip 101"


@pytest.mark.asyncio
async def test_studio_path_billable_tokens_and_cost(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind="studio")
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    cur = await db.execute("SELECT tokens_out, cost_usd FROM studio_run")
    out, cost = await cur.fetchone()
    assert out == 140                # candidates 100 + thinking 40 (billable)
    assert cost is not None and cost > 0
    cur = await db.execute("SELECT kind, status FROM run_telemetry")
    assert (await cur.fetchone()) == ("studio", "ok")


@pytest.mark.asyncio
async def test_failed_run_records_error_row(db, tmp_path):
    job_id, f = await _setup(db, tmp_path, kind=None)
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini(fail=True)))
    cur = await db.execute("SELECT status, error_class, model FROM run_telemetry")
    row = await cur.fetchone()
    assert row == ("error", "RuntimeError", "gemini-2.5-flash-lite")


@pytest.mark.asyncio
async def test_telemetry_insert_failure_does_not_fail_run(db, tmp_path, monkeypatch):
    job_id, f = await _setup(db, tmp_path, kind=None)

    async def _boom(self, conn, rec):
        raise RuntimeError("telemetry db broken")

    monkeypatch.setattr(RunTelemetryRepo, "insert", _boom)
    await run_job(job_id=job_id, **_run_kwargs(db, {101: f}, FakeGemini()))
    items = await JobsRepo().list_items(db, job_id)
    assert items[0].status == "review_ready"      # run still succeeded
```

- [ ] **Step 7.2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_annotator_telemetry.py -v`
Expected: FAIL — `run_job() got an unexpected keyword argument 'run_telemetry_repo'`.

- [ ] **Step 7.3: Wire `context.py`**

Three edits:

(a) imports — add:

```python
from backend.app.models.telemetry import TelemetryCtx
from backend.app.repositories.app_meta import get_or_create_install_id
from backend.app.repositories.run_telemetry import RunTelemetryRepo
```

(b) `CoreCtx` — add a repo field next to `studio_runs_repo` and a late-bound ctx:

```python
    run_telemetry_repo: RunTelemetryRepo = field(default_factory=RunTelemetryRepo)
    telemetry_ctx: TelemetryCtx = field(init=False)
```

and at the end of `CoreCtx.build` (after `ctx.write_queue = ...`), before `return ctx`:

```python
        import os
        from urllib.parse import urlparse

        install_id = await get_or_create_install_id(conn)
        host = urlparse(settings.catdv_base_url).netloc or None
        archive_id = (
            f"{settings.archive_provider}:{host}"
            if settings.archive_provider == "catdv" and host
            else settings.archive_provider
        )
        ctx.telemetry_ctx = TelemetryCtx(
            install_id=install_id,
            app_version=os.environ.get("APP_VERSION"),
            archive_id=archive_id,
            vertex_project=settings.gcp_project_id,
            vertex_location=settings.gcp_location,
        )
```

(c) `LiveCtx` — add delegators next to the `studio_runs_repo` property (the drift guard `tests/unit/test_context_delegation.py` fails without them):

```python
    @property
    def run_telemetry_repo(self) -> RunTelemetryRepo:
        return self.core.run_telemetry_repo

    @property
    def telemetry_ctx(self) -> TelemetryCtx:
        return self.core.telemetry_ctx
```

- [ ] **Step 7.4: Extend the annotator**

In `backend/app/services/annotator.py`:

(a) New imports:

```python
import uuid
from datetime import UTC, datetime

from backend.app.media_kind import classify_media_kind
from backend.app.models.telemetry import RunTelemetryRecord, TelemetryCtx
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services import run_estimator
from backend.app.services.pricing import compute_cost
from backend.app.services.telemetry_capture import (
    extract_finish_reason,
    extract_usage,
    prompt_hash,
    schema_hash,
)
```

(b) `run_job` signature — add two keyword params (after `studio_runs_repo`):

```python
    run_telemetry_repo: RunTelemetryRepo,
    telemetry_ctx: TelemetryCtx,
```

and pass both through to `_process_item(...)`, which also gains them as params.

(c) In `_process_item`, after `duration_secs = ...` compute the media meta and the pre-call estimate, and time the call (existing `t0`/`elapsed_s` stay):

```python
    media_path = str(
        (canonical.media.cached_path or canonical.media.upstream_handle) or ""
    )
    media_meta = {
        "media_kind": classify_media_kind(media_path or None),
        "media_duration_secs": duration_secs or None,
        "media_fps": canonical.fps or None,
        "media_bytes": canonical.media.size_bytes,
        "media_ext": (Path(media_path).suffix.lower() or None) if media_path else None,
        "clip_name": canonical.name or None,
    }

    # Pre-call estimate (spec §6; stamped onto the telemetry row so
    # est-vs-actual is one query). Blind to the outcome by construction.
    est: run_estimator.RunEstimate | None = None
    try:
        est = await run_estimator.estimate_clips(
            db, run_telemetry_repo,
            [run_estimator.ClipEstimateInput(
                clip_id=item.catdv_clip_id,
                media_kind=media_meta["media_kind"],
                duration_secs=duration_secs or None,
            )],
            prompt_body=version.body,
            schema=version.output_schema,
            model=version.model,
        )
    except Exception:  # noqa: BLE001 — estimation must never block a run
        log.exception("pre-run estimate failed for clip %s", item.catdv_clip_id)
```

(d) New module-level recorder helper (place after `_render_prompt`). It swallows its own failures — bookkeeping must never fail a run:

```python
async def _record_telemetry(
    db,
    repo: RunTelemetryRepo,
    tctx: TelemetryCtx,
    *,
    kind: str,
    item,
    version,
    status: str,
    result: dict | None = None,
    error_class: str | None = None,
    duration_s: float | None = None,
    media_meta: dict | None = None,
    est=None,
    ai_store_kind: str | None = None,
    review_item_count: int | None = None,
) -> None:
    try:
        raw = (result or {}).get("raw") or {}
        usage = extract_usage(raw)
        cost_usd, pricing_version = compute_cost(usage, version.model)
        if status == "error":
            cost_usd = None
        rendered_len = len((result or {}).get("text") or "") if result else None
        mm = media_meta or {}
        rec = RunTelemetryRecord(
            event_id=str(uuid.uuid4()),
            occurred_at=datetime.now(UTC).isoformat(),
            install_id=tctx.install_id,
            app_version=tctx.app_version,
            kind="studio" if kind == "studio" else "annotation",
            archive_id=tctx.archive_id,
            job_id=item.job_id,
            clip_id=item.catdv_clip_id,
            clip_name=mm.get("clip_name"),
            prompt_version_id=version.id,
            prompt_hash=prompt_hash(version.body),
            schema_hash=schema_hash(version.output_schema),
            model=version.model,
            media_kind=mm.get("media_kind"),
            media_duration_secs=mm.get("media_duration_secs"),
            media_fps=mm.get("media_fps"),
            media_bytes=mm.get("media_bytes"),
            media_ext=mm.get("media_ext"),
            vertex_project=tctx.vertex_project,
            vertex_location=tctx.vertex_location,
            ai_store_kind=ai_store_kind,
            status=status,
            error_class=error_class,
            finish_reason=extract_finish_reason(raw),
            attempt_count=1,
            duration_s=duration_s,
            tokens_in=usage.tokens_in,
            tokens_in_text=usage.tokens_in_text,
            tokens_in_video=usage.tokens_in_video,
            tokens_in_audio=usage.tokens_in_audio,
            tokens_in_image=usage.tokens_in_image,
            tokens_cached=usage.tokens_cached,
            tokens_out=usage.tokens_out,
            tokens_thinking=usage.tokens_thinking,
            cost_usd=cost_usd,
            pricing_version=pricing_version,
            est_tokens_in=getattr(est, "tokens_in", None),
            est_tokens_out_p50=getattr(est, "tokens_out_p50", None),
            est_tokens_out_p90=getattr(est, "tokens_out_p90", None),
            est_cost_usd_p50=getattr(est, "cost_usd_p50", None),
            est_cost_usd_p90=getattr(est, "cost_usd_p90", None),
            est_confidence=getattr(est, "confidence", None),
            output_chars=rendered_len,
            review_item_count=review_item_count,
        )
        await repo.insert(db, rec)
    except Exception:  # noqa: BLE001 — telemetry must never fail the run
        log.exception("run_telemetry insert failed (run unaffected)")
```

(e) Call sites:

- `_finalize_studio` and `_finalize_annotation` gain `run_telemetry_repo`, `telemetry_ctx`, `media_meta`, `est`, `ai_store_kind` params (passed from `_process_item`; `ai_store_kind=getattr(ai_store, "id", None)`, and `_finalize_annotation` also gains `elapsed_s` which `_process_item` already has).
- In `_finalize_studio`: replace the manual `usage = ...` block with `usage = extract_usage(result.get("raw") or {})`, pass `tokens_in=usage.tokens_in, tokens_out=usage.billable_out` to `complete_ok`, and replace `cost_usd = 0.0` with `cost_usd, _ = compute_cost(usage, version.model)` (pass `cost_usd or 0.0` to `complete_ok` to keep its float contract). After `complete_ok`, call `_record_telemetry(..., status="ok", result=result, duration_s=elapsed_s, review_item_count=len(review))`. In the non-JSON early-return branch, call `_record_telemetry(..., status="error", error_class="NonJsonOutput", result=result, duration_s=elapsed_s)` before returning.
- In `_finalize_annotation`: after `update_item_status(..., "review_ready")`, call `_record_telemetry(..., status="ok", result=result, duration_s=elapsed_s, review_item_count=len(review) if structured and review else 0)`.
- In `run_job`'s `except Exception as exc:` handler: after the existing error handling, add `await _record_telemetry(db, run_telemetry_repo, telemetry_ctx, kind=kind, item=item, version=version, status="error", error_class=type(exc).__name__)` — media/usage unknown there is fine (NULLs).

- [ ] **Step 7.5: Thread the routes**

In `backend/app/routes/jobs.py` `_run_in_bg` and `backend/app/routes/studio.py` `_run_in_bg`, add to the `run_job(...)` call:

```python
            run_telemetry_repo=ctx.run_telemetry_repo,
            telemetry_ctx=ctx.telemetry_ctx,
```

(Both call through LiveCtx, whose delegators were added in Step 7.3.)

- [ ] **Step 7.6: Run the new tests and the regression suite**

Run: `python -m pytest tests/integration/test_annotator_telemetry.py tests/integration/test_annotator_worker.py tests/unit/test_context_delegation.py -v`
Expected: all PASS. The pre-existing worker test must still pass with the new required kwargs — it will fail first; update its `run_job(...)` call with the two new kwargs (`RunTelemetryRepo()` and the `TCTX` pattern above).

- [ ] **Step 7.7: Run the no-sync-fs and import-linter guards**

Run: `python -m pytest tests/unit/test_no_sync_fs_in_async.py -q && lint-imports`
Expected: PASS / `Contracts: kept`. (`Path.suffix` is pure string parsing, not fs I/O.)

- [ ] **Step 7.8: Commit**

```bash
git add backend/app/context.py backend/app/services/annotator.py backend/app/routes/jobs.py backend/app/routes/studio.py tests/integration/test_annotator_telemetry.py tests/integration/test_annotator_worker.py
git commit -m "feat(telemetry): capture run telemetry in both finalize paths; billable studio tokens + real cost"
```

---

### Task 8: Batched `ClipCacheRepo.get_many_by_ids`

**Files:**
- Modify: `backend/app/repositories/clip_cache.py`
- Test: `tests/integration/test_clip_cache_get_many.py`

- [ ] **Step 8.1: Write the failing test**

First read `backend/app/repositories/clip_cache.py::upsert` to learn the exact upsert signature, then write (adjust the `upsert` call to it — the test seeds 3 cached clips):

```python
# tests/integration/test_clip_cache_get_many.py
"""get_many_by_ids: batched read via chunked_in_clause; constant query
count regardless of key-list size (ADR 0046)."""

import pytest

from backend.app.repositories.clip_cache import ClipCacheRepo
from tests._helpers.query_count import assert_query_count


async def _seed(db, repo, clip_id: int):
    # Use the repo's real upsert signature (read it first); the essential
    # fields are provider_id='catdv', provider_clip_id=str(clip_id),
    # duration_secs, and canonical_json containing media info.
    await repo.upsert(
        db,
        provider_id="catdv",
        provider_clip_id=str(clip_id),
        duration_secs=12.5,
        canonical_json={
            "name": f"clip {clip_id}",
            "duration_secs": 12.5,
            "media": {"cached_path": f"/x/{clip_id}.mov", "mime_type": "video/quicktime"},
        },
    )


@pytest.mark.asyncio
async def test_get_many_returns_rows(db):
    repo = ClipCacheRepo()
    for cid in (1, 2, 3):
        await _seed(db, repo, cid)
    rows = await repo.get_many_by_ids(db, "catdv", [1, 3, 999])
    assert set(rows) == {1, 3}
    assert rows[1]["duration_secs"] == 12.5


@pytest.mark.asyncio
async def test_get_many_constant_query_count(db):
    repo = ClipCacheRepo()
    for cid in range(1, 30):
        await _seed(db, repo, cid)
    async with assert_query_count(db, 1):
        await repo.get_many_by_ids(db, "catdv", list(range(1, 11)))
    async with assert_query_count(db, 1):
        await repo.get_many_by_ids(db, "catdv", list(range(1, 30)))
```

If `upsert`'s real signature differs (it likely takes a `CanonicalClip` or more columns), adapt `_seed` accordingly — the assertions are the contract, not the seeding mechanics. `assert_query_count` is `tests/_helpers/query_count.py`; check its exact call form before use.

- [ ] **Step 8.2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_clip_cache_get_many.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'get_many_by_ids'`.

- [ ] **Step 8.3: Implement with `chunked_in_clause`**

`chunked_in_clause` (in `backend/app/repositories/_batch.py`) takes **2-tuple keys** and yields `(sql_fragment, params)` pairs where the fragment is `"(?, ?), (?, ?), …"` for a `WHERE (a, b) IN (…)` clause. Add to `ClipCacheRepo` (import `chunked_in_clause` from `backend.app.repositories._batch` at the top if not already imported):

```python
    async def get_many_by_ids(
        self, conn: aiosqlite.Connection, provider_id: str, clip_ids: list[int]
    ) -> dict[int, dict]:
        """Batched: {clip_id: {'duration_secs': float, 'canonical_json': dict}}
        for the ids present in cache. Missing ids are simply absent."""
        out: dict[int, dict] = {}
        if not clip_ids:
            return out
        keys = [(provider_id, str(c)) for c in clip_ids]
        for fragment, params in chunked_in_clause(keys):
            cur = await conn.execute(
                "SELECT provider_clip_id, duration_secs, canonical_json "
                f"FROM clip_cache WHERE (provider_id, provider_clip_id) IN ({fragment})",
                tuple(params),
            )
            for pid, dur, cj in await cur.fetchall():
                out[int(pid)] = {
                    "duration_secs": dur,
                    "canonical_json": json.loads(cj) if cj else {},
                }
        return out
```

Provider-id note: pass the same string the active adapter writes into `clip_cache.provider_id` — verify with `grep -rn 'provider_id' backend/app/archive/providers/*/adapter.py | head -5`. In practice it matches `settings.archive_provider` (`"catdv"` / `"fs"`); if it doesn't, thread the adapter's constant instead.

- [ ] **Step 8.4: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_clip_cache_get_many.py tests/integration/test_clip_cache_repo.py -v`
Expected: all PASS (existing repo tests untouched).

- [ ] **Step 8.5: Commit**

```bash
git add backend/app/repositories/clip_cache.py tests/integration/test_clip_cache_get_many.py
git commit -m "feat(telemetry): batched clip_cache.get_many_by_ids"
```

---

### Task 9: Estimate endpoint `POST /api/jobs/estimate`

**Files:**
- Modify: `backend/app/services/run_estimator.py` (add `estimate_for_clip_ids`)
- Modify: `backend/app/routes/jobs.py` (thin route)
- Test: `tests/integration/test_estimate_query_count.py`

- [ ] **Step 9.1: Write the failing test**

```python
# tests/integration/test_estimate_query_count.py
"""estimate_for_clip_ids: DB-first (offline-safe), query count does not
scale with clip count (ADR 0046)."""

import pytest

from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.run_telemetry import RunTelemetryRepo
from backend.app.services.run_estimator import estimate_for_clip_ids
from tests._helpers.query_count import assert_query_count
from tests.integration.test_clip_cache_get_many import _seed


@pytest.mark.asyncio
async def test_estimate_for_clip_ids_smoke_and_query_count(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t", description=None, body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"}, model="gemini-2.5-flash-lite",
    )
    cache = ClipCacheRepo()
    for cid in range(1, 101):
        await _seed(db, cache, cid)

    result_small = await estimate_for_clip_ids(
        db, clip_cache_repo=cache, run_telemetry_repo=RunTelemetryRepo(),
        prompts_repo=prompts, provider_id="catdv",
        clip_ids=list(range(1, 11)), prompt_version_id=vid,
    )
    assert result_small["tokens_in"] > 0
    assert result_small["confidence"] == "rough"
    assert result_small["n_clips"] == 10

    # Query count must be the same for 10 and 100 clips (one cache chunk,
    # one prompt read, ≤3 aggregate reads per media-kind group).
    async with assert_query_count(db, 8):
        await estimate_for_clip_ids(
            db, clip_cache_repo=cache, run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts, provider_id="catdv",
            clip_ids=list(range(1, 11)), prompt_version_id=vid,
        )
    async with assert_query_count(db, 8):
        await estimate_for_clip_ids(
            db, clip_cache_repo=cache, run_telemetry_repo=RunTelemetryRepo(),
            prompts_repo=prompts, provider_id="catdv",
            clip_ids=list(range(1, 101)), prompt_version_id=vid,
        )


@pytest.mark.asyncio
async def test_uncached_clips_estimated_as_unknown(db):
    prompts = PromptsRepo()
    _, vid = await prompts.create_with_initial_version(
        db, name="t2", description=None, body="describe",
        target_map={"scenes": {"kind": "markers"}},
        output_schema={"type": "object"}, model="gemini-2.5-flash-lite",
    )
    result = await estimate_for_clip_ids(
        db, clip_cache_repo=ClipCacheRepo(),
        run_telemetry_repo=RunTelemetryRepo(), prompts_repo=prompts,
        provider_id="catdv", clip_ids=[777], prompt_version_id=vid,
    )
    # Unknown clip → conservative defaults, never an exception.
    assert result["n_clips"] == 1
    assert result["confidence"] == "rough"
```

- [ ] **Step 9.2: Run to verify it fails**

Run: `python -m pytest tests/integration/test_estimate_query_count.py -v`
Expected: FAIL — `ImportError: cannot import name 'estimate_for_clip_ids'`.

- [ ] **Step 9.3: Add the service function**

Append to `backend/app/services/run_estimator.py`:

```python
from backend.app.media_kind import classify_media_kind  # top of file

_UNKNOWN_CLIP_DURATION_SECS = 60.0  # conservative default for uncached clips


async def estimate_for_clip_ids(
    conn,
    *,
    clip_cache_repo,
    run_telemetry_repo,
    prompts_repo,
    provider_id: str,
    clip_ids: list[int],
    prompt_version_id: int,
) -> dict:
    """DB-first estimate for the UI: durations/kinds from clip_cache
    (offline-safe), history from run_telemetry. Uncached clips get a
    conservative default duration rather than failing the whole estimate."""
    version = await prompts_repo.get_version(conn, prompt_version_id)
    cached = await clip_cache_repo.get_many_by_ids(conn, provider_id, clip_ids)
    clips: list[ClipEstimateInput] = []
    for cid in clip_ids:
        row = cached.get(cid)
        if row is None:
            clips.append(ClipEstimateInput(
                clip_id=cid, media_kind="video+audio",
                duration_secs=_UNKNOWN_CLIP_DURATION_SECS,
            ))
            continue
        cj = row["canonical_json"] or {}
        media = cj.get("media") or {}
        path = media.get("cached_path") or media.get("upstream_handle") or cj.get("name")
        clips.append(ClipEstimateInput(
            clip_id=cid,
            media_kind=classify_media_kind(str(path) if path else None),
            duration_secs=row["duration_secs"],
        ))
    est = await estimate_clips(
        conn, run_telemetry_repo, clips,
        prompt_body=version.body, schema=version.output_schema,
        model=version.model,
    )
    return {
        "tokens_in": est.tokens_in,
        "tokens_out_p50": est.tokens_out_p50,
        "tokens_out_p90": est.tokens_out_p90,
        "cost_usd_p50": est.cost_usd_p50,
        "cost_usd_p90": est.cost_usd_p90,
        "confidence": est.confidence,
        "n_samples": est.n_samples,
        "n_clips": est.n_clips,
    }
```

- [ ] **Step 9.4: Add the thin route**

In `backend/app/routes/jobs.py`:

```python
from backend.app.services.run_estimator import estimate_for_clip_ids


class EstimateRequest(BaseModel):
    prompt_version_id: int
    clip_ids: list[int]


@router.post("/estimate")
async def estimate_job(request: Request, body: EstimateRequest):
    """Pre-run cost estimate. CoreCtx only — fully offline-capable.
    Advisory: failures here must never block launching a run (the UI
    treats errors as 'no estimate shown')."""
    ctx = get_core_ctx(request)
    try:
        return await estimate_for_clip_ids(
            ctx.db,
            clip_cache_repo=ctx.clip_cache_repo,
            run_telemetry_repo=ctx.run_telemetry_repo,
            prompts_repo=ctx.prompts_repo,
            provider_id=ctx.settings.archive_provider,
            clip_ids=body.clip_ids,
            prompt_version_id=body.prompt_version_id,
        )
    except LookupError:
        raise HTTPException(404, "prompt version not found") from None
```

Route order note: FastAPI matches in declaration order — `POST /estimate` must be declared **before** any `/{job_id}`-style POST route if one exists (today only GET `/{job_id}` exists, but keep `/estimate` above it anyway).

- [ ] **Step 9.5: Run tests to verify they pass**

Run: `python -m pytest tests/integration/test_estimate_query_count.py -v && lint-imports`
Expected: PASS / `Contracts: kept`. If the query count exceeds the bound, count the actual statements (1 prompt read + 1 cache chunk + 3×(input,out-l1,out-l2) per kind group — all clips here are one kind = 5–6 total) and tighten the bound to the observed constant; the contract is *same count at 10 and 100*.

- [ ] **Step 9.6: Commit**

```bash
git add backend/app/services/run_estimator.py backend/app/routes/jobs.py tests/integration/test_estimate_query_count.py
git commit -m "feat(telemetry): POST /api/jobs/estimate (offline-safe, constant queries)"
```

---

### Task 10: UI — estimate in batches modal + studio header

JS/templates have no automated harness here (no node — ADR 0001); these steps are verified by the manual acceptance flows. Keep changes minimal and use the shared conventions (`Alpine.store('toast')`, design tokens, `fmt*` helpers).

**Files:**
- Modify: `backend/app/static/format.js`
- Modify: `backend/app/templates/pages/batches.html`
- Modify: `backend/app/static/studioStore.js`
- Modify: `backend/app/templates/pages/_studio_header.html`

- [ ] **Step 10.1: Add `fmtUsd` to `format.js`**

Next to `fmtBytes`, and export it the same way (`window.fmtUsd = fmtUsd;`):

```javascript
  function fmtUsd(x) {
    if (x === null || x === undefined || isNaN(Number(x))) return "—";
    const n = Number(x);
    return "$" + (n < 0.1 ? n.toFixed(3) : n.toFixed(2));
  }
```

- [ ] **Step 10.2: Batches modal estimate line**

In `backend/app/templates/pages/batches.html`, inside the `batchesPage()` component:

(a) Add state and a refresh method (near `canStart()`):

```javascript
      estimate: null,
      _estimateKey: "",
      async refreshEstimate() {
        // One estimate call per kind that has a prompt picked; sum ranges.
        const groups = [];
        const byKind = {};
        for (const c of this.selectedClips()) (byKind[c.kind] ||= []).push(c.id);
        for (const [kind, ids] of Object.entries(byKind)) {
          if (this.kindPrompt[kind]) groups.push({ pv: Number(this.kindPrompt[kind]), ids });
        }
        const key = JSON.stringify(groups);
        if (key === this._estimateKey) return;   // selection unchanged
        this._estimateKey = key;
        if (!groups.length) { this.estimate = null; return; }
        try {
          const parts = await Promise.all(groups.map(async (g) => {
            const r = await fetch("/api/jobs/estimate", {
              method: "POST", headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ prompt_version_id: g.pv, clip_ids: g.ids }),
            });
            if (!r.ok) throw new Error("HTTP " + r.status);
            return r.json();
          }));
          const order = { rough: 0, fair: 1, good: 2 };
          this.estimate = parts.reduce((acc, p) => ({
            cost_usd_p50: (acc.cost_usd_p50 ?? 0) + (p.cost_usd_p50 ?? 0),
            cost_usd_p90: (acc.cost_usd_p90 ?? 0) + (p.cost_usd_p90 ?? 0),
            tokens_in: (acc.tokens_in ?? 0) + p.tokens_in,
            n_samples: Math.max(acc.n_samples ?? 0, p.n_samples),
            confidence: order[p.confidence] < order[acc.confidence ?? "good"] ? p.confidence : (acc.confidence ?? p.confidence),
          }), {});
        } catch (e) {
          console.error("estimate failed", e);   // advisory — no toast, no block
          this.estimate = null;
        }
      },
      estimateLabel() {
        if (!this.estimate) return "";
        const e = this.estimate;
        return `Estimated: ${fmtUsd(e.cost_usd_p50)} – ${fmtUsd(e.cost_usd_p90)}`
          + ` · ~${(e.tokens_in / 1e6).toFixed(1)}M tokens in`
          + ` · confidence: ${e.confidence}`
          + (e.n_samples ? ` (${e.n_samples} prior runs)` : "");
      },
```

(b) In the modal markup, immediately above/beside the Start button row, add a line that re-fires the refresh whenever its reactive deps change (`x-effect` reruns on `selectedClips()`/`kindPrompt` mutations):

```html
<div class="muted" style="font-size: var(--font-sm);"
     x-effect="refreshEstimate()"
     x-text="estimateLabel()"></div>
```

(Locate the Start button by searching for `canStart()` in the template; match the surrounding markup/classes — reuse the existing muted-text styling pattern rather than new CSS.)

- [ ] **Step 10.3: Studio header estimate**

(a) In `backend/app/static/studioStore.js`, add to the store object (near `runOnFocusedClip`):

```javascript
    estimateLabel: '',
    _estimateKey: '',
    async refreshEstimate() {
      const vid = this.activeVersionId, cid = this.focusedClipId;
      const key = `${vid}:${cid}`;
      if (key === this._estimateKey) return;
      this._estimateKey = key;
      if (!vid || !cid) { this.estimateLabel = ''; return; }
      try {
        const r = await fetch('/api/jobs/estimate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ prompt_version_id: vid, clip_ids: [cid] }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const e = await r.json();
        this.estimateLabel =
          `~${fmtUsd(e.cost_usd_p50)}–${fmtUsd(e.cost_usd_p90)} (${e.confidence})`;
      } catch (err) {
        console.error('estimate failed', err);  // advisory only
        this.estimateLabel = '';
      }
    },
```

(b) In `backend/app/templates/pages/_studio_header.html`, next to the existing run button (`.studio-run-btn`, around line 86), add:

```html
<span class="muted" style="font-size: var(--font-sm);"
      x-effect="$store.studio.refreshEstimate()"
      x-text="$store.studio.estimateLabel"></span>
```

(`x-effect` reads `activeVersionId`/`focusedClipId` through `refreshEstimate`, so it reruns on both changes. Verify the store is registered as `Alpine.store('studio', ...)` in `studioStore.js` — if the name differs, match it.)

- [ ] **Step 10.4: Manual smoke check**

Use the **server-start skill** (never raw uvicorn — license-seat discipline), then: open `/batches`, click New batch, pick ≥1 clip + a prompt → the estimate line appears and updates when selection changes. Open `/studio`, focus a clip with an active version → estimate appears next to Run. Stop with the **server-stop skill**. No CatDV writes are involved (estimates are read-only) — this respects the verification-sequencing rule.

- [ ] **Step 10.5: Commit**

```bash
git add backend/app/static/format.js backend/app/static/studioStore.js backend/app/templates/pages/batches.html backend/app/templates/pages/_studio_header.html
git commit -m "feat(telemetry): pre-run cost estimate in batches modal + studio header"
```

---

### Task 11: Full verification + ADR + docs

**Files:**
- Create: `docs/adr/00XX-run-telemetry-local-first.md` (next free number — check `ls docs/adr/ | tail -1` AND open PRs per the worktree-collision note)
- Modify: `docs/decisions.md`

- [ ] **Step 11.1: Full test suite + guards**

Run: `python -m pytest tests/ -q && lint-imports`
Expected: everything green, `Contracts: kept`. Fix any fallout before proceeding (report failures honestly — do not skip).

- [ ] **Step 11.2: Sanity-check seed constants against one real row (optional but recommended)**

If a dev server with live Gemini is available: run one studio run on a short video clip, then `sqlite3 <data_dir>/app.db "SELECT media_duration_secs, tokens_in_video, tokens_in_audio FROM run_telemetry ORDER BY id DESC LIMIT 1"` and compare `tokens/duration` to the seeds in `run_estimator.py` (≈258 video, ≈32 audio at default resolution). Adjust seeds if off by >20% and note the measured value in a comment. If no live access, skip — calibration self-corrects after 3 runs.

- [ ] **Step 11.3: Write the ADR**

MADR-lite format (see `docs/adr/0001-*.md`), covering the session's design calls in one entry: (1) local-first telemetry — table doubles as Phase-2 outbox, cloud collector deferred until first external deployment; (2) one `run_telemetry` table for both run kinds instead of widening `studio_run`/`annotations`; (3) prompt identity = SHA-256 of the template body, not the rendered prompt; (4) billable output = candidates + thinking everywhere (studio_run.tokens_out changes meaning for new rows); (5) est_* stamped immediately pre-call rather than at enqueue (deviation from spec §1, rationale: no jobs schema change, same outcome-blindness); (6) budget limits deferred (no admin console). Add the row to `docs/decisions.md`.

- [ ] **Step 11.4: Commit**

```bash
git add docs/adr/ docs/decisions.md
git commit -m "docs(adr): run telemetry local-first decisions"
```

- [ ] **Step 11.5: Manual acceptance flows**

Walk the 7 numbered flows at the bottom of `docs/specs/2026-06-07-run-telemetry-cost-estimation-design.md`. Flows 2–3 call Gemini (cost: pennies) and need CatDV/GCS online — follow the CatDV seat discipline (server-start/server-stop skills, check for existing instances first). Tick each flow off or report exactly which step broke.

- [ ] **Step 11.6: Finish the branch**

Use the **superpowers:finishing-a-development-branch** skill: push `feat/run-telemetry-phase1`, open a PR to `main` (check for divergence and rebase first per house git rules).

---

## Self-review notes (already applied)

- **Spec coverage:** §1 capture → Task 3+7; §2 table/outbox → Task 1; §3 media_kind → Task 2; §4 hashing → Task 3; §5 pricing → Task 4; §6 estimator → Tasks 5+6+9; §7 UI → Task 10; error-handling section → Tasks 4/6/7 (never-raise paths tested); testing section → mapped 1:1; manual flows → Task 11.5. Phase 2 items intentionally absent.
- **Type consistency:** `TokenUsage.billable_out` used by repo SQL comment, studio fix, and estimator docs; `RunTelemetryRecord` field names = `_COLS` = migration columns (Task 1 test cross-checks); `estimate_clips` signature matches Task 6 tests, Task 7 annotator call, and Task 9 wrapper.
- **Verified against source during planning:** `chunked_in_clause` (2-tuple keys → `(fragment, params)` pairs) and `assert_query_count(conn, max_n)` (async CM) — the code above uses their real APIs. **Known soft spots called out inline:** `clip_cache.upsert` seeding signature (Task 8.1), `clip_cache.provider_id` constant (Task 8.3), Alpine store name (Task 10.3), batches modal anchor (Task 10.2), migration/ADR numbering collisions (Tasks 1.0, 11.3). Each step says to read the real source first — the contracts in the tests are fixed; the mechanics may need 1-line adaptations.
