# Gemini Live Clip Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a one-button Czech voice assistant to the clip-detail page that sees the current proxy frame plus committed + draft annotations, talks back via Gemini Live native audio, and stores transcripts + summaries in a read-only History tab.

**Architecture:** Browser opens WSS directly to Gemini Developer API using single-use ephemeral tokens minted server-side via `authTokens.create`. Backend never touches audio bytes — its job is token minting, system-prompt + annotation-context assembly, transcript persistence, and post-session summarization via non-Live `generateContent`. Past sessions live in a new `live_sessions` SQLite table and surface as a History tab next to Published/Draft.

**Tech Stack:** FastAPI + `aiosqlite` + `httpx` (backend), Alpine.js + `AudioContext`/`AudioWorkletNode` + raw `WebSocket` (browser), Gemini Developer API (`generativelanguage.googleapis.com`) for Live + non-Live calls, pytest with the existing `conftest` fixtures for tests.

**Spec:** `docs/specs/2026-05-23-gemini-live-clip-assistant-design.md` (commits `a49d7bf` + `7605b14`).

---

## Pinned implementation details

These resolve the "open items" in §10 of the spec so every task below can be concrete:

- **Model name:** `gemini-2.5-flash-preview-native-audio-dialog` (Live native-audio dialog model; the Live-capable variant of the `gemini-2.5-flash` line). Keep configurable via `gemini_live_model`.
- **Voice name:** `"Aoede"` (Gemini Live prebuilt voice; speaks whichever language is set via `speechConfig.languageCode`, including `cs-CZ`). Configurable via `gemini_live_voice`.
- **`authTokens.create` endpoint (v1alpha):**
  `POST https://generativelanguage.googleapis.com/v1alpha/auth_tokens?key=<GEMINI_API_KEY>`
  Body: `{ "uses": 1, "expireTime": "<rfc3339>", "newSessionExpireTime": "<rfc3339>", "bidiGenerateContentSetup": { ...full setup... } }`
  Response: `{ "name": "tokens/<token>" }`.
- **WSS URL:** `wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent?access_token=tokens/<token>`
- **Non-Live summarize endpoint:**
  `POST https://generativelanguage.googleapis.com/v1beta/models/<settings.gemini_model>:generateContent?key=<GEMINI_API_KEY>`
- **`googleSearch` grounding citations:** stored as part of the transcript JSON (no special UI in v1). The History panel renders them inline if present, but no chips on the live strip.
- **Migration number:** `0010_live_sessions.sql`.

---

## File structure

### New backend files (clear single responsibility each)

| File | Responsibility |
|---|---|
| `backend/migrations/0010_live_sessions.sql` | Create `live_sessions` table + index. |
| `backend/app/models/live_session.py` | Pydantic `LiveSession` model (no DB logic). |
| `backend/app/repositories/live_sessions.py` | `LiveSessionsRepo` — CRUD + state transitions + stale-pending cleanup. The **only** consumer of the table. |
| `backend/app/services/live_context.py` | Pure function `build_context_text(clip, draft) -> str` producing the two Czech blocks. Pure & easily tested. |
| `backend/app/services/live_sessions.py` | `assemble_setup_payload`, `mint_ephemeral_token`, `summarize`. The integration glue between repo + Czech-context builder + Gemini HTTP calls. |
| `backend/app/routes/live.py` | FastAPI router for `/api/live/*`. |
| `backend/seeds/live_system_instruction_cs.json` | Seed JSON for the `live.system_instruction.cs` prompt. |

### New frontend files

| File | Responsibility |
|---|---|
| `backend/app/static/audio-worklet-recorder.js` | `AudioWorkletProcessor` that emits 16 kHz Int16 PCM chunks. Loaded as a worklet module — not a plain script. |
| `backend/app/static/liveSession.js` | Alpine component `liveSession(clipId, config)`. WSS, audio in/out, frames, inactivity, function-tool handling. |

### Modified

| File | Why |
|---|---|
| `backend/app/settings.py` | 4 new fields (`gemini_api_key`, `gemini_live_model`, `gemini_live_voice`, `gemini_live_inactivity_s`). |
| `backend/app/main.py` | Register `live` router; trigger pending-row cleanup at lifespan start. |
| `backend/app/seed.py` | Add `seed_live_system_instruction` that mirrors `seed_default_prompt`. |
| `backend/app/templates/pages/clip_detail.html` | `🎤 Live` button; overlay header during session; transcript strip; mount `liveSession()`; pass model/voice/inactivity to the component. |
| `backend/app/templates/pages/_anno_panels.html` | Add **History** tab. |
| `backend/app/routes/pages.py` | New endpoint `GET /clips/{id}/live-history` returning the History tab partial. |
| `backend/app/templates/pages/_anno_live_history.html` | New partial rendered by the History endpoint. |
| `deploy/enable-gemini-live.sh` | One-shot gcloud script. |
| `README.md` | Document the new env vars + the gcloud script. |

---

## Phases & review checkpoints

The implementation is split into eight phases. A checkpoint at the end of each phase is the natural point to stop, run `pytest`, and either review the work in-session or commit and continue in a fresh subagent.

1. **Phase 1 — Settings, schema, model, repository** (foundation, no Gemini calls).
2. **Phase 2 — Czech context builder** (pure function, exhaustive table-driven tests).
3. **Phase 3 — Seed the system-instruction prompt**.
4. **Phase 4 — Live-session service** (token mint + summarize, with `httpx` mocked).
5. **Phase 5 — Routes + lifespan wiring**.
6. **Phase 6 — Browser audio + WSS pipeline** (mostly manual verification).
7. **Phase 7 — Template integration & History tab**.
8. **Phase 8 — Infra script, README, manual verification**.

---

# Phase 1 — Settings, schema, model, repository

## Task 1: Settings additions

**Files:**
- Modify: `backend/app/settings.py`

- [ ] **Step 1: Add the 4 new settings fields**

Open `backend/app/settings.py`. Inside the `Settings` class, after the existing `gemini_model` line, add:

```python
    gemini_api_key: str | None = None
    gemini_live_model: str = "gemini-2.5-flash-preview-native-audio-dialog"
    gemini_live_voice: str = "Aoede"
    gemini_live_inactivity_s: int = 60
```

`gemini_api_key` is intentionally `None`-able so the app still boots without it (Live feature simply won't work until `.env` provides it).

- [ ] **Step 2: Commit**

```bash
git add backend/app/settings.py
git commit -m "feat(settings): add gemini live config fields"
```

---

## Task 2: Migration `0010_live_sessions.sql`

**Files:**
- Create: `backend/migrations/0010_live_sessions.sql`
- Test: `tests/integration/test_live_sessions_migration.py`

- [ ] **Step 1: Write the failing migration test**

Create `tests/integration/test_live_sessions_migration.py`:

```python
import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_live_sessions_table_exists_after_migrations(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='live_sessions'"
        )
        assert await cur.fetchone() is not None


@pytest.mark.asyncio
async def test_live_sessions_columns(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute("PRAGMA table_info(live_sessions)")
        cols = {row[1] for row in await cur.fetchall()}
    assert cols == {
        "id", "clip_id", "prompt_version", "state",
        "started_at", "ended_at", "end_reason",
        "transcript_json", "summary_cs",
        "frame_count", "search_calls", "created_at",
    }


@pytest.mark.asyncio
async def test_live_sessions_index_present(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='ix_live_sessions_clip'"
        )
        assert await cur.fetchone() is not None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_live_sessions_migration.py -v
```

Expected: all three tests FAIL (no migration yet → `live_sessions` table not present).

- [ ] **Step 3: Write the migration file**

Create `backend/migrations/0010_live_sessions.sql`:

```sql
-- 0010: live_sessions — Gemini Live API conversation records per clip.

CREATE TABLE live_sessions (
  id              TEXT PRIMARY KEY,            -- uuid v4 chosen by backend
  clip_id         INTEGER NOT NULL,
  prompt_version  INTEGER,                     -- which live.system_instruction.cs version was used
  state           TEXT NOT NULL,               -- pending | active | ended | failed
  started_at      TEXT,                        -- iso8601 utc
  ended_at        TEXT,                        -- iso8601 utc
  end_reason      TEXT,                        -- user_stop | voice_stop | inactivity | navigate | error
  transcript_json TEXT,                        -- json array of {role,text,ts,kind}
  summary_cs      TEXT,                        -- czech summary produced post-session
  frame_count     INTEGER NOT NULL DEFAULT 0,
  search_calls    INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX ix_live_sessions_clip
  ON live_sessions (clip_id, created_at DESC);
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/integration/test_live_sessions_migration.py -v
```

Expected: all three PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/0010_live_sessions.sql tests/integration/test_live_sessions_migration.py
git commit -m "feat(db): add live_sessions table migration 0010"
```

---

## Task 3: `LiveSession` model

**Files:**
- Create: `backend/app/models/live_session.py`
- Test: `tests/unit/test_live_session_model.py`

- [ ] **Step 1: Write the failing model test**

Create `tests/unit/test_live_session_model.py`:

```python
from datetime import datetime, timezone

import pytest

from backend.app.models.live_session import LiveSession


def test_live_session_minimal_construct():
    s = LiveSession(id="abc", clip_id=42, state="pending")
    assert s.id == "abc"
    assert s.clip_id == 42
    assert s.state == "pending"
    assert s.frame_count == 0
    assert s.search_calls == 0
    assert s.transcript_json is None
    assert s.summary_cs is None


def test_live_session_invalid_state_rejected():
    with pytest.raises(ValueError):
        LiveSession(id="x", clip_id=1, state="bogus")


def test_live_session_invalid_end_reason_rejected():
    with pytest.raises(ValueError):
        LiveSession(id="x", clip_id=1, state="ended", end_reason="nope")


def test_live_session_full_construct_roundtrip():
    now = datetime.now(timezone.utc).isoformat()
    s = LiveSession(
        id="abc", clip_id=42, prompt_version=3, state="ended",
        started_at=now, ended_at=now, end_reason="user_stop",
        transcript_json='[{"role":"user","text":"ahoj","ts":1}]',
        summary_cs="Krátký test.",
        frame_count=2, search_calls=1, created_at=now,
    )
    assert s.end_reason == "user_stop"
    assert s.frame_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_live_session_model.py -v
```

Expected: ImportError — module doesn't exist.

- [ ] **Step 3: Create the model**

Create `backend/app/models/live_session.py`:

```python
from typing import Literal

from pydantic import BaseModel, Field

LiveSessionState = Literal["pending", "active", "ended", "failed"]
EndReason = Literal["user_stop", "voice_stop", "inactivity", "navigate", "error"]


class LiveSession(BaseModel):
    id: str
    clip_id: int
    prompt_version: int | None = None
    state: LiveSessionState
    started_at: str | None = None
    ended_at: str | None = None
    end_reason: EndReason | None = None
    transcript_json: str | None = None
    summary_cs: str | None = None
    frame_count: int = 0
    search_calls: int = 0
    created_at: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/unit/test_live_session_model.py -v
```

Expected: all four PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/live_session.py tests/unit/test_live_session_model.py
git commit -m "feat(models): add LiveSession pydantic model"
```

---

## Task 4: `LiveSessionsRepo` — CRUD + state transitions

**Files:**
- Create: `backend/app/repositories/live_sessions.py`
- Test: `tests/integration/test_live_sessions_repo.py`

- [ ] **Step 1: Write the failing repo test**

Create `tests/integration/test_live_sessions_repo.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.models.live_session import LiveSession
from backend.app.repositories.live_sessions import LiveSessionsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def conn(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as c:
        await apply_migrations(c, MIGRATIONS)
        yield c


@pytest.mark.asyncio
async def test_insert_pending_and_get(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=3)
    s = await repo.get(conn, "abc")
    assert s.state == "pending"
    assert s.clip_id == 42
    assert s.prompt_version == 3


@pytest.mark.asyncio
async def test_mark_active_sets_started_at(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    s = await repo.get(conn, "abc")
    assert s.state == "active"
    assert s.started_at is not None


@pytest.mark.asyncio
async def test_mark_ended_persists_transcript_and_reason(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    transcript = [{"role": "user", "text": "ahoj", "ts": 1}]
    await repo.mark_ended(
        conn, "abc",
        end_reason="user_stop",
        transcript_json=json.dumps(transcript, ensure_ascii=False),
        frame_count=2, search_calls=1,
    )
    s = await repo.get(conn, "abc")
    assert s.state == "ended"
    assert s.end_reason == "user_stop"
    assert s.ended_at is not None
    assert s.frame_count == 2
    assert s.search_calls == 1
    assert json.loads(s.transcript_json) == transcript


@pytest.mark.asyncio
async def test_set_summary_is_idempotent(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(conn, "abc", end_reason="user_stop", transcript_json="[]")
    await repo.set_summary(conn, "abc", "První shrnutí.")
    # second call must be a no-op (returns False) and not overwrite
    overwrote = await repo.set_summary(conn, "abc", "Druhý pokus.")
    assert overwrote is False
    s = await repo.get(conn, "abc")
    assert s.summary_cs == "První shrnutí."


@pytest.mark.asyncio
async def test_list_by_clip_desc(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="a", clip_id=42, prompt_version=None)
    await repo.insert_pending(conn, id="b", clip_id=42, prompt_version=None)
    await repo.insert_pending(conn, id="c", clip_id=99, prompt_version=None)
    rows = await repo.list_by_clip(conn, 42)
    ids = [r.id for r in rows]
    assert set(ids) == {"a", "b"}


@pytest.mark.asyncio
async def test_get_missing_raises(conn):
    repo = LiveSessionsRepo()
    with pytest.raises(LookupError):
        await repo.get(conn, "no-such-id")


@pytest.mark.asyncio
async def test_cleanup_stale_pending_reaps_only_old_pending(conn):
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="old", clip_id=1, prompt_version=None)
    await repo.insert_pending(conn, id="fresh", clip_id=1, prompt_version=None)
    await repo.insert_pending(conn, id="active-old", clip_id=1, prompt_version=None)
    await repo.mark_active(conn, "active-old")

    # Backdate `old` and `active-old` by 2 hours.
    two_h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    await conn.execute("UPDATE live_sessions SET created_at = ? WHERE id IN ('old','active-old')", (two_h_ago,))
    await conn.commit()

    reaped = await repo.cleanup_stale_pending(conn, older_than_hours=1)
    assert reaped == 1
    # `old` gone; `fresh` and `active-old` remain.
    assert (await repo.get(conn, "fresh")).id == "fresh"
    assert (await repo.get(conn, "active-old")).id == "active-old"
    with pytest.raises(LookupError):
        await repo.get(conn, "old")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_live_sessions_repo.py -v
```

Expected: ImportError on `LiveSessionsRepo`.

- [ ] **Step 3: Implement the repository**

Create `backend/app/repositories/live_sessions.py`:

```python
"""LiveSessionsRepo — CRUD + state machine for the `live_sessions` table.

State transitions:
  pending  ──(mark_active)──▶  active  ──(mark_ended)──▶  ended
                  └────────(mark_ended)────────────────▶  ended  (mic denied, etc.)

`set_summary` is idempotent — once non-null it never overwrites.
"""
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.live_session import LiveSession


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_COLS = (
    "id, clip_id, prompt_version, state, started_at, ended_at, end_reason, "
    "transcript_json, summary_cs, frame_count, search_calls, created_at"
)


def _row(r) -> LiveSession:
    return LiveSession(
        id=r[0], clip_id=r[1], prompt_version=r[2], state=r[3],
        started_at=r[4], ended_at=r[5], end_reason=r[6],
        transcript_json=r[7], summary_cs=r[8],
        frame_count=r[9], search_calls=r[10], created_at=r[11],
    )


class LiveSessionsRepo:
    async def insert_pending(
        self, conn: aiosqlite.Connection,
        *, id: str, clip_id: int, prompt_version: int | None,
    ) -> None:
        await conn.execute(
            "INSERT INTO live_sessions (id, clip_id, prompt_version, state, created_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (id, clip_id, prompt_version, _now_iso()),
        )
        await conn.commit()

    async def mark_active(self, conn: aiosqlite.Connection, id: str) -> None:
        await conn.execute(
            "UPDATE live_sessions SET state='active', started_at=? WHERE id=?",
            (_now_iso(), id),
        )
        await conn.commit()

    async def mark_ended(
        self, conn: aiosqlite.Connection, id: str,
        *, end_reason: str, transcript_json: str,
        frame_count: int = 0, search_calls: int = 0,
    ) -> None:
        await conn.execute(
            "UPDATE live_sessions SET state='ended', ended_at=?, end_reason=?, "
            "transcript_json=?, frame_count=?, search_calls=? WHERE id=?",
            (_now_iso(), end_reason, transcript_json, frame_count, search_calls, id),
        )
        await conn.commit()

    async def set_summary(self, conn: aiosqlite.Connection, id: str, summary: str) -> bool:
        """Idempotent — only writes when `summary_cs` is currently NULL. Returns True if written."""
        cur = await conn.execute(
            "UPDATE live_sessions SET summary_cs=? WHERE id=? AND summary_cs IS NULL",
            (summary, id),
        )
        await conn.commit()
        return cur.rowcount > 0

    async def get(self, conn: aiosqlite.Connection, id: str) -> LiveSession:
        cur = await conn.execute(f"SELECT {_COLS} FROM live_sessions WHERE id=?", (id,))
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"live_session {id} not found")
        return _row(row)

    async def list_by_clip(self, conn: aiosqlite.Connection, clip_id: int) -> list[LiveSession]:
        cur = await conn.execute(
            f"SELECT {_COLS} FROM live_sessions WHERE clip_id=? ORDER BY created_at DESC",
            (clip_id,),
        )
        return [_row(r) for r in await cur.fetchall()]

    async def cleanup_stale_pending(self, conn: aiosqlite.Connection, older_than_hours: int = 1) -> int:
        """Delete pending rows older than `older_than_hours`. Returns number deleted."""
        cutoff = (datetime.now(timezone.utc).timestamp()
                  - older_than_hours * 3600)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        cur = await conn.execute(
            "DELETE FROM live_sessions WHERE state='pending' AND created_at < ?",
            (cutoff_iso,),
        )
        await conn.commit()
        return cur.rowcount
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/integration/test_live_sessions_repo.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/live_sessions.py tests/integration/test_live_sessions_repo.py
git commit -m "feat(repo): LiveSessionsRepo CRUD + state transitions + cleanup"
```

---

### ✅ Phase 1 review checkpoint

Run the full suite to make sure nothing else regressed:

```bash
.venv/bin/pytest
```

Expected: green. New tests: `test_live_sessions_migration` (3), `test_live_session_model` (4), `test_live_sessions_repo` (7).

---

# Phase 2 — Czech context builder

This is a **pure function**. No I/O, no Gemini calls, no DB. Heavily table-driven tests because this is where the Czech text shape is locked.

## Task 5: `build_context_text(clip, draft)`

**Files:**
- Create: `backend/app/services/live_context.py`
- Test: `tests/unit/test_live_context.py`

The signature accepts already-built view-model objects so the service stays decoupled from the DB. `clip` is the existing clip view-model (the same object passed to `clip_detail.html`); `draft` is the same dict shape that `_build_draft_for_clip` returns in `routes/pages.py:224`.

- [ ] **Step 1: Write the failing test suite**

Create `tests/unit/test_live_context.py`:

```python
from backend.app.services.live_context import build_context_text


def _published_block_present(out: str) -> bool:
    return "=== Publikované anotace (z CatDV) ===" in out


def _draft_block_present(out: str) -> bool:
    return "=== Rozpracované anotace (můj draft" in out


def _make_clip(**overrides):
    base = dict(
        id=42,
        name="P1010001",
        format="9,5 mm",
        fps=25,
        duration_secs=120.0,
        duration_smpte="00:02:00:00",
        notes="rodinný výlet",
        big_notes="dlouhý popis...",
        markers=[
            dict(
                in_secs=0.0, out_secs=10.0,
                in_smpte="00:00:00:00", out_smpte="00:00:10:00",
                name="Otevírací záběr", description="auto u domu",
            ),
        ],
        fields={
            "pragafilm.rok.natočení": ["1928", "1929"],
            "pragafilm.dekáda.natočení": "20.léta",
            "pragafilm.barva": "false",
        },
    )
    base.update(overrides)
    return base


def _make_draft(**overrides):
    base = dict(
        markers=[
            dict(
                in_secs=5.0, out_secs=8.0,
                in_smpte="00:00:05:00", out_smpte="00:00:08:00",
                name="možná Praha?", description="ulice s tramvají",
            ),
        ],
        fields={"pragafilm.popis.materialu": "rodinné video, ulice"},
        notes="myslím, že je to Praha 1928",
    )
    base.update(overrides)
    return base


# (a) Rich published + non-empty draft
def test_both_blocks_present_with_full_data():
    out = build_context_text(_make_clip(), _make_draft())
    assert _published_block_present(out)
    assert _draft_block_present(out)
    assert "P1010001" in out
    assert "00:02:00:00" in out
    assert "rodinný výlet" in out
    assert "00:00:00:00 – 00:00:10:00" in out
    assert "Otevírací záběr" in out
    assert "pragafilm.rok.natočení: 1928, 1929" in out
    assert "možná Praha?" in out
    assert "myslím, že je to Praha 1928" in out
    # closing marker is required so the model knows the context ends
    assert "(Konec kontextu." in out


# (b) Published-only — empty draft block omitted
def test_omits_empty_draft_block():
    draft = dict(markers=[], fields={}, notes="")
    out = build_context_text(_make_clip(), draft)
    assert _published_block_present(out)
    assert not _draft_block_present(out)


# (c) Draft-only — empty published block (no notes, no fields, no markers)
def test_omits_empty_published_block():
    clip = _make_clip(
        notes="", big_notes="", markers=[], fields={},
    )
    out = build_context_text(clip, _make_draft())
    # name/format/fps/duration are always part of the published block
    # if the *clip itself* exists, so the block is never fully empty —
    # but if notes/big_notes/markers/fields are all empty, we still
    # include the header + the basics (name/format/fps/duration) only.
    assert _published_block_present(out)
    assert "Poznámky:" not in out          # because notes is empty
    assert "Markery" not in out
    assert "Vlastní pole" not in out
    assert _draft_block_present(out)


# (d) Both effectively empty — still emits a minimal valid header
def test_minimal_clip_no_draft():
    clip = _make_clip(
        notes="", big_notes="", markers=[], fields={},
    )
    out = build_context_text(clip, dict(markers=[], fields={}, notes=""))
    assert _published_block_present(out)
    assert not _draft_block_present(out)
    assert "P1010001" in out
    assert "(Konec kontextu." in out


# (e) Mojibake input is repaired before insertion
def test_mojibake_in_published_notes_is_fixed():
    # "rodinný výlet" double-encoded in latin-1 -> utf-8
    bad_notes = "rodinnÃ½ vÃ½let"
    clip = _make_clip(notes=bad_notes)
    out = build_context_text(clip, dict(markers=[], fields={}, notes=""))
    # After view_models._fix the original Czech should appear.
    assert "rodinný výlet" in out
    assert bad_notes not in out


def test_mojibake_in_draft_marker_description_is_fixed():
    draft = _make_draft(
        markers=[
            dict(
                in_secs=0, out_secs=1,
                in_smpte="00:00:00:00", out_smpte="00:00:01:00",
                name="x", description="ulice s tramvajÃ­",
            ),
        ],
    )
    out = build_context_text(_make_clip(), draft)
    assert "ulice s tramvají" in out


def test_pragafilm_fields_only_listed_when_value_non_empty():
    clip = _make_clip(fields={
        "pragafilm.barva": "",
        "pragafilm.dekáda.natočení": "20.léta",
        "pragafilm.rok.natočení": [],
        "pragafilm.popis.materialu": None,
    })
    out = build_context_text(clip, dict(markers=[], fields={}, notes=""))
    assert "pragafilm.dekáda.natočení: 20.léta" in out
    assert "pragafilm.barva" not in out
    assert "pragafilm.rok.natočení" not in out
    assert "pragafilm.popis.materialu" not in out
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_live_context.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the builder**

Create `backend/app/services/live_context.py`:

```python
"""build_context_text — render Czech context blocks for Gemini Live.

Two clearly-labeled sections so the model never conflates committed data
with the operator's working hypothesis:

    === Publikované anotace (z CatDV) ===   ← clip metadata as currently in CatDV
    === Rozpracované anotace (můj draft, ještě neuložené do CatDV) ===

Either block may be omitted entirely when nothing of substance is in it.
All Czech free-text fields are run through `view_models._fix` to repair
mojibake (see catdv-mojibake-display-fix memory + ui/view_models.py).
"""
from typing import Any

from backend.app.ui.view_models import _fix


def _ne(value) -> bool:
    """non-empty: trims strings, treats None / [] / {} / '' as empty."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) > 0
    return True


def _v(value: Any) -> str:
    """Stringify a field value for display — list -> comma joined, else str()."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(_fix(v)) for v in value if _ne(v))
    if isinstance(value, str):
        return _fix(value)
    return str(value)


def _render_marker(m: dict) -> str:
    name = _fix(m.get("name", "") or "")
    desc = _fix(m.get("description", "") or "")
    range_ = f"{m.get('in_smpte','')} – {m.get('out_smpte','')}"
    label = f'„{name}"' if name else ""
    suffix = f" — {desc}" if desc else ""
    return f"- {range_}  {label}{suffix}".rstrip()


def _render_published(clip: dict) -> str | None:
    lines = ["=== Publikované anotace (z CatDV) ==="]
    lines.append(
        f"Název klipu: {_fix(clip.get('name','') or '')}\n"
        f"Formát: {_fix(clip.get('format','') or '')}   "
        f"FPS: {clip.get('fps','')}   "
        f"Délka: {clip.get('duration_smpte','')}"
    )
    notes = clip.get("notes")
    if _ne(notes):
        lines.append("Poznámky:")
        lines.append(_fix(notes))
    big_notes = clip.get("big_notes")
    if _ne(big_notes):
        lines.append("Rozšířené poznámky:")
        lines.append(_fix(big_notes))
    markers = clip.get("markers") or []
    if markers:
        lines.append("Markery (čas → popis):")
        lines.extend(_render_marker(m) for m in markers)
    fields = {k: v for k, v in (clip.get("fields") or {}).items() if _ne(v)}
    if fields:
        lines.append("Vlastní pole (pragafilm.*):")
        for k, v in fields.items():
            lines.append(f"- {k}: {_v(v)}")
    return "\n".join(lines)


def _render_draft(draft: dict) -> str | None:
    markers = draft.get("markers") or []
    fields = {k: v for k, v in (draft.get("fields") or {}).items() if _ne(v)}
    notes = draft.get("notes")
    if not markers and not fields and not _ne(notes):
        return None
    lines = ["=== Rozpracované anotace (můj draft, ještě neuložené do CatDV) ==="]
    if markers:
        lines.append("Draft markery:")
        lines.extend(_render_marker(m) for m in markers)
    if fields:
        lines.append("Draft pole:")
        for k, v in fields.items():
            lines.append(f"- {k}: {_v(v)}")
    if _ne(notes):
        lines.append("Draft poznámky:")
        lines.append(_fix(notes))
    return "\n".join(lines)


def build_context_text(clip: dict, draft: dict) -> str:
    blocks = [b for b in (_render_published(clip), _render_draft(draft)) if b]
    blocks.append("(Konec kontextu. Následuje aktuální snímek a moje otázka.)")
    return "\n\n".join(blocks)
```

> **Note:** If `view_models._fix` doesn't exist as a public symbol, inspect `backend/app/ui/view_models.py` and either import it (`from backend.app.ui.view_models import _fix`) or change the import to whatever the mojibake repair helper is actually named. The memory `catdv-mojibake-display-fix.md` says it lives there.

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/test_live_context.py -v
```

Expected: all 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/live_context.py tests/unit/test_live_context.py
git commit -m "feat(services): czech context block builder for gemini live"
```

---

### ✅ Phase 2 review checkpoint

```bash
.venv/bin/pytest tests/unit/test_live_context.py tests/integration/test_live_sessions_repo.py -v
```

---

# Phase 3 — Seed the Czech system-instruction prompt

The prompt lives in the existing `prompts` system so the operator can iterate the assistant persona through the prompts UI.

## Task 6: Seed JSON file

**Files:**
- Create: `backend/seeds/live_system_instruction_cs.json`

- [ ] **Step 1: Create the seed file**

Create `backend/seeds/live_system_instruction_cs.json`. The structure mirrors `backend/seeds/default_template.json` — inspect that file first to confirm the exact shape (open `backend/seeds/default_template.json` and copy the keys, then fill these values):

```json
{
  "name": "live.system_instruction.cs",
  "description": "Czech system instruction for the Gemini Live clip assistant",
  "prompt": "Jsi asistent pro popis archivních filmových záběrů ze soukromého českého archivu, převážně z let 1920–1950 (formáty 9,5 mm a 16 mm, domácí filmy). Uživatel ti pošle aktuální snímek z proxy videa a metadata k záběru. Metadata obsahují dva bloky: **Publikované anotace** (data již uložená v CatDV — ber je jako daná) a **Rozpracované anotace** (uživatelův draft, jeho pracovní hypotéza — užitečný kontext, ale ne pravda; pokud vidíš ve snímku rozpor s draftem, klidně to zmiň). Tvým úkolem je pomoci popsat scénu, odhadnout lokaci, dataci, identifikovat objekty a historický kontext. Komunikuj výhradně česky, krátkými větami vhodnými pro hlasovou odpověď. Pokud potřebuješ ověřit lokaci, historickou událost, vozidlo, módu nebo jiný detail, použij nástroj `googleSearch`. Pokud uživatel vyjádří přání ukončit konverzaci (např. „konec\", „děkuji, ukonči to\", „dobře, to stačí\"), zavolej nástroj `end_session` s krátkým odůvodněním. Buď stručný a věcný.",
  "target_map": {},
  "output_schema": {},
  "model": "gemini-2.5-flash-preview-native-audio-dialog"
}
```

> If the existing `default_template.json` has additional required keys (e.g. `target_map` with content), copy that shape verbatim — empty placeholders are fine for those keys because this prompt is only ever fetched for its `body`, never run through the batch annotation flow.

- [ ] **Step 2: Commit**

```bash
git add backend/seeds/live_system_instruction_cs.json
git commit -m "feat(seeds): czech system instruction for gemini live assistant"
```

---

## Task 7: Wire the seed loader

**Files:**
- Modify: `backend/app/seed.py`
- Modify: `backend/app/main.py:lifespan`
- Test: `tests/integration/test_seed_live_prompt.py`

- [ ] **Step 1: Write failing seed test**

Create `tests/integration/test_seed_live_prompt.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.prompts import PromptsRepo
from backend.app.seed import seed_live_system_instruction

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"
SEEDS = Path(__file__).resolve().parents[2] / "backend" / "seeds"


@pytest.mark.asyncio
async def test_seed_inserts_prompt_when_missing(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await seed_live_system_instruction(
            conn,
            seed_path=SEEDS / "live_system_instruction_cs.json",
        )
        repo = PromptsRepo()
        prompt = await repo.get_by_name(conn, "live.system_instruction.cs")
        assert prompt is not None


@pytest.mark.asyncio
async def test_seed_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        seed = SEEDS / "live_system_instruction_cs.json"
        await seed_live_system_instruction(conn, seed_path=seed)
        await seed_live_system_instruction(conn, seed_path=seed)
        cur = await conn.execute(
            "SELECT COUNT(*) FROM prompts WHERE name = ?",
            ("live.system_instruction.cs",),
        )
        assert (await cur.fetchone())[0] == 1
```

> If `PromptsRepo` has no `get_by_name`, replace the assert with a direct `SELECT id FROM prompts WHERE name=?` query — match whatever the existing repo exposes.

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_seed_live_prompt.py -v
```

Expected: ImportError on `seed_live_system_instruction`.

- [ ] **Step 3: Add the seed function**

Open `backend/app/seed.py` and add (alongside `seed_default_prompt`):

```python
async def seed_live_system_instruction(
    conn: aiosqlite.Connection, *, seed_path: Path,
) -> None:
    """Insert the Czech Live system-instruction prompt + v1@production if missing."""
    data = json.loads(seed_path.read_text())  # noqa: ASYNC240
    cur = await conn.execute("SELECT 1 FROM prompts WHERE name = ?", (data["name"],))
    if await cur.fetchone():
        return
    repo = PromptsRepo()
    await repo.create_with_initial_version(
        conn,
        name=data["name"],
        description=data.get("description"),
        body=data["prompt"],
        target_map=data.get("target_map", {}),
        output_schema=data.get("output_schema", {}),
        model=data["model"],
        initial_state="production",
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/integration/test_seed_live_prompt.py -v
```

Expected: both PASS.

- [ ] **Step 5: Wire into lifespan**

Open `backend/app/main.py`. Inside `lifespan`, after the existing `seed_default_prompt` call, add:

```python
    live_seed = SEEDS / "live_system_instruction_cs.json"
    if live_seed.exists():
        await seed_live_system_instruction(ctx.db, seed_path=live_seed)
```

Also add the import near the top with the other seed imports:

```python
from backend.app.seed import seed_default_prompt, seed_live_system_instruction
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/seed.py backend/app/main.py tests/integration/test_seed_live_prompt.py
git commit -m "feat(seed): seed live.system_instruction.cs prompt at startup"
```

---

### ✅ Phase 3 review checkpoint

```bash
.venv/bin/pytest
```

The full suite should still be green; new tests added: 2 in `test_seed_live_prompt`.

---

# Phase 4 — Live-session service (token mint + summarize)

This phase glues the Czech context builder, the repo, and the Gemini Developer API HTTP calls together. All Gemini calls are mocked at the `httpx.AsyncClient` boundary.

## Task 8: `assemble_setup_payload(clip, draft, prompt_body, settings)`

**Files:**
- Create: `backend/app/services/live_sessions.py` (first slice)
- Test: `tests/unit/test_live_sessions_service.py` (first cases)

- [ ] **Step 1: Write failing tests for the assembler**

Create `tests/unit/test_live_sessions_service.py`:

```python
from backend.app.services.live_sessions import assemble_setup_payload


class _Settings:
    gemini_live_model = "gemini-2.5-flash-preview-native-audio-dialog"
    gemini_live_voice = "Aoede"


def _clip():
    return dict(
        id=42, name="P1010001", format="9,5 mm", fps=25,
        duration_secs=120.0, duration_smpte="00:02:00:00",
        notes="rodinný výlet", big_notes="",
        markers=[], fields={"pragafilm.dekáda.natočení": "20.léta"},
    )


def _draft():
    return dict(markers=[], fields={}, notes="myslím, že je to Praha")


def test_setup_payload_top_level_model_and_config():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="SYSTÉM INSTRUKCE",
        settings=_Settings(),
    )
    assert p["model"] == "models/gemini-2.5-flash-preview-native-audio-dialog"
    cfg = p["config"]
    assert cfg["responseModalities"] == ["AUDIO"]
    assert cfg["speechConfig"]["languageCode"] == "cs-CZ"
    assert cfg["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"] == "Aoede"
    assert cfg["outputAudioTranscription"] == {}
    assert cfg["inputAudioTranscription"] == {}


def test_setup_payload_has_system_instruction_text():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="MŮJ ČESKÝ SYSTÉM",
        settings=_Settings(),
    )
    parts = p["config"]["systemInstruction"]["parts"]
    assert parts == [{"text": "MŮJ ČESKÝ SYSTÉM"}]


def test_setup_payload_declares_google_search_and_end_session_tools():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="x", settings=_Settings(),
    )
    tools = p["config"]["tools"]
    assert {"googleSearch": {}} in tools
    fd = next(t for t in tools if "functionDeclarations" in t)["functionDeclarations"]
    assert any(d["name"] == "end_session" for d in fd)
    end = next(d for d in fd if d["name"] == "end_session")
    assert end["parameters"]["required"] == ["reason"]


def test_setup_payload_initial_context_turn_has_text_part():
    p = assemble_setup_payload(
        clip=_clip(), draft=_draft(),
        prompt_body="x", settings=_Settings(),
    )
    turn = p["initial_context_turn"]
    assert turn["role"] == "user"
    text_part = next(part for part in turn["parts"] if "text" in part)
    assert "Publikované anotace" in text_part["text"]
    assert "Rozpracované anotace" in text_part["text"]
    assert "P1010001" in text_part["text"]
    assert "myslím, že je to Praha" in text_part["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/unit/test_live_sessions_service.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement the assembler**

Create `backend/app/services/live_sessions.py`:

```python
"""Live-session service — payload assembly, token minting, summarization.

The browser receives the assembled setup payload (which is the literal
contents of the WSS `setup` message it will send to Gemini Live) and the
ephemeral token to authenticate the WSS connection. Audio bytes never
flow through this process — see docs/decisions.md 2026-05-23.
"""
from __future__ import annotations

from typing import Any

from backend.app.services.live_context import build_context_text


def assemble_setup_payload(
    *, clip: dict, draft: dict, prompt_body: str, settings: Any,
) -> dict:
    """Return the dict the browser sends as the WSS `setup` message + a
    pre-built initial user turn carrying the Czech context.

    `settings` is duck-typed to anything with `gemini_live_model` /
    `gemini_live_voice` attributes (the real `Settings` object, or a
    test stub).
    """
    context_text = build_context_text(clip, draft)
    return {
        "model": f"models/{settings.gemini_live_model}",
        "config": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "languageCode": "cs-CZ",
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": settings.gemini_live_voice},
                },
            },
            "outputAudioTranscription": {},
            "inputAudioTranscription": {},
            "systemInstruction": {"parts": [{"text": prompt_body}]},
            "tools": [
                {"googleSearch": {}},
                {"functionDeclarations": [
                    {
                        "name": "end_session",
                        "description": "Ukončit aktuální živou relaci na žádost uživatele.",
                        "parameters": {
                            "type": "object",
                            "properties": {"reason": {"type": "string"}},
                            "required": ["reason"],
                        },
                    },
                ]},
            ],
        },
        "initial_context_turn": {
            "role": "user",
            "parts": [{"text": context_text}],
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/test_live_sessions_service.py -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/live_sessions.py tests/unit/test_live_sessions_service.py
git commit -m "feat(services): assemble_setup_payload for gemini live wss setup"
```

---

## Task 9: `mint_ephemeral_token(setup_payload)` via `httpx`

**Files:**
- Modify: `backend/app/services/live_sessions.py`
- Modify: `tests/unit/test_live_sessions_service.py`

- [ ] **Step 1: Add failing tests for token minting**

Append to `tests/unit/test_live_sessions_service.py`:

```python
import pytest
import respx
from httpx import Response

from backend.app.services.live_sessions import mint_ephemeral_token


class _SettingsWithKey(_Settings):
    gemini_api_key = "test-key-XYZ"


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_posts_to_auth_tokens_create_endpoint():
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(
        return_value=Response(200, json={"name": "tokens/abc123"})
    )
    setup = {
        "model": "models/x",
        "config": {"responseModalities": ["AUDIO"]},
        "initial_context_turn": {"role": "user", "parts": [{"text": "hi"}]},
    }
    tok = await mint_ephemeral_token(setup=setup, settings=_SettingsWithKey())
    assert tok == "tokens/abc123"
    assert route.called
    sent = route.calls[0].request
    assert sent.url.params["key"] == "test-key-XYZ"
    import json as _j
    body = _j.loads(sent.content)
    assert body["uses"] == 1
    assert "expireTime" in body
    # bidiGenerateContentSetup must NOT include our private
    # `initial_context_turn` key — that's a server-side carry-over only.
    bidi = body["bidiGenerateContentSetup"]
    assert "initial_context_turn" not in bidi
    assert bidi["model"] == "models/x"


@pytest.mark.asyncio
@respx.mock
async def test_mint_ephemeral_token_raises_on_non_200():
    respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(403, json={"error": {"message": "Forbidden"}}))
    with pytest.raises(RuntimeError, match="auth_tokens"):
        await mint_ephemeral_token(
            setup={"model": "x", "config": {}, "initial_context_turn": {}},
            settings=_SettingsWithKey(),
        )


@pytest.mark.asyncio
async def test_mint_ephemeral_token_requires_api_key():
    s = _Settings()
    s.gemini_api_key = None  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        await mint_ephemeral_token(
            setup={"model": "x", "config": {}, "initial_context_turn": {}},
            settings=s,
        )
```

> `respx` is the standard `httpx` mock library. If not yet in `pyproject.toml`'s test deps, install it: `.venv/bin/pip install respx` and add it to the dev-deps section.

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/unit/test_live_sessions_service.py::test_mint_ephemeral_token_posts_to_auth_tokens_create_endpoint -v
```

Expected: ImportError on `mint_ephemeral_token`.

- [ ] **Step 3: Implement the mint function**

Append to `backend/app/services/live_sessions.py`:

```python
from datetime import datetime, timedelta, timezone

import httpx

AUTH_TOKENS_URL = "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
TOKEN_TTL_MINUTES = 30


async def mint_ephemeral_token(*, setup: dict, settings: Any) -> str:
    """POST to `authTokens.create`, return the `tokens/<id>` access string.

    Single-use (`uses=1`), 30-minute lifetime. The setup is forwarded verbatim
    as `bidiGenerateContentSetup`, minus our private `initial_context_turn`
    key (which the browser sends as a separate `clientContent` message after
    the setup handshake).
    """
    if not getattr(settings, "gemini_api_key", None):
        raise RuntimeError("GEMINI_API_KEY is not configured; cannot mint live token")
    bidi = {k: v for k, v in setup.items() if k != "initial_context_turn"}
    expire = (datetime.now(timezone.utc) + timedelta(minutes=TOKEN_TTL_MINUTES)).isoformat()
    body = {
        "uses": 1,
        "expireTime": expire,
        "newSessionExpireTime": expire,
        "bidiGenerateContentSetup": bidi,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(
            AUTH_TOKENS_URL,
            params={"key": settings.gemini_api_key},
            json=body,
        )
    if r.status_code != 200:
        raise RuntimeError(f"auth_tokens.create failed: {r.status_code} {r.text}")
    return r.json()["name"]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/test_live_sessions_service.py -v
```

Expected: all 7 PASS (4 from Task 8 + 3 here).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/live_sessions.py tests/unit/test_live_sessions_service.py
git commit -m "feat(services): mint_ephemeral_token via authTokens.create"
```

---

## Task 10: `summarize(session_id)` — non-Live `generateContent`

**Files:**
- Modify: `backend/app/services/live_sessions.py`
- Modify: `tests/unit/test_live_sessions_service.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_live_sessions_service.py`:

```python
from backend.app.services.live_sessions import summarize


class _SettingsForSummary(_Settings):
    gemini_api_key = "test-key"
    gemini_model = "gemini-2.5-flash-lite"


@pytest.mark.asyncio
@respx.mock
async def test_summarize_calls_generate_content_with_czech_prompt(tmp_path):
    import aiosqlite, json
    from pathlib import Path
    from backend.app.migrations_runner import apply_migrations
    from backend.app.repositories.live_sessions import LiveSessionsRepo

    MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
        await repo.mark_active(conn, "abc")
        transcript = [{"role": "user", "text": "co to je za auto?", "ts": 1},
                      {"role": "model", "text": "Vypadá to jako Škoda z 30. let.", "ts": 2}]
        await repo.mark_ended(conn, "abc", end_reason="user_stop",
                              transcript_json=json.dumps(transcript, ensure_ascii=False))

        route = respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
        ).mock(return_value=Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "Krátké české shrnutí."}]}}]
        }))

        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is True
        assert route.called
        # The prompt sent must mention Czech-summary instruction
        body = route.calls[0].request.read().decode("utf-8")
        assert "česky" in body or "Shrň" in body or "shrň" in body
        s = await repo.get(conn, "abc")
        assert s.summary_cs == "Krátké české shrnutí."


@pytest.mark.asyncio
@respx.mock
async def test_summarize_is_idempotent(tmp_path):
    import aiosqlite, json
    from pathlib import Path
    from backend.app.migrations_runner import apply_migrations
    from backend.app.repositories.live_sessions import LiveSessionsRepo

    MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
        await repo.mark_active(conn, "abc")
        await repo.mark_ended(conn, "abc", end_reason="user_stop",
                              transcript_json=json.dumps([{"role": "user", "text": "x", "ts": 1}]))
        await repo.set_summary(conn, "abc", "Již existující shrnutí.")

        respx.post(
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
        ).mock(return_value=Response(200, json={"candidates":[{"content":{"parts":[{"text":"new"}]}}]}))

        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is False  # no-op
        s = await repo.get(conn, "abc")
        assert s.summary_cs == "Již existující shrnutí."


@pytest.mark.asyncio
async def test_summarize_skips_when_transcript_empty(tmp_path):
    import aiosqlite, json
    from pathlib import Path
    from backend.app.migrations_runner import apply_migrations
    from backend.app.repositories.live_sessions import LiveSessionsRepo

    MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="abc", clip_id=1, prompt_version=None)
        await repo.mark_active(conn, "abc")
        await repo.mark_ended(conn, "abc", end_reason="error",
                              transcript_json="[]")
        ok = await summarize(conn, session_id="abc", settings=_SettingsForSummary())
        assert ok is False
        s = await repo.get(conn, "abc")
        assert s.summary_cs is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/unit/test_live_sessions_service.py::test_summarize_calls_generate_content_with_czech_prompt -v
```

Expected: ImportError on `summarize`.

- [ ] **Step 3: Implement `summarize`**

Append to `backend/app/services/live_sessions.py`:

```python
import json as _json

import aiosqlite

from backend.app.repositories.live_sessions import LiveSessionsRepo

SUMMARY_PROMPT_CS = (
    "Shrň následující konverzaci o archivním filmovém záběru "
    "ve 2–4 stručných větách česky. Zaměř se na popis scény, "
    "lokaci, dataci a zjištěné historické souvislosti. "
    "Vrať pouze samotné shrnutí, žádný úvod ani závěr.\n\n"
    "PŘEPIS:\n"
)


def _generate_content_url(model: str) -> str:
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def summarize(
    conn: aiosqlite.Connection, *, session_id: str, settings: Any,
) -> bool:
    """Generate + store the Czech summary for a finished session.

    Idempotent: returns False (no-op) if summary already set or transcript
    is empty. Returns True when a new summary was written.
    """
    repo = LiveSessionsRepo()
    session = await repo.get(conn, session_id)
    if session.summary_cs is not None:
        return False
    transcript = _json.loads(session.transcript_json or "[]")
    if not transcript:
        return False
    lines = [f"{t.get('role','?')}: {t.get('text','')}" for t in transcript if t.get("text")]
    full_prompt = SUMMARY_PROMPT_CS + "\n".join(lines)
    body = {"contents": [{"role": "user", "parts": [{"text": full_prompt}]}]}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            _generate_content_url(settings.gemini_model),
            params={"key": settings.gemini_api_key},
            json=body,
        )
    if r.status_code != 200:
        raise RuntimeError(f"generateContent failed: {r.status_code} {r.text}")
    candidates = r.json().get("candidates") or []
    text = ""
    if candidates:
        parts = candidates[0].get("content", {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        return False
    return await repo.set_summary(conn, session_id, text)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/unit/test_live_sessions_service.py -v
```

Expected: all 10 PASS (4 + 3 + 3).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/live_sessions.py tests/unit/test_live_sessions_service.py
git commit -m "feat(services): summarize() — czech post-session summary via generateContent"
```

---

### ✅ Phase 4 review checkpoint

```bash
.venv/bin/pytest
```

All green. Phase totals: 18 new tests (3 + 4 + 7 + 2 + 4 + 3 + 3) plus the migration sanity tests.

---

# Phase 5 — Routes + lifespan wiring

## Task 11: `routes/live.py` — `GET /api/live/session-config`

**Files:**
- Create: `backend/app/routes/live.py`
- Test: `tests/integration/test_routes_live.py`

- [ ] **Step 1: Open `backend/app/routes/prompts.py` and skim the `router` pattern**

Note: routes get `ctx` via `request.app.state.ctx`. Use the same pattern.

- [ ] **Step 2: Write the failing test for session-config**

Create `tests/integration/test_routes_live.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest
import respx
from httpx import AsyncClient, ASGITransport, Response

from backend.app.migrations_runner import apply_migrations
from backend.app.main import app  # the FastAPI app
from backend.app.repositories.live_sessions import LiveSessionsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.fixture
async def client_and_db(tmp_path, monkeypatch):
    """Spin up the FastAPI app with a fresh sqlite db + a stub clip lookup.

    Most route tests in this repo already have a similar fixture; reuse it
    if `tests/conftest.py` exposes one (e.g. `test_app`, `app_client`).
    Otherwise, this is the minimal version.
    """
    db_path = tmp_path / "t.db"
    conn = await aiosqlite.connect(db_path)
    await apply_migrations(conn, MIGRATIONS)

    # Build a fake AppContext that exposes what the routes need.
    class _Ctx:
        db = conn
        mode = "online"
        settings = type("S", (), {
            "gemini_api_key": "test-key",
            "gemini_live_model": "gemini-2.5-flash-preview-native-audio-dialog",
            "gemini_live_voice": "Aoede",
            "gemini_live_inactivity_s": 60,
            "gemini_model": "gemini-2.5-flash-lite",
        })()
    app.state.ctx = _Ctx()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, conn
    await conn.close()


@pytest.mark.asyncio
@respx.mock
async def test_session_config_returns_token_and_setup(client_and_db, monkeypatch):
    ac, conn = client_and_db
    # Stub the clip + draft view-models that the route would normally fetch
    # via routes/pages.py helpers. The route should accept an injected
    # builder for tests, or we monkeypatch the pages helpers.
    from backend.app import routes as routes_pkg  # noqa
    import backend.app.routes.live as live_routes

    async def fake_load_clip(ctx, clip_id):
        return dict(
            id=clip_id, name="P1010001", format="9,5 mm", fps=25,
            duration_secs=120.0, duration_smpte="00:02:00:00",
            notes="rodinný výlet", big_notes="",
            markers=[], fields={},
        )

    async def fake_load_draft(ctx, clip_id):
        return dict(markers=[], fields={}, notes="")

    monkeypatch.setattr(live_routes, "load_clip_for_live", fake_load_clip)
    monkeypatch.setattr(live_routes, "load_draft_for_live", fake_load_draft)

    respx.post(
        "https://generativelanguage.googleapis.com/v1alpha/auth_tokens"
    ).mock(return_value=Response(200, json={"name": "tokens/xyz"}))

    r = await ac.get("/api/live/session-config", params={"clip_id": 42})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["token"] == "tokens/xyz"
    assert data["session_id"]
    assert data["ws_url"].startswith("wss://generativelanguage.googleapis.com/ws/")
    assert "access_token=tokens/xyz" in data["ws_url"]
    assert data["setup_payload"]["model"].endswith("native-audio-dialog")
    assert data["setup_payload"]["initial_context_turn"]["parts"][0]["text"].startswith(
        "=== Publikované anotace"
    )
    # The pending row exists
    repo = LiveSessionsRepo()
    row = await repo.get(conn, data["session_id"])
    assert row.state == "pending"


@pytest.mark.asyncio
async def test_session_config_404_when_offline(client_and_db, monkeypatch):
    ac, _ = client_and_db
    app.state.ctx.mode = "offline"
    r = await ac.get("/api/live/session-config", params={"clip_id": 42})
    assert r.status_code == 404
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py::test_session_config_returns_token_and_setup -v
```

Expected: route doesn't exist → 404.

- [ ] **Step 4: Implement the route**

Create `backend/app/routes/live.py`:

```python
"""Live session API — session-config, transcript persistence, summarize, history."""
import json
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.services.live_sessions import (
    assemble_setup_payload,
    mint_ephemeral_token,
    summarize,
)

router = APIRouter(prefix="/api/live", tags=["live"])

WSS_URL_TEMPLATE = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContent"
    "?access_token={token}"
)


# Indirection points so tests can monkeypatch without touching pages.py internals.
async def load_clip_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages import _build_clip_view_model
    return await _build_clip_view_model(ctx, clip_id)


async def load_draft_for_live(ctx: Any, clip_id: int) -> dict:
    from backend.app.routes.pages import _build_draft_for_clip
    return await _build_draft_for_clip(ctx, clip_id)


def _require_online(ctx: Any) -> None:
    if getattr(ctx, "mode", "online") != "online":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Live is online-only")


@router.get("/session-config")
async def session_config(request: Request, clip_id: int) -> dict:
    ctx = request.app.state.ctx
    _require_online(ctx)
    settings = ctx.settings

    clip = await load_clip_for_live(ctx, clip_id)
    draft = await load_draft_for_live(ctx, clip_id)

    prompts = PromptsRepo()
    prompt = await prompts.get_by_name(ctx.db, "live.system_instruction.cs")
    if prompt is None:
        raise HTTPException(500, detail="live system instruction prompt missing")
    version = await prompts.get_production_version(ctx.db, prompt.id)
    if version is None:
        raise HTTPException(500, detail="live system instruction has no production version")

    setup_payload = assemble_setup_payload(
        clip=clip, draft=draft, prompt_body=version.body, settings=settings,
    )
    token = await mint_ephemeral_token(setup=setup_payload, settings=settings)

    session_id = uuid.uuid4().hex
    repo = LiveSessionsRepo()
    await repo.insert_pending(
        ctx.db, id=session_id, clip_id=clip_id, prompt_version=version.id,
    )

    return {
        "session_id": session_id,
        "token": token,
        "ws_url": WSS_URL_TEMPLATE.format(token=token),
        "setup_payload": setup_payload,
        "inactivity_s": settings.gemini_live_inactivity_s,
    }
```

> **Important:** `PromptsRepo.get_by_name` and `.get_production_version` may be named differently. Open `backend/app/repositories/prompts.py` and adapt the calls to the existing methods (look for the method that retrieves a prompt by `name` and the one that fetches the version row with `state='production'`).

- [ ] **Step 5: Register the router in `main.py`**

Open `backend/app/main.py`. Below the other `app.include_router(...)` calls (search for `include_router`), add:

```python
from backend.app.routes import live as live_routes  # noqa: E402
app.include_router(live_routes.router)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py -v
```

Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/live.py backend/app/main.py tests/integration/test_routes_live.py
git commit -m "feat(routes): GET /api/live/session-config — mint token + assemble setup"
```

---

## Task 12: `POST /api/live/sessions/{id}/transcript`

**Files:**
- Modify: `backend/app/routes/live.py`
- Modify: `tests/integration/test_routes_live.py`

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_routes_live.py`:

```python
@pytest.mark.asyncio
async def test_transcript_persist_happy_path(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")

    payload = {
        "end_reason": "user_stop",
        "transcript": [
            {"role": "user", "text": "ahoj", "ts": 1, "kind": "speech"},
            {"role": "model", "text": "dobrý den", "ts": 2, "kind": "speech"},
        ],
        "frame_count": 3,
        "search_calls": 1,
    }
    r = await ac.post("/api/live/sessions/abc/transcript", json=payload)
    assert r.status_code == 200, r.text
    s = await repo.get(conn, "abc")
    assert s.state == "ended"
    assert s.end_reason == "user_stop"
    assert s.frame_count == 3
    assert json.loads(s.transcript_json) == payload["transcript"]


@pytest.mark.asyncio
async def test_transcript_invalid_end_reason_rejected(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    r = await ac.post("/api/live/sessions/abc/transcript",
                      json={"end_reason": "nonsense", "transcript": []})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_transcript_unknown_session_404(client_and_db):
    ac, _ = client_and_db
    r = await ac.post("/api/live/sessions/missing/transcript",
                      json={"end_reason": "user_stop", "transcript": []})
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py::test_transcript_persist_happy_path -v
```

Expected: 404 (route not defined).

- [ ] **Step 3: Implement the route**

Append to `backend/app/routes/live.py`:

```python
from typing import Literal


class TranscriptEntry(BaseModel):
    role: str
    text: str
    ts: float | int | None = None
    kind: str | None = None


class TranscriptPayload(BaseModel):
    end_reason: Literal["user_stop", "voice_stop", "inactivity", "navigate", "error"]
    transcript: list[TranscriptEntry]
    frame_count: int = 0
    search_calls: int = 0


@router.post("/sessions/{session_id}/transcript")
async def post_transcript(request: Request, session_id: str, body: TranscriptPayload) -> dict:
    ctx = request.app.state.ctx
    _require_online(ctx)
    repo = LiveSessionsRepo()
    try:
        await repo.get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found")
    await repo.mark_ended(
        ctx.db, session_id,
        end_reason=body.end_reason,
        transcript_json=json.dumps([t.model_dump() for t in body.transcript], ensure_ascii=False),
        frame_count=body.frame_count,
        search_calls=body.search_calls,
    )
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/live.py tests/integration/test_routes_live.py
git commit -m "feat(routes): POST /api/live/sessions/{id}/transcript"
```

---

## Task 13: `POST /api/live/sessions/{id}/summarize`

**Files:**
- Modify: `backend/app/routes/live.py`
- Modify: `tests/integration/test_routes_live.py`

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_routes_live.py`:

```python
@pytest.mark.asyncio
@respx.mock
async def test_summarize_route_happy_path(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(conn, "abc", end_reason="user_stop",
                          transcript_json=json.dumps([
                              {"role":"user","text":"co je to za auto?","ts":1},
                              {"role":"model","text":"Škoda 30. léta.","ts":2},
                          ], ensure_ascii=False))
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent"
    ).mock(return_value=Response(200, json={
        "candidates":[{"content":{"parts":[{"text":"Škoda z 30. let na rodinném záběru."}]}}]
    }))
    r = await ac.post("/api/live/sessions/abc/summarize")
    assert r.status_code == 200, r.text
    assert r.json()["summary_cs"] == "Škoda z 30. let na rodinném záběru."
    assert (await repo.get(conn, "abc")).summary_cs == "Škoda z 30. let na rodinném záběru."


@pytest.mark.asyncio
@respx.mock
async def test_summarize_route_idempotent(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(conn, "abc", end_reason="user_stop",
                          transcript_json=json.dumps([{"role":"user","text":"x","ts":1}]))
    await repo.set_summary(conn, "abc", "Existující.")
    r = await ac.post("/api/live/sessions/abc/summarize")
    assert r.status_code == 200
    assert r.json()["summary_cs"] == "Existující."
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py::test_summarize_route_happy_path -v
```

Expected: 404 (route not defined).

- [ ] **Step 3: Implement the route**

Append to `backend/app/routes/live.py`:

```python
@router.post("/sessions/{session_id}/summarize")
async def post_summarize(request: Request, session_id: str) -> dict:
    ctx = request.app.state.ctx
    _require_online(ctx)
    repo = LiveSessionsRepo()
    try:
        session = await repo.get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found")
    await summarize(ctx.db, session_id=session_id, settings=ctx.settings)
    session = await repo.get(ctx.db, session_id)
    return {"summary_cs": session.summary_cs}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/live.py tests/integration/test_routes_live.py
git commit -m "feat(routes): POST /api/live/sessions/{id}/summarize"
```

---

## Task 14: `GET /api/live/sessions` (list) + `GET /api/live/sessions/{id}` (detail)

**Files:**
- Modify: `backend/app/routes/live.py`
- Modify: `tests/integration/test_routes_live.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/integration/test_routes_live.py`:

```python
@pytest.mark.asyncio
async def test_list_by_clip(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="a", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "a")
    await repo.mark_ended(conn, "a", end_reason="user_stop",
                          transcript_json=json.dumps([{"role":"u","text":"x","ts":1}]))
    await repo.insert_pending(conn, id="b", clip_id=99, prompt_version=None)
    r = await ac.get("/api/live/sessions", params={"clip_id": 42})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["id"] == "a"
    assert data[0]["end_reason"] == "user_stop"
    assert "has_summary" in data[0]
    assert data[0]["has_summary"] is False


@pytest.mark.asyncio
async def test_get_detail(client_and_db):
    ac, conn = client_and_db
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(conn, "abc", end_reason="user_stop",
                          transcript_json=json.dumps([{"role":"u","text":"hi","ts":1}]))
    await repo.set_summary(conn, "abc", "Shrnutí.")
    r = await ac.get("/api/live/sessions/abc")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == "abc"
    assert data["summary_cs"] == "Shrnutí."
    assert data["transcript"] == [{"role":"u","text":"hi","ts":1}]


@pytest.mark.asyncio
async def test_get_detail_404(client_and_db):
    ac, _ = client_and_db
    r = await ac.get("/api/live/sessions/no-such")
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py::test_list_by_clip -v
```

Expected: 404.

- [ ] **Step 3: Implement the routes**

Append to `backend/app/routes/live.py`:

```python
@router.get("/sessions")
async def list_sessions(request: Request, clip_id: int) -> list[dict]:
    ctx = request.app.state.ctx
    _require_online(ctx)
    repo = LiveSessionsRepo()
    rows = await repo.list_by_clip(ctx.db, clip_id)
    out = []
    for s in rows:
        duration_s = None
        if s.started_at and s.ended_at:
            from datetime import datetime
            try:
                duration_s = (datetime.fromisoformat(s.ended_at)
                              - datetime.fromisoformat(s.started_at)).total_seconds()
            except ValueError:
                duration_s = None
        out.append({
            "id": s.id,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "duration_s": duration_s,
            "end_reason": s.end_reason,
            "state": s.state,
            "has_summary": s.summary_cs is not None,
            "frame_count": s.frame_count,
        })
    return out


@router.get("/sessions/{session_id}")
async def get_session(request: Request, session_id: str) -> dict:
    ctx = request.app.state.ctx
    _require_online(ctx)
    repo = LiveSessionsRepo()
    try:
        s = await repo.get(ctx.db, session_id)
    except LookupError:
        raise HTTPException(404, detail="session not found")
    return {
        "id": s.id,
        "clip_id": s.clip_id,
        "state": s.state,
        "started_at": s.started_at,
        "ended_at": s.ended_at,
        "end_reason": s.end_reason,
        "transcript": json.loads(s.transcript_json or "[]"),
        "summary_cs": s.summary_cs,
        "frame_count": s.frame_count,
        "search_calls": s.search_calls,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/integration/test_routes_live.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/live.py tests/integration/test_routes_live.py
git commit -m "feat(routes): GET /api/live/sessions list + detail"
```

---

## Task 15: Stale-pending cleanup at lifespan startup

**Files:**
- Modify: `backend/app/main.py`
- Test: `tests/integration/test_live_pending_cleanup_startup.py`

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_live_pending_cleanup_startup.py`:

```python
import aiosqlite
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.live_sessions import LiveSessionsRepo
from backend.app.startup import run_startup_cleanup  # new helper

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_startup_cleanup_drops_stale_pending(tmp_path):
    db = tmp_path / "t.db"
    async with aiosqlite.connect(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = LiveSessionsRepo()
        await repo.insert_pending(conn, id="old", clip_id=1, prompt_version=None)
        two_h_ago = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        await conn.execute("UPDATE live_sessions SET created_at=? WHERE id='old'", (two_h_ago,))
        await conn.commit()

        n = await run_startup_cleanup(conn)
        assert n >= 1
        with pytest.raises(LookupError):
            await repo.get(conn, "old")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_live_pending_cleanup_startup.py -v
```

Expected: ImportError on `run_startup_cleanup`.

- [ ] **Step 3: Add the helper**

Open `backend/app/startup.py` and add:

```python
import aiosqlite

from backend.app.repositories.live_sessions import LiveSessionsRepo


async def run_startup_cleanup(conn: aiosqlite.Connection) -> int:
    """Drop stale-pending live_sessions older than 1h. Returns rows deleted."""
    repo = LiveSessionsRepo()
    return await repo.cleanup_stale_pending(conn, older_than_hours=1)
```

- [ ] **Step 4: Wire into `main.py:lifespan`**

In `backend/app/main.py` after the seed calls, before `if init_external:`, add:

```python
    from backend.app.startup import run_startup_cleanup
    await run_startup_cleanup(ctx.db)
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/integration/test_live_pending_cleanup_startup.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/startup.py backend/app/main.py tests/integration/test_live_pending_cleanup_startup.py
git commit -m "feat(startup): reap stale-pending live_sessions on lifespan start"
```

---

### ✅ Phase 5 review checkpoint

```bash
.venv/bin/pytest
```

All green. Backend is now feature-complete behind a real `GEMINI_API_KEY`. Browser work begins.

---

# Phase 6 — Browser audio + WSS pipeline

> **TDD note for this phase:** Browser audio plumbing is verified manually in a real browser tab against a real Gemini key. There is no JS unit-test framework wired into this repo (confirmed by `find tests -name '*.js'` → empty). The discipline here is **single-purpose commits per Task** so it stays reviewable; verification is by the manual checklist at the end of each task.
>
> Before starting Phase 6, make sure a valid `GEMINI_API_KEY=...` is in `.env` and run `.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8765` per the project's `run.sh` to have a hot-reload server ready.

## Task 16: Audio worklet for PCM capture

**Files:**
- Create: `backend/app/static/audio-worklet-recorder.js`

- [ ] **Step 1: Write the worklet processor**

Create `backend/app/static/audio-worklet-recorder.js`:

```javascript
// AudioWorkletProcessor: capture mono Float32 frames from the AudioContext,
// downsample to 16 kHz, convert to Int16 PCM, post 100 ms chunks back to
// the main thread.
//
// The main thread sends `{type:"start"}` and `{type:"stop"}` messages.

const TARGET_SR = 16000;
const CHUNK_MS = 100;

class RecorderProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.running = false;
    this.acc = [];               // Float32 samples accumulated at native rate
    this.nativeSR = sampleRate;  // global in AudioWorkletGlobalScope
    this.samplesPerChunk = Math.round(this.nativeSR * (CHUNK_MS / 1000));
    this.port.onmessage = (e) => {
      const t = e.data?.type;
      if (t === "start") { this.running = true; }
      else if (t === "stop") { this.running = false; this.acc = []; }
    };
  }

  // Linear downsample to 16 kHz.
  _downsample(input) {
    if (this.nativeSR === TARGET_SR) return input;
    const ratio = this.nativeSR / TARGET_SR;
    const outLen = Math.floor(input.length / ratio);
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      out[i] = input[Math.floor(i * ratio)];
    }
    return out;
  }

  _floatToPCM16(float32) {
    const out = new Int16Array(float32.length);
    for (let i = 0; i < float32.length; i++) {
      const s = Math.max(-1, Math.min(1, float32[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    return out;
  }

  process(inputs) {
    if (!this.running) return true;
    const input = inputs[0];
    if (!input || !input[0]) return true;
    const mono = input[0];
    for (let i = 0; i < mono.length; i++) this.acc.push(mono[i]);
    while (this.acc.length >= this.samplesPerChunk) {
      const slice = this.acc.splice(0, this.samplesPerChunk);
      const down = this._downsample(Float32Array.from(slice));
      const pcm = this._floatToPCM16(down);
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/static/audio-worklet-recorder.js
git commit -m "feat(static): audio worklet — 16kHz int16 pcm capture"
```

---

## Task 17: `liveSession.js` skeleton (state machine, no audio yet)

**Files:**
- Create: `backend/app/static/liveSession.js`

- [ ] **Step 1: Write the Alpine component skeleton**

Create `backend/app/static/liveSession.js`:

```javascript
// Alpine component for the Gemini Live clip assistant.
// Composes into clip_detail.html's existing x-data alongside player() and clipAnnotate().
// Audio bytes flow browser ↔ Google directly via WSS; this component only
// talks to our backend for token minting and post-session persistence.

function liveSession(clipId, config) {
  return {
    // ── state ────────────────────────────────────────────────────────────
    state: "idle",                  // idle | connecting | active | closing
    transcript: [],                 // [{role, text, ts, kind}]
    elapsedFmt: "0:00",
    expanded: false,
    error: null,
    sessionId: null,
    inactivityS: (config && config.inactivityS) || 60,

    // ── internals ────────────────────────────────────────────────────────
    _ws: null,
    _audioCtxIn: null,
    _audioCtxOut: null,
    _workletNode: null,
    _mediaStream: null,
    _frameCount: 0,
    _searchCalls: 0,
    _startedAt: 0,
    _elapsedTimer: null,
    _inactivityTimer: null,
    _endReason: null,
    _setupPayload: null,

    // ── public API ───────────────────────────────────────────────────────
    async start() {
      if (this.state !== "idle") return;
      this.error = null;
      this.transcript = [];
      this._frameCount = 0;
      this._searchCalls = 0;
      this._endReason = null;
      this.state = "connecting";
      try {
        const config = await this._fetchConfig();
        this.sessionId = config.session_id;
        this._setupPayload = config.setup_payload;
        await this._openMic();
        this._openWs(config.ws_url);
      } catch (e) {
        this.error = String(e);
        this.state = "idle";
        await this._teardown();
      }
    },

    async close(reason) {
      if (this.state === "idle" || this.state === "closing") return;
      this.state = "closing";
      this._endReason = reason || this._endReason || "user_stop";
      try { if (this._ws && this._ws.readyState === 1) this._ws.close(); } catch {}
      await this._persistAndSummarize();
      await this._teardown();
      this.state = "idle";
    },

    sendFrame() { /* implemented in Task 20 */ },

    // ── helpers (stubs filled in later tasks) ────────────────────────────
    async _fetchConfig() {
      const r = await fetch(`/api/live/session-config?clip_id=${clipId}`);
      if (!r.ok) throw new Error(`session-config HTTP ${r.status}`);
      return r.json();
    },

    async _openMic() { /* Task 18 */ },
    _openWs(url) { /* Task 19 */ },
    _onWsMessage(evt) { /* Task 19 + 21 */ },
    _resetInactivity() { /* Task 22 */ },

    async _persistAndSummarize() {
      if (!this.sessionId) return;
      const body = {
        end_reason: this._endReason || "user_stop",
        transcript: this.transcript,
        frame_count: this._frameCount,
        search_calls: this._searchCalls,
      };
      try {
        const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
        if (navigator.sendBeacon) {
          navigator.sendBeacon(`/api/live/sessions/${this.sessionId}/transcript`, blob);
        } else {
          await fetch(`/api/live/sessions/${this.sessionId}/transcript`,
                      { method: "POST", body: blob });
        }
        // Summarize is fire-and-forget; History panel can retry later.
        fetch(`/api/live/sessions/${this.sessionId}/summarize`,
              { method: "POST" }).catch(() => {});
      } catch {}
    },

    async _teardown() {
      try { this._workletNode?.disconnect(); } catch {}
      try { if (this._mediaStream) this._mediaStream.getTracks().forEach(t => t.stop()); } catch {}
      try { await this._audioCtxIn?.close(); } catch {}
      try { await this._audioCtxOut?.close(); } catch {}
      this._workletNode = null;
      this._mediaStream = null;
      this._audioCtxIn = null;
      this._audioCtxOut = null;
      this._ws = null;
      clearInterval(this._elapsedTimer);
      clearTimeout(this._inactivityTimer);
    },
  };
}

window.liveSession = liveSession;
```

- [ ] **Step 2: Manual verification**

In a browser console (with the dev server running and clip_detail.html briefly modified to include `<script src="/static/liveSession.js"></script>`):

```js
const s = liveSession(123, {inactivityS:60});
console.log(s.state);       // "idle"
console.log(typeof s.start) // "function"
```

(Don't call `start()` yet — fetches are not wired into the template yet.)

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/liveSession.js
git commit -m "feat(static): liveSession alpine component skeleton"
```

---

## Task 18: `_openMic` — request permission + start the worklet

**Files:**
- Modify: `backend/app/static/liveSession.js`

- [ ] **Step 1: Replace the `_openMic` stub**

In `backend/app/static/liveSession.js`, replace the `_openMic` stub with:

```javascript
    async _openMic() {
      this._mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      this._audioCtxIn = new AudioContext();
      await this._audioCtxIn.audioWorklet.addModule("/static/audio-worklet-recorder.js");
      const src = this._audioCtxIn.createMediaStreamSource(this._mediaStream);
      this._workletNode = new AudioWorkletNode(this._audioCtxIn, "recorder-processor");
      src.connect(this._workletNode);
      // Do NOT connect to destination — we don't want to hear ourselves.
      this._workletNode.port.onmessage = (e) => this._onCaptureChunk(e.data);
      this._workletNode.port.postMessage({ type: "start" });
    },

    _onCaptureChunk(arrayBuffer) {
      if (!this._ws || this._ws.readyState !== 1) return;
      // Gemini Live expects { realtimeInput: { mediaChunks: [{ mimeType, data }] } }
      // mediaChunks is base64 PCM at the rate declared in setup (16000).
      const b64 = this._b64FromBuffer(arrayBuffer);
      this._ws.send(JSON.stringify({
        realtimeInput: {
          mediaChunks: [{ mimeType: "audio/pcm;rate=16000", data: b64 }],
        },
      }));
      this._resetInactivity();
    },

    _b64FromBuffer(buf) {
      const bytes = new Uint8Array(buf);
      let bin = "";
      for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      return btoa(bin);
    },
```

- [ ] **Step 2: Manual verification (browser console)**

Hard to verify in isolation; deferred to integrated test at end of Phase 6.

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/liveSession.js
git commit -m "feat(static): mic capture wired through audio worklet to wss"
```

---

## Task 19: `_openWs` — open WebSocket, send setup, route messages

**Files:**
- Modify: `backend/app/static/liveSession.js`

- [ ] **Step 1: Replace the `_openWs` and `_onWsMessage` stubs**

In `backend/app/static/liveSession.js`:

```javascript
    _openWs(url) {
      const ws = new WebSocket(url);
      this._ws = ws;
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        // Send the setup message — same shape the backend assembled, minus our private key.
        const setup = { ...this._setupPayload };
        const initial = setup.initial_context_turn;
        delete setup.initial_context_turn;
        ws.send(JSON.stringify({ setup }));
        // Send the initial context turn + initial frame as the first client content.
        this._sendInitialClientContent(initial);
        this.state = "active";
        this._startedAt = Date.now();
        this._elapsedTimer = setInterval(() => this._tickElapsed(), 1000);
        this._resetInactivity();
      };
      ws.onmessage = (evt) => this._onWsMessage(evt);
      ws.onerror = () => {
        this._endReason = "error";
      };
      ws.onclose = () => {
        if (this.state === "active") {
          this.close(this._endReason || "error");
        }
      };
    },

    _sendInitialClientContent(initialTurn) {
      // Combine the context text part with an initial JPEG frame so the model
      // has both from the start.
      const parts = [...(initialTurn?.parts || [])];
      const frame = this._captureFrameJpegB64();
      if (frame) parts.push({ inlineData: { mimeType: "image/jpeg", data: frame } });
      const msg = { clientContent: { turns: [{ role: "user", parts }], turnComplete: false } };
      try { this._ws.send(JSON.stringify(msg)); } catch {}
      this._frameCount += frame ? 1 : 0;
    },

    _tickElapsed() {
      const s = Math.floor((Date.now() - this._startedAt) / 1000);
      const mm = Math.floor(s / 60);
      const ss = s % 60;
      this.elapsedFmt = `${mm}:${ss.toString().padStart(2, "0")}`;
    },

    _onWsMessage(evt) {
      let msg;
      try { msg = JSON.parse(evt.data); } catch { return; }
      // server-side audio (PCM 24kHz base64) — handled in Task 21
      if (msg.serverContent) this._handleServerContent(msg.serverContent);
      if (msg.toolCall) this._handleToolCall(msg.toolCall);
      this._resetInactivity();
    },

    _handleServerContent(sc) {
      // Audio playback — Task 21.
      const transcripts = sc.outputTranscription;
      if (transcripts?.text) {
        this.transcript.push({ role: "model", text: transcripts.text, ts: Date.now(), kind: "speech" });
      }
      const input = sc.inputTranscription;
      if (input?.text) {
        this.transcript.push({ role: "user", text: input.text, ts: Date.now(), kind: "speech" });
      }
    },

    _handleToolCall(tc) {
      const calls = tc.functionCalls || [];
      for (const c of calls) {
        if (c.name === "end_session") {
          this.transcript.push({ role: "system", text: `Konec: ${c.args?.reason || ""}`, ts: Date.now(), kind: "function_call" });
          this.close("voice_stop");
        }
      }
    },
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/static/liveSession.js
git commit -m "feat(static): wss open + setup + transcript wiring + end_session tool"
```

---

## Task 20: Frame capture + `sendFrame()`

**Files:**
- Modify: `backend/app/static/liveSession.js`

- [ ] **Step 1: Implement frame capture**

In `backend/app/static/liveSession.js`, replace the `sendFrame` stub and add helpers:

```javascript
    sendFrame() {
      if (this.state !== "active" || !this._ws) return;
      const b64 = this._captureFrameJpegB64();
      if (!b64) return;
      this._ws.send(JSON.stringify({
        realtimeInput: {
          mediaChunks: [{ mimeType: "image/jpeg", data: b64 }],
        },
      }));
      this._frameCount += 1;
      this._resetInactivity();
    },

    _captureFrameJpegB64() {
      const v = document.querySelector("video.video");
      if (!v || !v.videoWidth) return null;
      const maxW = 1280, maxH = 720;
      const scale = Math.min(1, maxW / v.videoWidth, maxH / v.videoHeight);
      const w = Math.round(v.videoWidth * scale);
      const h = Math.round(v.videoHeight * scale);
      let canvas = this._frameCanvas;
      if (!canvas) {
        canvas = this._frameCanvas = document.createElement("canvas");
      }
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w; canvas.height = h;
      }
      canvas.getContext("2d").drawImage(v, 0, 0, w, h);
      const dataUrl = canvas.toDataURL("image/jpeg", 0.85);
      // strip "data:image/jpeg;base64," prefix
      return dataUrl.substring(dataUrl.indexOf(",") + 1);
    },
```

- [ ] **Step 2: Wire `<video>` pause event to auto-send frames**

Still in `liveSession.js`, add an `init()` method (Alpine lifecycle hook) above `start()`:

```javascript
    init() {
      // Auto-send frame on player pause while session is active.
      const v = document.querySelector("video.video");
      if (v) {
        v.addEventListener("pause", () => {
          if (this.state === "active") this.sendFrame();
        });
      }
      // Persist transcript on navigation away mid-session.
      window.addEventListener("beforeunload", () => {
        if (this.state === "active") {
          this._endReason = "navigate";
          this._persistAndSummarize();
        }
      });
    },
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/liveSession.js
git commit -m "feat(static): frame capture + auto-send on pause + beforeunload persist"
```

---

## Task 21: Audio playback (24 kHz PCM queue)

**Files:**
- Modify: `backend/app/static/liveSession.js`

- [ ] **Step 1: Replace `_handleServerContent` to also play audio**

In `backend/app/static/liveSession.js`:

```javascript
    _handleServerContent(sc) {
      if (sc.outputTranscription?.text) {
        this.transcript.push({ role: "model", text: sc.outputTranscription.text, ts: Date.now(), kind: "speech" });
      }
      if (sc.inputTranscription?.text) {
        this.transcript.push({ role: "user", text: sc.inputTranscription.text, ts: Date.now(), kind: "speech" });
      }
      const turns = sc.modelTurn?.parts || [];
      for (const part of turns) {
        if (part.inlineData && part.inlineData.mimeType?.startsWith("audio/pcm")) {
          this._enqueueAudio(part.inlineData.data);
        }
      }
    },

    _enqueueAudio(b64) {
      if (!this._audioCtxOut) {
        this._audioCtxOut = new AudioContext({ sampleRate: 24000 });
      }
      const bin = atob(b64);
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      // Interpret as Int16 PCM @24kHz mono
      const view = new DataView(bytes.buffer);
      const sampleCount = bytes.length / 2;
      const buf = this._audioCtxOut.createBuffer(1, sampleCount, 24000);
      const channel = buf.getChannelData(0);
      for (let i = 0; i < sampleCount; i++) {
        channel[i] = view.getInt16(i * 2, true) / 0x8000;
      }
      const node = this._audioCtxOut.createBufferSource();
      node.buffer = buf;
      node.connect(this._audioCtxOut.destination);
      const startAt = Math.max(this._audioCtxOut.currentTime, (this._nextPlayAt || 0));
      node.start(startAt);
      this._nextPlayAt = startAt + buf.duration;
    },
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/static/liveSession.js
git commit -m "feat(static): pcm 24khz playback queue for gemini live audio"
```

---

## Task 22: Inactivity timer

**Files:**
- Modify: `backend/app/static/liveSession.js`

- [ ] **Step 1: Implement `_resetInactivity`**

Replace the stub:

```javascript
    _resetInactivity() {
      clearTimeout(this._inactivityTimer);
      const ms = (this.inactivityS || 60) * 1000;
      this._inactivityTimer = setTimeout(() => {
        if (this.state === "active") this.close("inactivity");
      }, ms);
    },
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/static/liveSession.js
git commit -m "feat(static): rolling inactivity timer (default 60s)"
```

---

### ✅ Phase 6 review checkpoint

`liveSession.js` is feature-complete. The next phase wires it into the template and exercises it end-to-end in a browser.

---

# Phase 7 — Template integration + History tab

## Task 23: Add the `🎤 Live` button + header overlay + transcript strip

**Files:**
- Modify: `backend/app/templates/pages/clip_detail.html`
- Modify: `backend/app/templates/pages/layout.html` (if needed to load JS globally) **OR** load via per-page block. Read the existing layout to choose.

- [ ] **Step 1: Inspect the layout for how other JS is loaded**

```bash
grep -n "static/" backend/app/templates/pages/layout.html backend/app/templates/pages/clip_detail.html
```

Note the pattern (e.g. `<script src="/static/player.js"></script>` in layout, or block-level). Match it.

- [ ] **Step 2: Update `clip_detail.html`**

In `backend/app/templates/pages/clip_detail.html`, change the root `<div class="detail" x-data='...'>` to include `liveSession`:

```html
<div class="detail"
     :class="{ 'is-draft': scope === 'draft', 'live-active': liveSession.state !== 'idle' }"
     x-data='Object.assign(
       player({{ clip.fps }}, {{ clip.duration_secs }}, {{ clip.markers|tojson }}),
       clipAnnotate({{ clip.id }}),
       { scope: "published", tab: "markers",
         liveSession: liveSession({{ clip.id }}, { inactivityS: {{ gemini_live_inactivity_s|default(60) }} })
       }
     )'
     @keydown.window="handleKey($event)">
```

Replace the existing `<header class="detail-hdr">…</header>` block with a conditional shape: when idle, show the existing cache-actions; when live is active, swap to the control bar.

```html
  <header class="detail-hdr">
    <template x-if="liveSession.state === 'idle'">
      <div class="detail-hdr-row">
        <span class="clip-title">{{ clip.name }}</span>
        <span class="meta mono">{{ clip.format or "" }}</span>
        <span class="cache-actions">
          {% with cache = clip.cache %}
            {% include "pages/_cache_badge.html" %}
          {% endwith %}
          {% if not host_local_proxies %}
            {% if clip.cache and clip.cache.media_local.present %}
              <button type="button"
                      class="ca-btn ca-btn-danger"
                      onclick="evictLocal({{ clip.id }})">Evict local</button>
            {% elif mode == "online" %}
              <button type="button"
                      class="ca-btn"
                      onclick="cacheClip({{ clip.id }})">⬇ Cache video</button>
            {% endif %}
          {% endif %}
          {% if mode == "online" %}
            {% include "pages/_annotate_dropdown.html" %}
            <button type="button" class="ca-btn ca-btn-live"
                    @click="liveSession.start()"
                    :disabled="!{{ clip.duration_secs|tojson }}">🎤 Live</button>
          {% endif %}
        </span>
        <span class="grow"></span>
        <span class="tc-readout mono">
          <span class="cur" x-text="tc(current)">00:00:00:00</span>
          <span class="slash">/</span>
          <span class="end" x-text="tc(duration)">{{ duration_smpte }}</span>
        </span>
      </div>
    </template>

    <template x-if="liveSession.state !== 'idle'">
      <div class="live-bar">
        <span class="rec-pill" :class="{ pulsing: liveSession.state === 'active' }">
          ● REC <span x-text="liveSession.elapsedFmt">0:00</span>
        </span>
        <button type="button" class="ca-btn"
                :disabled="liveSession.state !== 'active'"
                @click="liveSession.sendFrame()">📸 Send frame</button>
        <button type="button" class="ca-btn ca-btn-danger"
                @click="liveSession.close('user_stop')">■ Stop</button>
        <span class="grow"></span>
        <span class="live-error" x-show="liveSession.error" x-text="liveSession.error"></span>
      </div>
    </template>
  </header>

  <div class="live-strip" x-show="liveSession.state !== 'idle'" x-cloak
       :class="{ expanded: liveSession.expanded }">
    <ul class="live-strip-lines">
      <template x-for="(line, i) in liveSession.transcript.slice(-3)" :key="i">
        <li><b x-text="line.role + ':'"></b> <span x-text="line.text"></span></li>
      </template>
    </ul>
    <button type="button" class="live-expand"
            @click="liveSession.expanded = !liveSession.expanded">
      <span x-show="!liveSession.expanded">▾ expand</span>
      <span x-show="liveSession.expanded">▴ collapse</span>
    </button>
    <div class="live-strip-full" x-show="liveSession.expanded" x-cloak>
      <template x-for="(line, i) in liveSession.transcript" :key="i">
        <div><b x-text="line.role + ':'"></b> <span x-text="line.text"></span></div>
      </template>
    </div>
  </div>
```

Also include the JS module at the bottom of the file (after the existing `<script>` block):

```html
<script src="/static/liveSession.js"></script>
```

- [ ] **Step 3: Add minimal CSS so the live bar looks right**

Open the project's CSS file (find it — `grep -rn "detail-hdr" backend/app/static/` or similar). Add:

```css
.detail-hdr .live-bar { display:flex; align-items:center; gap:.6rem; }
.live-bar .rec-pill { background:#400; color:#f88; padding:.15rem .5rem; border-radius:999px; font-family:monospace; }
.live-bar .rec-pill.pulsing { animation: live-pulse 1.2s infinite; }
@keyframes live-pulse { 0%,100% { opacity:1 } 50% { opacity:.55 } }
.live-strip { background:rgba(255,255,180,0.06); padding:.4rem .6rem; border-bottom:1px solid #333; font-size:.9rem; }
.live-strip-lines li { list-style:none; padding:.05rem 0; }
.live-strip.expanded .live-strip-lines { display:none; }
.live-strip-full { max-height:40vh; overflow-y:auto; }
.ca-btn-live { background:#262; color:#bfb; }
```

- [ ] **Step 4: Pass `gemini_live_inactivity_s` to the template context**

Find the route that renders `clip_detail.html` in `backend/app/routes/pages.py` (search for `clip_detail.html`). Add `"gemini_live_inactivity_s": ctx.settings.gemini_live_inactivity_s` to the context dict.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/clip_detail.html backend/app/routes/pages.py
# also stage whichever CSS file you edited
git commit -m "feat(ui): live button + header overlay + transcript strip on clip detail"
```

---

## Task 24: History tab + partial

**Files:**
- Modify: `backend/app/templates/pages/_anno_panels.html`
- Create: `backend/app/templates/pages/_anno_live_history.html`
- Modify: `backend/app/routes/pages.py` (add `/clips/{id}/live-history`)
- Test: `tests/integration/test_routes_live_history_partial.py`

- [ ] **Step 1: Write the failing route test**

Create `tests/integration/test_routes_live_history_partial.py`:

```python
import json
from pathlib import Path

import aiosqlite
import pytest
from httpx import AsyncClient, ASGITransport

from backend.app.migrations_runner import apply_migrations
from backend.app.main import app
from backend.app.repositories.live_sessions import LiveSessionsRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest.mark.asyncio
async def test_clip_live_history_partial_renders(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    conn = await aiosqlite.connect(db_path)
    await apply_migrations(conn, MIGRATIONS)
    repo = LiveSessionsRepo()
    await repo.insert_pending(conn, id="abc", clip_id=42, prompt_version=None)
    await repo.mark_active(conn, "abc")
    await repo.mark_ended(conn, "abc", end_reason="user_stop",
                          transcript_json=json.dumps([{"role":"user","text":"ahoj","ts":1}]))
    await repo.set_summary(conn, "abc", "Krátké shrnutí.")

    class _Ctx:
        db = conn; mode = "online"; settings = type("S", (), {})()
    app.state.ctx = _Ctx()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/clips/42/live-history")
        assert r.status_code == 200
        html = r.text
        assert "abc" in html or "Krátké shrnutí." in html
        assert "user_stop" in html
    await conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/integration/test_routes_live_history_partial.py -v
```

Expected: 404.

- [ ] **Step 3: Add the partial template**

Create `backend/app/templates/pages/_anno_live_history.html`:

```html
{# Live session history for a clip — read-only. #}
<div class="live-history">
  {% if sessions %}
    <ul class="live-history-list">
      {% for s in sessions %}
        <li class="live-history-item"
            x-data="{ open: false, detail: null, loading: false }">
          <button type="button" class="live-history-row"
                  @click="open = !open;
                          if (open && !detail) {
                            loading = true;
                            const r = await fetch('/api/live/sessions/{{ s.id }}');
                            if (r.ok) detail = await r.json();
                            loading = false;
                          }">
            <span class="ts mono">{{ s.started_at or s.created_at }}</span>
            <span class="dur mono">{% if s.duration_s %}{{ '%d'|format(s.duration_s) }} s{% else %}—{% endif %}</span>
            <span class="reason">{{ s.end_reason or s.state }}</span>
            <span class="has-summary">{% if s.has_summary %}✓{% else %}—{% endif %}</span>
            <span class="frames">{{ s.frame_count }} fr.</span>
          </button>
          <div class="live-history-detail" x-show="open" x-cloak>
            <template x-if="loading"><div>Načítám…</div></template>
            <template x-if="detail">
              <div>
                <div class="live-summary" x-show="detail.summary_cs">
                  <b>Shrnutí:</b> <span x-text="detail.summary_cs"></span>
                </div>
                <div class="live-summary-missing" x-show="!detail.summary_cs">
                  <button type="button"
                          @click="
                            const r = await fetch('/api/live/sessions/{{ s.id }}/summarize', {method:'POST'});
                            if (r.ok) { const d = await r.json(); detail.summary_cs = d.summary_cs; }">
                    Generovat shrnutí
                  </button>
                </div>
                <div class="live-transcript">
                  <template x-for="(line, i) in detail.transcript" :key="i">
                    <div><b x-text="line.role + ':'"></b> <span x-text="line.text"></span></div>
                  </template>
                </div>
              </div>
            </template>
          </div>
        </li>
      {% endfor %}
    </ul>
  {% else %}
    <p class="empty">Zatím žádné živé relace pro tento klip.</p>
  {% endif %}
</div>
```

- [ ] **Step 4: Add the History tab to `_anno_panels.html`**

Open `backend/app/templates/pages/_anno_panels.html`. Find the existing tab strip (Markers / Fields / Notes etc.) and add a new tab button + content slot. Reuse whatever pattern the file already uses; here is the conceptual addition:

```html
<button type="button" :class="{ active: tab === 'history' }"
        @click="tab = 'history'; if (!historyLoaded) loadHistory()">History</button>
```

Add a template branch where the panel contents are rendered:

```html
<div x-show="tab === 'history'" x-cloak>
  <div x-html="historyHtml || '<p class=\"empty\">Načítám…</p>'"></div>
</div>
```

And the loader (in the `clipAnnotate` Alpine state or inline `x-data` extension on the wrapper — match the existing pattern):

```javascript
// inside the x-data Object.assign on the .detail wrapper:
historyLoaded: false,
historyHtml: "",
async loadHistory() {
  this.historyLoaded = true;
  const r = await fetch(`/clips/{{ clip.id }}/live-history`);
  this.historyHtml = r.ok ? await r.text() : "<p class='error'>Selhalo načtení.</p>";
},
```

- [ ] **Step 5: Add the partial route**

Open `backend/app/routes/pages.py` and add:

```python
@router.get("/clips/{clip_id}/live-history", response_class=HTMLResponse)
async def clip_live_history(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    repo = LiveSessionsRepo()
    rows = await repo.list_by_clip(ctx.db, clip_id)
    sessions = []
    from datetime import datetime as _dt
    for s in rows:
        duration_s = None
        if s.started_at and s.ended_at:
            try:
                duration_s = (_dt.fromisoformat(s.ended_at)
                              - _dt.fromisoformat(s.started_at)).total_seconds()
            except ValueError:
                pass
        sessions.append({
            "id": s.id, "started_at": s.started_at, "created_at": s.created_at,
            "duration_s": duration_s, "end_reason": s.end_reason,
            "state": s.state, "has_summary": s.summary_cs is not None,
            "frame_count": s.frame_count,
        })
    return templates.TemplateResponse(
        request, "pages/_anno_live_history.html", {"sessions": sessions},
    )
```

Add the import at the top: `from backend.app.repositories.live_sessions import LiveSessionsRepo`.

- [ ] **Step 6: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/integration/test_routes_live_history_partial.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/templates/pages/_anno_live_history.html \
        backend/app/templates/pages/_anno_panels.html \
        backend/app/routes/pages.py \
        tests/integration/test_routes_live_history_partial.py
git commit -m "feat(ui): live-session history tab + per-session detail expansion"
```

---

### ✅ Phase 7 review checkpoint

```bash
.venv/bin/pytest
```

All green. Now run the dev server, open a clip, and walk the manual checklist from §8.2 of the spec.

---

# Phase 8 — Infrastructure, docs, manual verification

## Task 25: gcloud enablement script

**Files:**
- Create: `deploy/enable-gemini-live.sh`

- [ ] **Step 1: Confirm `deploy/` exists**

```bash
ls deploy/ 2>/dev/null || mkdir -p deploy
```

- [ ] **Step 2: Write the script**

Create `deploy/enable-gemini-live.sh` with mode `0755`:

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT="${GCP_PROJECT_ID:?set GCP_PROJECT_ID}"

echo "→ Enabling Generative Language API on $PROJECT"
gcloud services enable generativelanguage.googleapis.com --project="$PROJECT"

echo "→ Creating API key 'catdv-live-tokens'"
gcloud alpha services api-keys create \
  --display-name="catdv-live-tokens" \
  --api-target="service=generativelanguage.googleapis.com" \
  --project="$PROJECT"

echo "→ Printing key value (paste into .env as GEMINI_API_KEY):"
KEY_NAME="$(gcloud alpha services api-keys list \
  --filter='displayName=catdv-live-tokens' \
  --format='value(name)' --project="$PROJECT" | head -1)"
gcloud alpha services api-keys get-key-string "$KEY_NAME" --project="$PROJECT"
```

Make it executable:

```bash
chmod +x deploy/enable-gemini-live.sh
```

- [ ] **Step 3: Commit**

```bash
git add deploy/enable-gemini-live.sh
git commit -m "feat(deploy): gcloud script to enable gemini live + mint api key"
```

---

## Task 26: README updates

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the section**

Open `README.md`. Find the existing env-vars / setup section and append:

```markdown
### Gemini Live clip assistant (optional)

Voice-driven Czech assistant on the clip-detail page. Browser opens a WebSocket
directly to Google's Gemini Live API using ephemeral tokens minted by the
backend; audio bytes never traverse our process.

**One-shot infrastructure setup:**

```bash
GCP_PROJECT_ID=<your-project> ./deploy/enable-gemini-live.sh
# Copy the printed key into .env:
echo 'GEMINI_API_KEY=<key>' >> .env
```

**Env vars added (all optional; Live feature is disabled until `GEMINI_API_KEY` is set):**

- `GEMINI_API_KEY` — Generative Language API key.
- `GEMINI_LIVE_MODEL` — default `gemini-2.5-flash-preview-native-audio-dialog`.
- `GEMINI_LIVE_VOICE` — default `Aoede`.
- `GEMINI_LIVE_INACTIVITY_S` — default `60` (seconds of mutual silence → auto-close).

Sessions are stored in `live_sessions` SQLite table and surface as a History
tab on the clip page. Output is read-only — nothing is auto-pushed to draft
annotations.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(readme): document gemini live setup + env vars"
```

---

## Task 27: Manual verification

This is the gate before shipping. With a real `GEMINI_API_KEY` in `.env`:

- [ ] Start the dev server: `./run.sh` (or `.venv/bin/python -m uvicorn backend.app.main:app --reload --port 8765`).
- [ ] In the browser, navigate to a clip with `duration_secs > 0`.
- [ ] **`🎤 Live` button appears** only when `mode == "online"` and clip has duration > 0.
- [ ] First click **triggers mic permission prompt**. Deny → inline error in header, header reverts.
- [ ] Allow → **header overlays with REC / Send frame / Stop**. Player + annotation column still interactive.
- [ ] **Speak Czech**: round-trip latency feels < ~600 ms. Gemini responds in Czech voice.
- [ ] Mention something visible to Gemini (a car / house / clothing) — confirm relevant Czech answer.
- [ ] Ask: *"co o tomhle vím z CatDV a co jsem si k tomu psal?"* — confirm Gemini distinguishes published vs draft.
- [ ] **Pause `<video>`** during conversation — verify frame is auto-sent (Gemini reacts to new frame within ~1 turn). Manual **📸 Send frame** also works.
- [ ] Say *"konec"* or *"děkuji, ukonči to"* — `end_session` tool fires, session closes.
- [ ] Stay silent for 60 s — session ends with `end_reason="inactivity"`.
- [ ] Reload the page mid-session — verify the transcript appears in History on reload (uses `beforeunload` + `sendBeacon`).
- [ ] Open **History** tab — verify session appears; expand → shows transcript + Czech summary. Click *Generovat shrnutí* on a summary-less session and confirm it fills in.
- [ ] Ask a location question — confirm `googleSearch` is exercised (Gemini cites a place or year). `search_calls` column in `live_sessions` increments (verify in `sqlite3 data/app.db 'SELECT id, search_calls FROM live_sessions ORDER BY created_at DESC LIMIT 5'`).

- [ ] **If all green, commit any final tweaks. Otherwise, file the issue and stop.**

```bash
git status   # should be clean
```

---

# Self-review notes (for plan author)

- **Spec coverage:** every section of the spec maps to at least one task:
  - §2.1 entry point → Task 23.
  - §2.2 active-session overlay → Task 23.
  - §2.3 frame delivery → Tasks 20 + 23.
  - §2.4 session end (5 end_reasons) → routes (Task 12), close paths (Tasks 17–22, 27).
  - §2.5 History panel → Task 24.
  - §3.1 surface choice (Gemini Developer API) → all of Phase 4 + 5.
  - §3.2 components diagram → Phases 4–6 structure.
  - §3.3 setup payload → Task 8.
  - §3.4 Czech system instruction → Task 6.
  - §3.5 end_session function tool → Tasks 8 + 19.
  - §3.6 inactivity timer → Task 22.
  - §4.1 routes → Tasks 11–14.
  - §4.2 service → Tasks 8, 9, 10.
  - §4.3 repository → Task 4.
  - §4.4 schema → Task 2.
  - §4.5 settings → Task 1.
  - §5 frontend → Phase 6 + Task 23.
  - §6 error handling → covered piecewise (mic denial in Task 18 / state machine; WSS error in Task 19; summarize failure surface in Task 24).
  - §7 infra → Task 25.
  - §8.1 automated tests → Phase 1–5 tests.
  - §8.2 manual checklist → Task 27.
  - §9 files touched → matches Tasks above.
  - §10 open items → resolved in "Pinned implementation details" at top.

- **Type / name consistency check:**
  - `LiveSession` (model) — used identically in repo, service, routes ✓
  - `LiveSessionsRepo` — Task 4 + every consumer ✓
  - `assemble_setup_payload`, `mint_ephemeral_token`, `summarize` — single signatures across Tasks 8/9/10/11–14 ✓
  - `setup_payload.initial_context_turn` — added in Task 8, stripped from outgoing `bidiGenerateContentSetup` in Task 9, re-attached on browser-side WSS in Task 19 ✓
  - `end_reason` enum (5 values) consistent across model (Task 3), repo (Task 4), route Pydantic (Task 12), JS (Tasks 17–22, 27) ✓

- **No placeholders.** Every step has runnable code or an exact command.

- **Migration filename:** the next free index after `0009_prompts_and_versions.sql` is `0010_live_sessions.sql` (confirmed by `ls backend/migrations/`).

- **Test paths verified** against existing convention (`tests/unit/` for pure logic, `tests/integration/` for routes / DB-touching).
