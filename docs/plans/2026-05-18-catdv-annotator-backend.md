# CatDV Annotator — Backend Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the backend engine that can run a full CatDV → Gemini → review → write-back annotation cycle, drivable via HTTP API and pytest. No UI in this plan — UI is Plan B (`2026-05-18-catdv-annotator-ui.md`).

**Architecture:** FastAPI app with one AppContext singleton wiring three external clients (CatDV REST, Google Cloud Storage, Vertex AI Gemini) + pluggable proxy resolver (REST cache in dev, filesystem in prod) + repository layer over SQLite + one async job worker that runs annotation batches. All state in SQLite. Spec: `docs/specs/2026-05-18-catdv-annotator-design.md`.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, httpx, google-genai (Vertex), google-cloud-storage, google-cloud-secret-manager, aiosqlite, Pydantic v2, sse-starlette, python-json-logger, Ruff, pytest, pytest-asyncio. Package mgr: `uv`. Run via `.venv/bin/python` per global rules.

---

## Conventions used throughout this plan

- **TDD:** every task writes the failing test first, then the minimal implementation.
- **Commits:** conventional commits (`feat:`, `test:`, `chore:`, `fix:`, `refactor:`). One commit per task by default.
- **Paths:** absolute project paths assumed rooted at `catdv-annotator/`. The plan reads `backend/app/...` etc.
- **Python execution:** all commands use `.venv/bin/python` and `.venv/bin/pytest` per the user's global rules.
- **No emojis** in code or commit messages.
- **No "Similar to Task N":** every task carries its own complete code.

## File structure (final)

```
catdv-annotator/
├── pyproject.toml                          # Task 1
├── .env.example                            # Task 2
├── run.sh                                  # Task 40
├── README.md                               # exists
├── .gitignore                              # exists
├── docs/
│   ├── specs/...                           # exists
│   ├── plans/...                           # this file
│   ├── decisions.md                        # Task 1
│   └── DEPLOY.md                           # Task 38
├── scripts/
│   └── setup-gcp.sh                        # Task 38
├── backend/app/
│   ├── __init__.py                         # Task 1
│   ├── main.py                             # Task 1, expanded Task 31
│   ├── settings.py                         # Task 2
│   ├── logging_setup.py                    # Task 3
│   ├── secrets.py                          # Task 2
│   ├── db.py                               # Task 4
│   ├── migrations_runner.py                # Task 4
│   ├── context.py                          # Task 30
│   ├── startup.py                          # Task 31
│   ├── timecode.py                         # Task 6
│   ├── models/{__init__,catdv,template,job,annotation}.py  # Tasks 7, 8, 21, 22
│   ├── services/
│   │   ├── catdv_client.py                 # Tasks 9-13
│   │   ├── gcs.py                          # Task 14
│   │   ├── gemini.py                       # Tasks 15-16
│   │   ├── proxy_resolver.py               # Tasks 17-19
│   │   ├── target_map.py                   # Task 26
│   │   ├── payload_builder.py              # Task 27
│   │   ├── annotator.py                    # Task 28
│   │   ├── events.py                       # Task 29
│   ├── repositories/
│   │   ├── templates.py                    # Task 20
│   │   ├── jobs.py                         # Task 21
│   │   ├── annotations.py                  # Task 22
│   │   ├── review_items.py                 # Task 23
│   │   ├── write_log.py                    # Task 23
│   │   ├── proxy_cache.py                  # Task 24
│   │   ├── gcs_files.py                    # Task 25
│   ├── routes/
│   │   ├── templates.py                    # Task 32
│   │   ├── catdv.py                        # Task 33
│   │   ├── jobs.py                         # Task 34
│   │   ├── review.py                       # Task 35
│   │   ├── media.py                        # Task 36
│   │   ├── events.py                       # Task 37
├── backend/migrations/0001_initial.sql     # Task 5
├── backend/seeds/default_template.json     # Task 39
└── tests/
    ├── conftest.py                         # Task 1
    ├── unit/                               # various
    ├── integration/                        # various
    ├── fakes/{fake_catdv,fake_gemini}.py
    └── fixtures/{clip_sample.json, ...}
```

---

## Phase 1 — Project foundation (Tasks 1–5)

End state: app starts up, serves `GET /api/health`, has a SQLite schema, all migrations applied at startup.

### Task 1: Project scaffolding & FastAPI hello

**Files:**
- Create: `pyproject.toml`
- Create: `backend/app/__init__.py`
- Create: `backend/app/main.py`
- Create: `tests/conftest.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/test_health.py`
- Create: `docs/decisions.md`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "catdv-annotator"
version = "0.0.1"
description = "AI annotation engine for the Pragafilm CatDV archive"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "httpx>=0.27",
  "google-genai>=0.3",
  "google-cloud-storage>=2.18",
  "google-cloud-secret-manager>=2.20",
  "aiosqlite>=0.20",
  "pydantic>=2.9",
  "pydantic-settings>=2.6",
  "sse-starlette>=2.1",
  "python-json-logger>=2.0",
  "jinja2>=3.1",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "ruff>=0.7",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]
```

- [ ] **Step 2: Create the venv and install deps**

```bash
cd /Users/peterhora/Documents/futurememorylab/sikl/catdv-annotator
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -c "import fastapi, uvicorn, httpx, pydantic; print('ok')"
```

Expected last line: `ok`.

- [ ] **Step 3: Create `backend/app/__init__.py`** (empty file, just `touch`).

- [ ] **Step 4: Write failing health test**

`tests/integration/test_health.py`:

```python
from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

`tests/conftest.py`:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

- [ ] **Step 5: Run test, see it fail**

```bash
.venv/bin/pytest tests/integration/test_health.py -v
```

Expected: ImportError / ModuleNotFoundError on `backend.app.main`.

- [ ] **Step 6: Minimal implementation**

`backend/app/main.py`:

```python
from fastapi import FastAPI

app = FastAPI(title="CatDV Annotator")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 7: Run test, see it pass**

```bash
.venv/bin/pytest tests/integration/test_health.py -v
```

Expected: 1 passed.

- [ ] **Step 8: Create `docs/decisions.md`**

```markdown
# Architecture Decisions

Each decision: one paragraph — context, alternatives, choice, why. Append below.

## 2026-05-18: Python-only stack, no Node frontend

**Context:** The PoC (Archive-AI) used a Node/React/TS stack. Maintaining two
package.json files, two test runners, and TS↔Python type drift consumed
significant time.

**Alternatives:** React+TS SPA via Vite, Svelte SPA.

**Choice:** Server-rendered Jinja2 + HTMX + Alpine.js + Tailwind standalone CLI.
The UI is forms + one video screen; React is overkill.

**Why:** One language top to bottom, no npm/Node, no build step beyond Tailwind
CLI, smaller cognitive surface for future single-maintainer work.
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml backend/app/__init__.py backend/app/main.py \
  tests/conftest.py tests/__init__.py tests/integration/__init__.py \
  tests/unit/__init__.py tests/integration/test_health.py docs/decisions.md
git commit -m "feat: scaffold FastAPI app with health endpoint"
```

(Create empty `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py` if missing.)

---

### Task 2: Settings module + .env.example + secrets stub

**Files:**
- Create: `backend/app/settings.py`
- Create: `backend/app/secrets.py`
- Create: `.env.example`
- Create: `tests/unit/test_settings.py`

- [ ] **Step 1: Write failing settings test**

`tests/unit/test_settings.py`:

```python
import os

import pytest


def test_settings_loads_from_env(monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://example.test:8080")
    monkeypatch.setenv("CATDV_USERNAME", "user1")
    monkeypatch.setenv("CATDV_PASSWORD", "pw")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", "/tmp/cdv")

    from backend.app.settings import Settings
    s = Settings()
    assert s.app_env == "dev"
    assert s.catdv_base_url == "http://example.test:8080"
    assert s.catdv_catalog_id == 881507
    assert s.proxy_source == "rest"
    assert s.gemini_model == "gemini-2.5-pro"  # default


def test_settings_rejects_filesystem_without_root(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("CATDV_USERNAME", "x")
    monkeypatch.setenv("CATDV_PASSWORD", "x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "filesystem")
    monkeypatch.delenv("PROXY_FS_ROOT", raising=False)
    monkeypatch.setenv("DATA_DIR", "/tmp/cdv")

    from backend.app.settings import Settings
    with pytest.raises(ValueError, match="PROXY_FS_ROOT"):
        Settings()
```

- [ ] **Step 2: Run test, expect failure**

```bash
.venv/bin/pytest tests/unit/test_settings.py -v
```

Expected: ModuleNotFoundError on `backend.app.settings`.

- [ ] **Step 3: Implement `backend/app/settings.py`**

```python
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "prod"] = "dev"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8765
    data_dir: Path = Field(default=Path("./data"))

    catdv_base_url: str
    catdv_username: str | None = None  # may be loaded from Secret Manager in prod
    catdv_password: str | None = None
    catdv_catalog_id: int

    proxy_source: Literal["rest", "filesystem"] = "rest"
    proxy_fs_root: Path | None = None
    proxy_path_template: str | None = None
    proxy_cache_cap_gb: float = 20.0

    gcp_project_id: str
    gcp_location: str = "europe-west3"
    gcs_bucket_name: str
    google_application_credentials: Path | None = None
    gemini_model: str = "gemini-2.5-pro"

    @model_validator(mode="after")
    def _validate_proxy(self) -> "Settings":
        if self.proxy_source == "filesystem" and self.proxy_fs_root is None:
            raise ValueError("PROXY_FS_ROOT is required when PROXY_SOURCE=filesystem")
        return self


def load_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Implement `backend/app/secrets.py`**

```python
import os
from functools import lru_cache


@lru_cache(maxsize=64)
def get_secret(name: str, *, app_env: str, project_id: str | None = None) -> str:
    """Return secret value. In dev reads from env; in prod reads from Secret Manager."""
    if app_env != "prod":
        value = os.environ.get(name)
        if value is None:
            raise KeyError(f"Secret {name} not found in environment (APP_ENV=dev)")
        return value

    # Lazy import so dev tests don't open a gRPC channel
    from google.cloud import secretmanager  # type: ignore[import-not-found]

    if not project_id:
        raise RuntimeError("project_id required to fetch secrets in prod")

    client = secretmanager.SecretManagerServiceClient()
    resource = f"projects/{project_id}/secrets/{name}/versions/latest"
    response = client.access_secret_version(request={"name": resource})
    return response.payload.data.decode("utf-8")
```

- [ ] **Step 5: Create `.env.example`**

```ini
# App
APP_ENV=dev
BIND_HOST=127.0.0.1
BIND_PORT=8765
DATA_DIR=./data

# CatDV
CATDV_BASE_URL=http://192.168.1.41:8080
CATDV_USERNAME=klientAI
CATDV_PASSWORD=changeme
CATDV_CATALOG_ID=881507

# Proxy resolution
PROXY_SOURCE=rest
PROXY_FS_ROOT=
PROXY_PATH_TEMPLATE=
PROXY_CACHE_CAP_GB=20

# GCP / Vertex AI
GCP_PROJECT_ID=pragafilm-catdv-annotator
GCP_LOCATION=europe-west3
GCS_BUCKET_NAME=pragafilm-catdv-annotator-proxies
GOOGLE_APPLICATION_CREDENTIALS=/Users/peterhora/.gcp/catdv-annotator-key.json
GEMINI_MODEL=gemini-2.5-pro
```

- [ ] **Step 6: Run tests, expect pass**

```bash
.venv/bin/pytest tests/unit/test_settings.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/settings.py backend/app/secrets.py .env.example tests/unit/test_settings.py
git commit -m "feat: settings + secrets loader"
```

---

### Task 3: Structured JSON logging

**Files:**
- Create: `backend/app/logging_setup.py`
- Create: `tests/unit/test_logging_setup.py`

- [ ] **Step 1: Failing test**

`tests/unit/test_logging_setup.py`:

```python
import io
import json
import logging

from backend.app.logging_setup import configure_logging


def test_emits_structured_json(monkeypatch):
    stream = io.StringIO()
    configure_logging(stream=stream, level="INFO")
    logger = logging.getLogger("test")
    logger.info("hello", extra={"job_id": 42, "clip_id": 7})

    line = stream.getvalue().strip().splitlines()[-1]
    record = json.loads(line)
    assert record["message"] == "hello"
    assert record["job_id"] == 42
    assert record["clip_id"] == 7
    assert record["levelname"] == "INFO"
```

- [ ] **Step 2: Run, see fail**

```bash
.venv/bin/pytest tests/unit/test_logging_setup.py -v
```

- [ ] **Step 3: Implement `backend/app/logging_setup.py`**

```python
import logging
import sys
from typing import IO

from pythonjsonlogger.json import JsonFormatter


def configure_logging(stream: IO[str] | None = None, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            rename_fields={"asctime": "ts"},
        )
    )
    root.addHandler(handler)

    # Quiet some noisy libs at the default level
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("urllib3").setLevel("WARNING")
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_logging_setup.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/logging_setup.py tests/unit/test_logging_setup.py
git commit -m "feat: structured JSON logging via python-json-logger"
```

---

### Task 4: SQLite connection + migration runner

**Files:**
- Create: `backend/app/db.py`
- Create: `backend/app/migrations_runner.py`
- Create: `backend/migrations/.gitkeep`
- Create: `tests/integration/test_migrations.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_migrations.py`:

```python
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.mark.asyncio
async def test_apply_migrations_creates_meta_table(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migrations_dir = tmp_path / "migs"
    migrations_dir.mkdir()
    (migrations_dir / "0001_init.sql").write_text(
        "CREATE TABLE thing (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
    )

    async with open_db(db_path) as conn:
        await apply_migrations(conn, migrations_dir)

    async with open_db(db_path) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [row[0] for row in await cur.fetchall()]
    assert "schema_migrations" in names
    assert "thing" in names


@pytest.mark.asyncio
async def test_apply_migrations_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migrations_dir = tmp_path / "migs"
    migrations_dir.mkdir()
    (migrations_dir / "0001_init.sql").write_text(
        "CREATE TABLE thing (id INTEGER PRIMARY KEY);"
    )

    async with open_db(db_path) as conn:
        await apply_migrations(conn, migrations_dir)
        await apply_migrations(conn, migrations_dir)  # second run must not error
        cur = await conn.execute("SELECT count(*) FROM schema_migrations")
        n = (await cur.fetchone())[0]
    assert n == 1
```

- [ ] **Step 2: Implement `backend/app/db.py`**

```python
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


@asynccontextmanager
async def open_db(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn
    finally:
        await conn.close()
```

- [ ] **Step 3: Implement `backend/app/migrations_runner.py`**

```python
from pathlib import Path

import aiosqlite

META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


async def apply_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[str]:
    """Apply any *.sql files under migrations_dir not already in schema_migrations.

    Returns the names that were applied this run.
    """
    await conn.execute(META_TABLE_SQL)
    await conn.commit()

    cur = await conn.execute("SELECT name FROM schema_migrations")
    applied = {row[0] for row in await cur.fetchall()}

    sql_files = sorted(p for p in migrations_dir.glob("*.sql"))
    newly_applied: list[str] = []
    for path in sql_files:
        if path.name in applied:
            continue
        sql = path.read_text()
        await conn.executescript(sql)
        await conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (path.name,))
        await conn.commit()
        newly_applied.append(path.name)
    return newly_applied
```

- [ ] **Step 4: Create `backend/migrations/.gitkeep`** (touch).

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_migrations.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/db.py backend/app/migrations_runner.py \
  backend/migrations/.gitkeep tests/integration/test_migrations.py
git commit -m "feat: aiosqlite connection + idempotent migration runner"
```

---

### Task 5: Initial migration with full schema

**Files:**
- Create: `backend/migrations/0001_initial.sql`
- Create: `tests/integration/test_initial_schema.py`

- [ ] **Step 1: Write failing test**

`tests/integration/test_initial_schema.py`:

```python
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

EXPECTED_TABLES = {
    "templates", "jobs", "job_items", "proxy_cache", "gcs_files",
    "annotations", "annotations_fts", "review_items", "write_log",
    "embeddings", "tags", "schema_migrations",
}


@pytest.mark.asyncio
async def test_initial_migration_creates_all_tables(tmp_path: Path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual')")
        names = {row[0] for row in await cur.fetchall()}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


@pytest.mark.asyncio
async def test_fts5_handles_czech_diacritics(tmp_path: Path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await conn.execute("""
            INSERT INTO annotations
              (catdv_clip_id, catdv_clip_name, template_id, model, prompt_used,
               raw_response, structured_output, clip_snapshot, created_at)
            VALUES (1, 'Polčakovi', 1, 'gemini-2.5-pro', 'p', '{}', '{}', '{}', '2026-05-18')
        """)
        # Insert a dummy template row first to satisfy the FK
        # (rolled back: relax FKs for this test by deferring or pre-inserting template)
```

This test will get refined in the next step — first run the schema-existence test alone:

- [ ] **Step 2: Run test, expect fail (missing migration file)**

```bash
.venv/bin/pytest tests/integration/test_initial_schema.py::test_initial_migration_creates_all_tables -v
```

- [ ] **Step 3: Write `backend/migrations/0001_initial.sql`**

```sql
-- Templates
CREATE TABLE templates (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,
  description     TEXT,
  prompt          TEXT NOT NULL,
  output_schema   TEXT NOT NULL,
  target_map      TEXT NOT NULL,
  model           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  archived        INTEGER NOT NULL DEFAULT 0
);

-- Jobs and items
CREATE TABLE jobs (
  id              INTEGER PRIMARY KEY,
  template_id     INTEGER NOT NULL REFERENCES templates(id),
  status          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  started_at      TEXT,
  finished_at     TEXT,
  total_clips     INTEGER NOT NULL,
  notes           TEXT
);

CREATE TABLE job_items (
  id              INTEGER PRIMARY KEY,
  job_id          INTEGER NOT NULL REFERENCES jobs(id),
  catdv_clip_id   INTEGER NOT NULL,
  status          TEXT NOT NULL,
  error_message   TEXT,
  annotation_id   INTEGER,
  started_at      TEXT,
  finished_at     TEXT
);
CREATE INDEX idx_job_items_job ON job_items(job_id);
CREATE INDEX idx_job_items_status ON job_items(status);

-- Local proxy file cache (rest mode only)
CREATE TABLE proxy_cache (
  catdv_clip_id   INTEGER PRIMARY KEY,
  file_path       TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  etag            TEXT,
  downloaded_at   TEXT NOT NULL,
  last_used_at    TEXT NOT NULL
);

-- GCS upload registry (reused across re-annotation)
CREATE TABLE gcs_files (
  catdv_clip_id   INTEGER PRIMARY KEY,
  gcs_uri         TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  size_bytes      INTEGER NOT NULL,
  sha256          TEXT NOT NULL,
  uploaded_at     TEXT NOT NULL,
  last_used_at    TEXT NOT NULL
);

-- Annotation archive
CREATE TABLE annotations (
  id                 INTEGER PRIMARY KEY,
  catdv_clip_id      INTEGER NOT NULL,
  catdv_clip_name    TEXT NOT NULL,
  template_id        INTEGER NOT NULL REFERENCES templates(id),
  job_id             INTEGER REFERENCES jobs(id),
  model              TEXT NOT NULL,
  prompt_used        TEXT NOT NULL,
  raw_response       TEXT NOT NULL,
  structured_output  TEXT NOT NULL,
  clip_snapshot      TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
CREATE INDEX idx_annotations_clip ON annotations(catdv_clip_id);
CREATE INDEX idx_annotations_template ON annotations(template_id);

CREATE VIRTUAL TABLE annotations_fts USING fts5(
  clip_name, prompt_used, structured_output, raw_response,
  content='annotations', content_rowid='id',
  tokenize = "unicode61 remove_diacritics 2"
);

CREATE TRIGGER annotations_ai AFTER INSERT ON annotations BEGIN
  INSERT INTO annotations_fts(rowid, clip_name, prompt_used, structured_output, raw_response)
  VALUES (new.id, new.catdv_clip_name, new.prompt_used, new.structured_output, new.raw_response);
END;

CREATE TRIGGER annotations_ad AFTER DELETE ON annotations BEGIN
  INSERT INTO annotations_fts(annotations_fts, rowid, clip_name, prompt_used, structured_output, raw_response)
  VALUES ('delete', old.id, old.catdv_clip_name, old.prompt_used, old.structured_output, old.raw_response);
END;

-- Review queue
CREATE TABLE review_items (
  id                 INTEGER PRIMARY KEY,
  annotation_id      INTEGER NOT NULL REFERENCES annotations(id),
  catdv_clip_id      INTEGER NOT NULL,
  kind               TEXT NOT NULL,
  target_identifier  TEXT,
  proposed_value     TEXT NOT NULL,
  edited_value       TEXT,
  decision           TEXT NOT NULL,
  decided_at         TEXT,
  applied_at         TEXT
);
CREATE INDEX idx_review_items_annotation ON review_items(annotation_id);
CREATE INDEX idx_review_items_clip ON review_items(catdv_clip_id);
CREATE INDEX idx_review_items_decision ON review_items(decision);

CREATE TABLE write_log (
  id              INTEGER PRIMARY KEY,
  catdv_clip_id   INTEGER NOT NULL,
  annotation_id   INTEGER REFERENCES annotations(id),
  payload         TEXT NOT NULL,
  response        TEXT NOT NULL,
  status          TEXT NOT NULL,
  written_at      TEXT NOT NULL
);

-- Reserved for future search/curation app
CREATE TABLE embeddings (
  annotation_id   INTEGER PRIMARY KEY REFERENCES annotations(id),
  model           TEXT NOT NULL,
  vector          BLOB NOT NULL,
  created_at      TEXT NOT NULL
);

CREATE TABLE tags (
  annotation_id   INTEGER NOT NULL REFERENCES annotations(id),
  tag             TEXT NOT NULL,
  source          TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  PRIMARY KEY (annotation_id, tag)
);
```

- [ ] **Step 4: Replace the second test with a clean FTS test**

Update `tests/integration/test_initial_schema.py`:

```python
from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"

EXPECTED_TABLES = {
    "templates", "jobs", "job_items", "proxy_cache", "gcs_files",
    "annotations", "annotations_fts", "review_items", "write_log",
    "embeddings", "tags", "schema_migrations",
}


async def _seed_template(conn):
    await conn.execute(
        """
        INSERT INTO templates (id, name, prompt, output_schema, target_map, model,
                               created_at, updated_at)
        VALUES (1, 't', 'p', '{}', '{}', 'gemini-2.5-pro', '2026-05-18', '2026-05-18')
        """
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_initial_migration_creates_all_tables(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')"
        )
        names = {row[0] for row in await cur.fetchall()}
    assert EXPECTED_TABLES.issubset(names), f"missing: {EXPECTED_TABLES - names}"


@pytest.mark.asyncio
async def test_fts5_handles_czech_diacritics(tmp_path):
    db = tmp_path / "test.db"
    async with open_db(db) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed_template(conn)
        await conn.execute(
            """
            INSERT INTO annotations
              (catdv_clip_id, catdv_clip_name, template_id, model, prompt_used,
               raw_response, structured_output, clip_snapshot, created_at)
            VALUES (1, 'Polčakovi rodina', 1, 'gemini-2.5-pro',
                    'popiš scénu', '{}', '{}', '{}', '2026-05-18')
            """
        )
        await conn.commit()

        # match without diacritics should still find it
        cur = await conn.execute(
            "SELECT count(*) FROM annotations_fts WHERE annotations_fts MATCH 'Polcakovi'"
        )
        assert (await cur.fetchone())[0] == 1
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_initial_schema.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0001_initial.sql tests/integration/test_initial_schema.py
git commit -m "feat: initial schema with templates, jobs, annotations, FTS5"
```

---

## Phase 2 — Domain models & pure-logic utilities (Tasks 6–8)

End state: timecode conversions, CatDV models, template/target_map models are all defined and unit-tested. No I/O yet.

### Task 6: Timecode SMPTE conversion

**Files:**
- Create: `backend/app/timecode.py`
- Create: `tests/unit/test_timecode.py`

- [ ] **Step 1: Failing tests**

`tests/unit/test_timecode.py`:

```python
import pytest

from backend.app.timecode import secs_to_smpte, smpte_to_secs, snap_to_frame


@pytest.mark.parametrize(
    "secs,fps,expected",
    [
        (0.0, 25, "00:00:00:00"),
        (1.0, 25, "00:00:01:00"),
        (1.04, 25, "00:00:01:01"),
        (60.0, 25, "00:01:00:00"),
        (3600.0, 25, "01:00:00:00"),
        (10.0, 24, "00:00:10:00"),
        (10.0, 30, "00:00:10:00"),
    ],
)
def test_secs_to_smpte_basic(secs, fps, expected):
    assert secs_to_smpte(secs, fps) == expected


@pytest.mark.parametrize(
    "smpte,fps,expected",
    [
        ("00:00:00:00", 25, 0.0),
        ("00:00:01:00", 25, 1.0),
        ("00:00:01:12", 25, 1.48),  # 12/25 = 0.48
        ("00:01:00:00", 25, 60.0),
    ],
)
def test_smpte_to_secs_basic(smpte, fps, expected):
    assert smpte_to_secs(smpte, fps) == pytest.approx(expected, abs=1e-9)


def test_round_trip():
    for frames in range(0, 5000, 7):
        secs = frames / 25.0
        assert smpte_to_secs(secs_to_smpte(secs, 25), 25) == pytest.approx(secs, abs=1e-9)


def test_snap_to_frame_rounds_down_within_half_frame():
    # 25 fps: 1 frame = 0.04s. 1.039s should snap to frame 25 (1.00s).
    assert snap_to_frame(1.039, 25) == pytest.approx(1.04, abs=1e-9)
    assert snap_to_frame(1.0, 25) == pytest.approx(1.0, abs=1e-9)


def test_smpte_to_secs_rejects_garbage():
    with pytest.raises(ValueError):
        smpte_to_secs("not-a-timecode", 25)
```

- [ ] **Step 2: Run, see fail**

```bash
.venv/bin/pytest tests/unit/test_timecode.py -v
```

- [ ] **Step 3: Implement `backend/app/timecode.py`**

```python
import re

_SMPTE_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2}):(\d{2})$")


def secs_to_smpte(secs: float, fps: float) -> str:
    """Convert seconds to HH:MM:SS:FF using non-drop-frame counting."""
    total_frames = round(secs * fps)
    frames_per_sec = round(fps)
    ff = total_frames % frames_per_sec
    total_secs = total_frames // frames_per_sec
    ss = total_secs % 60
    mm = (total_secs // 60) % 60
    hh = total_secs // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def smpte_to_secs(smpte: str, fps: float) -> float:
    m = _SMPTE_RE.match(smpte.strip())
    if not m:
        raise ValueError(f"invalid SMPTE timecode: {smpte!r}")
    hh, mm, ss, ff = (int(x) for x in m.groups())
    frames_per_sec = round(fps)
    total_frames = ((hh * 3600 + mm * 60 + ss) * frames_per_sec) + ff
    return total_frames / fps


def snap_to_frame(secs: float, fps: float) -> float:
    """Round secs to the nearest whole-frame boundary at fps."""
    return round(secs * fps) / fps
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_timecode.py -v
```

Expected: all parametrized + 3 regular tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/timecode.py tests/unit/test_timecode.py
git commit -m "feat: SMPTE timecode conversion utilities"
```

---

### Task 7: CatDV domain models (envelope, clip, marker, field)

**Files:**
- Create: `backend/app/models/__init__.py`
- Create: `backend/app/models/catdv.py`
- Create: `tests/fixtures/clip_sample.json`
- Create: `tests/unit/test_catdv_models.py`

- [ ] **Step 1: Create the fixture**

`tests/fixtures/clip_sample.json` — minimal but realistic CatDV clip JSON:

```json
{
  "ID": 12345,
  "name": "Sample_Clip_01",
  "notes": "Some notes",
  "bigNotes": "",
  "format": "ProRes 422 HQ",
  "fps": 25,
  "in":  {"frm": 0,    "fmt": 25, "secs": 0.0,  "txt": "00:00:00:00"},
  "out": {"frm": 1500, "fmt": 25, "secs": 60.0, "txt": "00:01:00:00"},
  "duration": {"frm": 1500, "fmt": 25, "secs": 60.0, "txt": "00:01:00:00"},
  "markers": [
    {
      "name": "Scene A",
      "category": "Event",
      "in":  {"frm": 250, "fmt": 25, "secs": 10.0, "txt": "00:00:10:00"},
      "out": {"frm": 500, "fmt": 25, "secs": 20.0, "txt": "00:00:20:00"},
      "description": "first scene",
      "color": "white"
    }
  ],
  "thumbnailIDs": [98, 99],
  "posterID": 100,
  "media": {"sourceMediaID": 555},
  "importSource": {},
  "history": [],
  "fields": {
    "pragafilm.rok.natočení": ["1933"],
    "pragafilm.dekáda.natočení": "30.léta",
    "pragafilm.popis.materialu": "Rodinné záběry."
  }
}
```

- [ ] **Step 2: Failing tests**

`tests/unit/test_catdv_models.py`:

```python
import json
from pathlib import Path

from backend.app.models.catdv import Clip, Envelope, Marker, TimecodeQuad


FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "clip_sample.json"


def test_clip_parses_fixture():
    raw = json.loads(FIXTURE.read_text())
    clip = Clip.model_validate(raw)
    assert clip.id == 12345
    assert clip.name == "Sample_Clip_01"
    assert clip.fps == 25
    assert clip.duration.secs == 60.0
    assert len(clip.markers) == 1
    assert clip.markers[0].name == "Scene A"
    assert clip.fields["pragafilm.dekáda.natočení"] == "30.léta"


def test_clip_round_trips_through_json():
    raw = json.loads(FIXTURE.read_text())
    clip = Clip.model_validate(raw)
    again = clip.model_dump(mode="json", by_alias=True, exclude_none=False)
    # Critical fields preserved
    assert again["ID"] == 12345
    assert again["markers"][0]["in"]["frm"] == 250


def test_marker_optional_out():
    m = Marker.model_validate({
        "name": "Point",
        "in": {"frm": 100, "fmt": 25, "secs": 4.0, "txt": "00:00:04:00"},
    })
    assert m.out is None


def test_envelope_ok():
    env = Envelope.model_validate({"status": "OK", "errorMessage": None, "data": {"a": 1}})
    assert env.is_ok
    assert env.data == {"a": 1}


def test_envelope_auth():
    env = Envelope.model_validate({"status": "AUTH", "errorMessage": None, "data": None})
    assert not env.is_ok
    assert env.requires_reauth
```

- [ ] **Step 3: Implement `backend/app/models/__init__.py`** (empty file).

- [ ] **Step 4: Implement `backend/app/models/catdv.py`**

```python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TimecodeQuad(BaseModel):
    frm: int
    fmt: float
    secs: float
    txt: str

    model_config = ConfigDict(extra="allow")


class Marker(BaseModel):
    name: str
    category: str | None = None
    in_: TimecodeQuad = Field(alias="in")
    out: TimecodeQuad | None = None
    description: str | None = None
    color: str | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Clip(BaseModel):
    id: int = Field(alias="ID")
    name: str
    notes: str | None = None
    big_notes: str | None = Field(default=None, alias="bigNotes")
    format: str | None = None
    fps: float | None = None
    in_: TimecodeQuad | None = Field(default=None, alias="in")
    out: TimecodeQuad | None = None
    duration: TimecodeQuad | None = None
    markers: list[Marker] = []
    thumbnail_ids: list[int] = Field(default_factory=list, alias="thumbnailIDs")
    poster_id: int | None = Field(default=None, alias="posterID")
    media: dict[str, Any] = {}
    import_source: dict[str, Any] = Field(default_factory=dict, alias="importSource")
    history: list[Any] = []
    fields: dict[str, Any] = {}

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class Envelope(BaseModel):
    status: Literal["OK", "AUTH", "ERROR"]
    error_message: str | None = Field(default=None, alias="errorMessage")
    data: Any = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @property
    def is_ok(self) -> bool:
        return self.status == "OK"

    @property
    def requires_reauth(self) -> bool:
        return self.status == "AUTH"
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_catdv_models.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/__init__.py backend/app/models/catdv.py \
  tests/fixtures/clip_sample.json tests/unit/test_catdv_models.py
git commit -m "feat: CatDV envelope, clip, and marker models"
```

---

### Task 8: Template + target_map models

**Files:**
- Create: `backend/app/models/template.py`
- Create: `tests/unit/test_template_models.py`

- [ ] **Step 1: Failing tests**

`tests/unit/test_template_models.py`:

```python
import pytest

from backend.app.models.template import TargetMap, Template


def test_template_minimal():
    t = Template(
        name="Scene markers",
        prompt="Identify scenes",
        output_schema={"type": "object"},
        target_map={"scenes": {"kind": "markers"}},
        model="gemini-2.5-pro",
    )
    assert t.name == "Scene markers"
    assert t.target_map.fields["scenes"].kind == "markers"


def test_target_map_accepts_field_entry():
    tm = TargetMap.model_validate(
        {"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}
    )
    entry = tm.fields["decade"]
    assert entry.kind == "field"
    assert entry.identifier == "pragafilm.dekáda.natočení"


def test_target_map_accepts_note_entry_with_mode():
    tm = TargetMap.model_validate(
        {"summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"}}
    )
    entry = tm.fields["summary"]
    assert entry.kind == "note"
    assert entry.target == "pragafilm.popis.materialu"
    assert entry.mode == "append"


def test_target_map_field_requires_identifier():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "field"}})


def test_target_map_note_requires_target():
    with pytest.raises(ValueError):
        TargetMap.model_validate({"x": {"kind": "note"}})


def test_target_map_note_defaults_to_append():
    tm = TargetMap.model_validate(
        {"summary": {"kind": "note", "target": "notes"}}
    )
    assert tm.fields["summary"].mode == "append"
```

- [ ] **Step 2: Implement `backend/app/models/template.py`**

```python
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, RootModel, model_validator


class TargetEntry(BaseModel):
    kind: Literal["markers", "field", "note"]
    identifier: str | None = None       # required for kind=field
    target: str | None = None            # required for kind=note
    mode: Literal["append", "replace"] = "append"  # for kind=note

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_required(self) -> "TargetEntry":
        if self.kind == "field" and not self.identifier:
            raise ValueError("kind=field requires 'identifier'")
        if self.kind == "note" and not self.target:
            raise ValueError("kind=note requires 'target'")
        return self


class TargetMap(RootModel[dict[str, TargetEntry]]):
    @property
    def fields(self) -> dict[str, TargetEntry]:
        return self.root


class Template(BaseModel):
    id: int | None = None
    name: str
    description: str | None = None
    prompt: str
    output_schema: dict[str, Any]
    target_map: TargetMap
    model: str
    archived: bool = False

    model_config = ConfigDict(extra="allow")
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_template_models.py -v
```

Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/models/template.py tests/unit/test_template_models.py
git commit -m "feat: template + target_map models with validation"
```

---

## Phase 3 — CatDV client (Tasks 9–13)

End state: a fully exercised `CatdvClient` that logs in, re-authenticates on AUTH, lists/gets/PUTs clips, and downloads proxies with Range/resume. All tested against a local fake CatDV server.

### Task 9: Fake CatDV server + client login

**Files:**
- Create: `tests/fakes/__init__.py`
- Create: `tests/fakes/fake_catdv.py`
- Create: `backend/app/services/__init__.py`
- Create: `backend/app/services/catdv_client.py`
- Create: `tests/integration/test_catdv_client_login.py`

- [ ] **Step 1: Implement the fake CatDV server**

`tests/fakes/__init__.py` — empty file.

`tests/fakes/fake_catdv.py`:

```python
import contextlib
import socket
import threading
import time
from typing import Iterator

import uvicorn
from fastapi import FastAPI, Request, Response


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeCatdv:
    """In-process fake CatDV server controllable from tests."""

    def __init__(self) -> None:
        self.app = FastAPI()
        self.session_cookie = "JSESSIONID=fake-session"
        self.valid_creds = {"klientAI": "secret"}
        self.clips: dict[int, dict] = {}
        self.proxies: dict[int, bytes] = {}
        self.force_auth_until: float = 0.0   # set to (time.time() + N) to force AUTH responses
        self.put_log: list[tuple[int, dict]] = []
        self._register_routes()

    def _envelope(self, status: str, data=None, msg: str | None = None) -> dict:
        return {"status": status, "errorMessage": msg, "data": data}

    def _register_routes(self) -> None:
        @self.app.post("/catdv/api/9/session")
        async def login(req: Request):
            body = await req.json()
            if self.valid_creds.get(body.get("username")) == body.get("password"):
                response = Response(content='{"status":"OK","errorMessage":null,"data":null}',
                                    media_type="application/json")
                response.set_cookie("JSESSIONID", "fake-session")
                return response
            return self._envelope("ERROR", msg="Invalid user name or password")

        @self.app.get("/catdv/api/9/clips/{clip_id}")
        async def get_clip(clip_id: int, request: Request):
            if time.time() < self.force_auth_until or request.cookies.get("JSESSIONID") != "fake-session":
                return self._envelope("AUTH")
            clip = self.clips.get(clip_id)
            if not clip:
                return self._envelope("ERROR", msg="Not found")
            return self._envelope("OK", data=clip)

        @self.app.put("/catdv/api/9/clips/{clip_id}")
        async def put_clip(clip_id: int, request: Request):
            if request.cookies.get("JSESSIONID") != "fake-session":
                return self._envelope("AUTH")
            body = await request.json()
            self.put_log.append((clip_id, body))
            existing = self.clips.get(clip_id, {})
            existing.update(body)
            self.clips[clip_id] = existing
            return self._envelope("OK", data={"ID": clip_id, "modifyDate": "2026-05-18"})

        @self.app.get("/catdv/api/9/clips/{clip_id}/media")
        async def get_media(clip_id: int, request: Request):
            if request.cookies.get("JSESSIONID") != "fake-session":
                return Response(status_code=401)
            blob = self.proxies.get(clip_id)
            if blob is None:
                return Response(status_code=404)
            range_header = request.headers.get("range")
            if range_header and range_header.startswith("bytes="):
                start_s, _, end_s = range_header[6:].partition("-")
                start = int(start_s)
                end = int(end_s) if end_s else len(blob) - 1
                chunk = blob[start:end + 1]
                return Response(
                    content=chunk,
                    status_code=206,
                    media_type="video/quicktime",
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{len(blob)}",
                        "Content-Length": str(len(chunk)),
                        "Accept-Ranges": "bytes",
                    },
                )
            return Response(
                content=blob,
                media_type="video/quicktime",
                headers={"Accept-Ranges": "bytes", "Content-Length": str(len(blob))},
            )


@contextlib.contextmanager
def running_fake_catdv() -> Iterator[tuple[str, FakeCatdv]]:
    """Boot a fake CatDV on a free port. Yields (base_url, fake)."""
    fake = FakeCatdv()
    port = _free_port()
    config = uvicorn.Config(fake.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    # Wait until ready
    deadline = time.time() + 5
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", fake
    finally:
        server.should_exit = True
        thread.join(timeout=5)
```

- [ ] **Step 2: Failing client login test**

`tests/integration/test_catdv_client_login.py`:

```python
import pytest

from backend.app.services.catdv_client import CatdvClient, CatdvAuthError
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_login_succeeds_with_valid_creds():
    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Session cookie is set; verify by issuing a session-protected call
            fake.clips[1] = {"ID": 1, "name": "c"}
            clip = await client.get_clip(1)
            assert clip["ID"] == 1


@pytest.mark.asyncio
async def test_login_fails_with_bad_creds():
    with running_fake_catdv() as (base_url, _):
        client = CatdvClient(base_url=base_url, username="klientAI", password="wrong")
        async with client:
            with pytest.raises(CatdvAuthError):
                await client.login()
```

- [ ] **Step 3: Implement `backend/app/services/__init__.py`** (empty).

- [ ] **Step 4: Implement `backend/app/services/catdv_client.py`** (initial slice)

```python
import asyncio
from typing import Any, Self

import httpx

from backend.app.models.catdv import Envelope


class CatdvAuthError(RuntimeError):
    """Raised when the CatDV server rejects credentials."""


class CatdvError(RuntimeError):
    """Raised for non-AUTH ERROR envelopes."""


class CatdvClient:
    """Thin async wrapper around CatDV REST. One client per app process.

    Re-authenticates transparently when the server returns an AUTH envelope.
    """

    def __init__(self, base_url: str, username: str, password: str,
                 timeout_secs: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._client: httpx.AsyncClient | None = None
        self._login_lock = asyncio.Lock()
        self._timeout = timeout_secs

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def http(self) -> httpx.AsyncClient:
        assert self._client is not None, "CatdvClient must be used as async context manager"
        return self._client

    async def login(self) -> None:
        async with self._login_lock:
            resp = await self.http.post(
                f"{self._base}/catdv/api/9/session",
                json={"username": self._username, "password": self._password},
            )
            env = Envelope.model_validate(resp.json())
            if not env.is_ok:
                raise CatdvAuthError(env.error_message or "login rejected")

    async def get_clip(self, clip_id: int) -> dict[str, Any]:
        env = await self._call_json("GET", f"/catdv/api/9/clips/{clip_id}")
        return env.data
```

We also need `_call_json`:

```python
    async def _call_json(self, method: str, path: str, *, json: Any = None) -> Envelope:
        """Issue a JSON request. Re-login once on AUTH; raise on ERROR."""
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, json=json)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            await self.login()
            resp = await self.http.request(method, url, json=json)
            env = Envelope.model_validate(resp.json())
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env
```

Place `_call_json` as an additional method on `CatdvClient`.

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_client_login.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/fakes/__init__.py tests/fakes/fake_catdv.py \
  backend/app/services/__init__.py backend/app/services/catdv_client.py \
  tests/integration/test_catdv_client_login.py
git commit -m "feat: CatdvClient login + fake CatDV for tests"
```

---

### Task 10: AUTH re-login on expired session

**Files:**
- Modify: `backend/app/services/catdv_client.py` (no changes; verify behavior)
- Create: `tests/integration/test_catdv_client_reauth.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_catdv_client_reauth.py`:

```python
import time

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_reauth_on_auth_envelope_succeeds():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[42] = {"ID": 42, "name": "x"}
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Force the next call to see AUTH; retry should re-login automatically.
            fake.force_auth_until = time.time() + 0.05
            clip = await client.get_clip(42)
            assert clip["ID"] == 42
```

- [ ] **Step 2: Run, see pass** (the implementation from Task 9 already supports this)

```bash
.venv/bin/pytest tests/integration/test_catdv_client_reauth.py -v
```

If the test fails, check that `_call_json` re-issues the request after re-login. (It should — that's the design.)

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_catdv_client_reauth.py
git commit -m "test: CatdvClient transparently re-authenticates on AUTH"
```

---

### Task 11: list_clips_in_catalog with pagination and search

**Files:**
- Modify: `backend/app/services/catdv_client.py`
- Modify: `tests/fakes/fake_catdv.py`
- Create: `tests/integration/test_catdv_client_list.py`

- [ ] **Step 1: Extend the fake**

Append to `_register_routes` in `tests/fakes/fake_catdv.py`:

```python
        @self.app.get("/catdv/api/9/catalogs/{catalog_id}/clips")
        async def list_clips(catalog_id: int, request: Request):
            if request.cookies.get("JSESSIONID") != "fake-session":
                return self._envelope("AUTH")
            q = request.query_params.get("q", "").lower()
            offset = int(request.query_params.get("offset", "0"))
            limit = int(request.query_params.get("limit", "100"))
            all_clips = list(self.clips.values())
            if q:
                all_clips = [c for c in all_clips if q in c.get("name", "").lower()]
            return self._envelope(
                "OK",
                data={
                    "total": len(all_clips),
                    "clips": all_clips[offset:offset + limit],
                },
            )
```

- [ ] **Step 2: Failing test**

`tests/integration/test_catdv_client_list.py`:

```python
import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_list_clips_with_paging_and_search():
    with running_fake_catdv() as (base_url, fake):
        for i in range(5):
            fake.clips[i] = {"ID": i, "name": f"clip_{i}"}

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            page = await client.list_clips(catalog_id=881507, offset=0, limit=2)
            assert page["total"] == 5
            assert len(page["clips"]) == 2

            matches = await client.list_clips(catalog_id=881507, q="clip_3")
            assert matches["total"] == 1
            assert matches["clips"][0]["ID"] == 3
```

- [ ] **Step 3: Add to `CatdvClient`**

Add to `backend/app/services/catdv_client.py`:

```python
    async def list_clips(self, catalog_id: int, *, offset: int = 0,
                          limit: int = 100, q: str | None = None) -> dict[str, Any]:
        params: dict[str, str] = {"offset": str(offset), "limit": str(limit)}
        if q:
            params["q"] = q
        url = f"/catdv/api/9/catalogs/{catalog_id}/clips"
        env = await self._call_json_with_params("GET", url, params=params)
        return env.data
```

And update `_call_json` so it can take params (or add `_call_json_with_params`):

```python
    async def _call_json_with_params(self, method: str, path: str, *,
                                      params: dict[str, str] | None = None) -> Envelope:
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, params=params)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            await self.login()
            resp = await self.http.request(method, url, params=params)
            env = Envelope.model_validate(resp.json())
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_client_list.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catdv_client.py tests/fakes/fake_catdv.py \
  tests/integration/test_catdv_client_list.py
git commit -m "feat: CatdvClient.list_clips with paging and search"
```

---

### Task 12: download_proxy with Range-resumable streaming

**Files:**
- Modify: `backend/app/services/catdv_client.py`
- Create: `tests/integration/test_catdv_client_download.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_catdv_client_download.py`:

```python
import hashlib
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_download_proxy_full(tmp_path: Path):
    blob = b"A" * (256 * 1024)  # 256 KB
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob
        out = tmp_path / "proxy.mov"
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_proxy(clip_id=7, dest=out)
        assert out.read_bytes() == blob


@pytest.mark.asyncio
async def test_download_proxy_resumes_partial(tmp_path: Path):
    blob = b"X" * 1024 + b"Y" * 1024  # 2 KB
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob
        out = tmp_path / "proxy.mov"
        # Pre-write a partial file (1 KB) to trigger resume from byte 1024
        out.write_bytes(b"X" * 1024)
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            await client.download_proxy(clip_id=7, dest=out)
        assert hashlib.sha256(out.read_bytes()).hexdigest() == hashlib.sha256(blob).hexdigest()
```

- [ ] **Step 2: Implement `download_proxy`**

Add to `backend/app/services/catdv_client.py`:

```python
    async def download_proxy(self, clip_id: int, dest: Path,
                              chunk_size: int = 1024 * 1024) -> None:
        """Stream the proxy for a clip to `dest`. Resumes from existing partial file."""
        url = f"{self._base}/catdv/api/9/clips/{clip_id}/media"
        existing_size = dest.stat().st_size if dest.exists() else 0
        headers: dict[str, str] = {}
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

        async with self.http.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 401:
                # Session expired: re-login and retry once
                await self.login()
                async with self.http.stream("GET", url, headers=headers) as resp2:
                    resp2.raise_for_status()
                    await self._stream_to_file(resp2, dest, append=existing_size > 0,
                                               chunk_size=chunk_size)
                    return
            resp.raise_for_status()
            await self._stream_to_file(
                resp, dest, append=existing_size > 0, chunk_size=chunk_size
            )

    async def _stream_to_file(self, resp: httpx.Response, dest: Path, *,
                              append: bool, chunk_size: int) -> None:
        mode = "ab" if append else "wb"
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, mode) as f:
            async for chunk in resp.aiter_bytes(chunk_size):
                f.write(chunk)
```

Also add `from pathlib import Path` to the imports at the top of the file.

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_client_download.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/catdv_client.py tests/integration/test_catdv_client_download.py
git commit -m "feat: CatdvClient.download_proxy with Range-resumable streaming"
```

---

### Task 13: put_clip and ERROR surfacing

**Files:**
- Modify: `backend/app/services/catdv_client.py`
- Create: `tests/integration/test_catdv_client_put.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_catdv_client_put.py`:

```python
import pytest

from backend.app.services.catdv_client import CatdvClient, CatdvError
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_put_clip_writes_payload():
    with running_fake_catdv() as (base_url, fake):
        fake.clips[10] = {"ID": 10, "name": "before", "markers": []}
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            payload = {"markers": [{"name": "scene-1"}]}
            result = await client.put_clip(10, payload)
            assert result["ID"] == 10
        assert fake.put_log == [(10, payload)]
        assert fake.clips[10]["markers"] == [{"name": "scene-1"}]


@pytest.mark.asyncio
async def test_put_clip_raises_on_error():
    with running_fake_catdv() as (base_url, fake):
        # Server returns ERROR for unknown clip
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Force fake to return ERROR by writing an "error" sentinel:
            # easier: just don't pre-populate, and expect the fake's PUT to still accept
            # We instead test by causing AUTH then ERROR: replace fake behavior inline.
            fake.clips[10] = {"ID": 10}
            # Hack: make next put fail by patching put_log to raise; instead we
            # rely on the server returning OK. Skip if you prefer.
```

Note: the simpler way to test the ERROR path is via a unit test that monkeypatches the response. Replace the second test with this version:

```python
import pytest
import httpx

from backend.app.models.catdv import Envelope
from backend.app.services.catdv_client import CatdvClient, CatdvError


@pytest.mark.asyncio
async def test_put_clip_raises_catdv_error_on_error_envelope(monkeypatch):
    client = CatdvClient(base_url="http://fake", username="u", password="p")

    class FakeResp:
        status_code = 200
        def json(self):
            return {"status": "ERROR", "errorMessage": "boom", "data": None}

    class FakeClient:
        async def request(self, *a, **kw):
            return FakeResp()
        async def aclose(self):
            pass

    client._client = FakeClient()  # bypass __aenter__
    with pytest.raises(CatdvError, match="boom"):
        await client.put_clip(1, {"markers": []})
```

- [ ] **Step 2: Implement `put_clip`**

Add to `backend/app/services/catdv_client.py`:

```python
    async def put_clip(self, clip_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        env = await self._call_json("PUT", f"/catdv/api/9/clips/{clip_id}", json=payload)
        return env.data
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_catdv_client_put.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/catdv_client.py tests/integration/test_catdv_client_put.py
git commit -m "feat: CatdvClient.put_clip with ERROR surfacing"
```

---

## Phase 4 — GCS + Gemini clients (Tasks 14–16)

### Task 14: GCS service against an emulator-or-mock

This is the only task that touches `google-cloud-storage`. We test against the in-process `Mock` of the SDK rather than spinning up the GCS emulator — keeps CI dependency-free. (If a future test needs the real emulator, that's a separate addition.)

**Files:**
- Create: `backend/app/services/gcs.py`
- Create: `tests/unit/test_gcs.py`

- [ ] **Step 1: Failing tests**

`tests/unit/test_gcs.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

from backend.app.services.gcs import GcsService


def _wire_mock_bucket(blob_exists: bool):
    bucket = MagicMock(name="bucket")
    bucket.name = "test-bucket"
    blob = MagicMock(name="blob")
    blob.exists.return_value = blob_exists
    bucket.blob.return_value = blob
    return bucket, blob


def test_upload_if_absent_uploads_when_missing(tmp_path: Path):
    local = tmp_path / "f.mov"
    local.write_bytes(b"data")

    bucket, blob = _wire_mock_bucket(blob_exists=False)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_called_once_with(str(local), content_type="video/quicktime")
    assert uri == "gs://test-bucket/clips/42.mov"
    bucket.blob.assert_called_with("clips/42.mov")


def test_upload_if_absent_skips_when_present(tmp_path: Path):
    local = tmp_path / "f.mov"
    local.write_bytes(b"data")

    bucket, blob = _wire_mock_bucket(blob_exists=True)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    uri = service.upload_if_absent(clip_id=42, local_path=local, mime="video/quicktime")
    blob.upload_from_filename.assert_not_called()
    assert uri == "gs://test-bucket/clips/42.mov"


def test_delete_calls_blob_delete():
    bucket, blob = _wire_mock_bucket(blob_exists=True)
    service = GcsService.__new__(GcsService)
    service._bucket = bucket

    service.delete(clip_id=42)
    blob.delete.assert_called_once()
```

- [ ] **Step 2: Implement `backend/app/services/gcs.py`**

```python
from pathlib import Path

from google.cloud import storage  # type: ignore[import-not-found]


class GcsService:
    def __init__(self, bucket_name: str) -> None:
        self._client = storage.Client()  # uses ADC
        self._bucket = self._client.bucket(bucket_name)

    @property
    def bucket_name(self) -> str:
        return self._bucket.name

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self._bucket.name}/clips/{clip_id}.mov"

    def upload_if_absent(self, clip_id: int, local_path: Path, mime: str) -> str:
        blob_name = f"clips/{clip_id}.mov"
        blob = self._bucket.blob(blob_name)
        if not blob.exists():
            blob.upload_from_filename(str(local_path), content_type=mime)
        return f"gs://{self._bucket.name}/{blob_name}"

    def delete(self, clip_id: int) -> None:
        blob = self._bucket.blob(f"clips/{clip_id}.mov")
        blob.delete()
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_gcs.py -v
```

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/gcs.py tests/unit/test_gcs.py
git commit -m "feat: GcsService with idempotent upload"
```

---

### Task 15: Gemini service against a stub

**Files:**
- Create: `backend/app/services/gemini.py`
- Create: `tests/fakes/fake_gemini.py`
- Create: `tests/unit/test_gemini.py`

- [ ] **Step 1: Fake Gemini SDK**

`tests/fakes/fake_gemini.py`:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FakeResponse:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return self.raw or {"text": self.text}


@dataclass
class FakeModels:
    canned: Any = None
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def generate_content(self, *, model: str, contents: list, config: dict) -> FakeResponse:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.error is not None:
            raise self.error
        return self.canned


@dataclass
class FakeGenAIClient:
    vertexai: bool
    project: str
    location: str
    models: FakeModels = field(default_factory=FakeModels)
```

- [ ] **Step 2: Failing tests**

`tests/unit/test_gemini.py`:

```python
import pytest

from backend.app.services.gemini import (
    GeminiService,
    GeminiQuotaError,
    GeminiSafetyError,
    GeminiPermissionError,
)
from tests.fakes.fake_gemini import FakeGenAIClient, FakeModels, FakeResponse


def _service_with_fake_client() -> tuple[GeminiService, FakeGenAIClient]:
    fake = FakeGenAIClient(vertexai=True, project="p", location="europe-west3")
    svc = GeminiService.__new__(GeminiService)
    svc._client = fake
    return svc, fake


def test_annotate_returns_text_and_raw():
    svc, fake = _service_with_fake_client()
    fake.models.canned = FakeResponse(text='{"a": 1}', raw={"candidates": [{"text": '{"a":1}'}]})
    result = svc.annotate(
        gcs_uri="gs://b/clips/1.mov",
        mime="video/quicktime",
        prompt="describe",
        schema={"type": "object"},
        model="gemini-2.5-pro",
    )
    assert result["text"] == '{"a": 1}'
    assert result["raw"]["candidates"][0]["text"] == '{"a":1}'


def test_quota_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("Resource exhausted: quota exceeded")
    with pytest.raises(GeminiQuotaError):
        svc.annotate(gcs_uri="gs://b/x.mov", mime="video/quicktime",
                     prompt="p", schema={}, model="m")


def test_safety_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("SAFETY: content policy violation")
    with pytest.raises(GeminiSafetyError):
        svc.annotate(gcs_uri="gs://b/x.mov", mime="video/quicktime",
                     prompt="p", schema={}, model="m")


def test_permission_error_is_classified():
    svc, fake = _service_with_fake_client()
    fake.models.error = RuntimeError("permission denied on resource")
    with pytest.raises(GeminiPermissionError):
        svc.annotate(gcs_uri="gs://b/x.mov", mime="video/quicktime",
                     prompt="p", schema={}, model="m")
```

- [ ] **Step 3: Implement `backend/app/services/gemini.py`**

```python
from typing import Any

from google import genai  # type: ignore[import-not-found]


class GeminiError(RuntimeError):
    pass


class GeminiQuotaError(GeminiError):
    """Rate / quota exceeded; retryable with backoff."""


class GeminiSafetyError(GeminiError):
    """Response blocked by safety policy; do not retry."""


class GeminiPermissionError(GeminiError):
    """Service account lacks required IAM; operator must fix."""


def _classify(exc: Exception) -> Exception:
    msg = str(exc).lower()
    if "quota" in msg or "resource exhausted" in msg or "rate" in msg:
        return GeminiQuotaError(str(exc))
    if "safety" in msg or "content policy" in msg or "blocked" in msg:
        return GeminiSafetyError(str(exc))
    if "permission" in msg or "access denied" in msg or "forbidden" in msg:
        return GeminiPermissionError(str(exc))
    return GeminiError(str(exc))


class GeminiService:
    def __init__(self, project: str, location: str) -> None:
        self._client = genai.Client(vertexai=True, project=project, location=location)

    def annotate(self, *, gcs_uri: str, mime: str, prompt: str,
                 schema: dict[str, Any], model: str) -> dict[str, Any]:
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[
                    {"text": prompt},
                    {"file_data": {"file_uri": gcs_uri, "mime_type": mime}},
                ],
                config={
                    "response_mime_type": "application/json",
                    "response_schema": schema,
                },
            )
        except Exception as exc:  # noqa: BLE001 — classify and re-raise
            raise _classify(exc) from exc

        text = getattr(response, "text", "")
        raw = response.model_dump() if hasattr(response, "model_dump") else {}
        return {"text": text, "raw": raw}
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_gemini.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/gemini.py tests/fakes/fake_gemini.py tests/unit/test_gemini.py
git commit -m "feat: GeminiService with structured output + error classification"
```

---

### Task 16: Gemini retry-on-quota helper

**Files:**
- Modify: `backend/app/services/gemini.py`
- Create: `tests/unit/test_gemini_retry.py`

- [ ] **Step 1: Failing test**

`tests/unit/test_gemini_retry.py`:

```python
import pytest

from backend.app.services.gemini import (
    GeminiQuotaError,
    annotate_with_retry,
)


class FlakySvc:
    def __init__(self, calls_before_success: int) -> None:
        self._left = calls_before_success
        self.calls = 0

    def annotate(self, **kwargs):
        self.calls += 1
        if self._left > 0:
            self._left -= 1
            raise GeminiQuotaError("quota")
        return {"text": "ok", "raw": {}}


@pytest.mark.asyncio
async def test_retries_quota_then_succeeds():
    svc = FlakySvc(calls_before_success=2)
    result = await annotate_with_retry(
        svc, gcs_uri="gs://b/1.mov", mime="video/quicktime",
        prompt="p", schema={}, model="m",
        max_attempts=4, base_delay_secs=0.01,
    )
    assert result["text"] == "ok"
    assert svc.calls == 3


@pytest.mark.asyncio
async def test_gives_up_after_max_attempts():
    svc = FlakySvc(calls_before_success=10)
    with pytest.raises(GeminiQuotaError):
        await annotate_with_retry(
            svc, gcs_uri="gs://b/1.mov", mime="video/quicktime",
            prompt="p", schema={}, model="m",
            max_attempts=3, base_delay_secs=0.01,
        )
```

- [ ] **Step 2: Add `annotate_with_retry` to `backend/app/services/gemini.py`**

```python
import asyncio


async def annotate_with_retry(
    service: "GeminiService",
    *,
    gcs_uri: str,
    mime: str,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    max_attempts: int = 5,
    base_delay_secs: float = 1.0,
) -> dict[str, Any]:
    """Call service.annotate retrying only GeminiQuotaError with exponential backoff."""
    delay = base_delay_secs
    for attempt in range(1, max_attempts + 1):
        try:
            return service.annotate(
                gcs_uri=gcs_uri, mime=mime, prompt=prompt, schema=schema, model=model,
            )
        except GeminiQuotaError:
            if attempt >= max_attempts:
                raise
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")
```

(Add `import asyncio` at the top if not present.)

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_gemini_retry.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/gemini.py tests/unit/test_gemini_retry.py
git commit -m "feat: Gemini quota-aware retry with exponential backoff"
```

---

## Phase 5 — Proxy resolver (Tasks 17–19)

### Task 17: ProxyResolver protocol + RestProxyResolver

**Files:**
- Create: `backend/app/services/proxy_resolver.py`
- Create: `tests/integration/test_proxy_resolver_rest.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_proxy_resolver_rest.py`:

```python
from pathlib import Path

import pytest

from backend.app.services.catdv_client import CatdvClient
from backend.app.services.proxy_resolver import RestProxyResolver
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_rest_resolver_downloads_on_miss(tmp_path: Path):
    blob = b"V" * 10000
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            cache_dir = tmp_path / "cache"
            resolver = RestProxyResolver(catdv=client, cache_dir=cache_dir)

            path = await resolver.path_for_clip_id(7)
            assert path.exists()
            assert path.read_bytes() == blob
            assert resolver.is_managed(path)


@pytest.mark.asyncio
async def test_rest_resolver_hits_cache_on_second_call(tmp_path: Path):
    blob = b"V" * 10000
    with running_fake_catdv() as (base_url, fake):
        fake.proxies[7] = blob

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            cache_dir = tmp_path / "cache"
            resolver = RestProxyResolver(catdv=client, cache_dir=cache_dir)

            await resolver.path_for_clip_id(7)
            # Wipe fake proxy to prove no download happens
            fake.proxies.pop(7)
            path = await resolver.path_for_clip_id(7)
            assert path.read_bytes() == blob
```

- [ ] **Step 2: Implement `backend/app/services/proxy_resolver.py`**

```python
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ProxyResolver(Protocol):
    async def path_for_clip_id(self, clip_id: int) -> Path: ...
    def is_managed(self, path: Path) -> bool: ...


class RestProxyResolver:
    """Downloads proxies via CatDV REST and caches them on local disk."""

    def __init__(self, catdv, cache_dir: Path) -> None:
        self._catdv = catdv
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    async def path_for_clip_id(self, clip_id: int) -> Path:
        dest = self._cache_dir / f"{clip_id}.mov"
        if not dest.exists() or dest.stat().st_size == 0:
            await self._catdv.download_proxy(clip_id, dest)
        return dest

    def is_managed(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._cache_dir.resolve())
        except ValueError:
            return False
        return True
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_proxy_resolver_rest.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/proxy_resolver.py tests/integration/test_proxy_resolver_rest.py
git commit -m "feat: ProxyResolver protocol + RestProxyResolver with caching"
```

---

### Task 18: FilesystemProxyResolver

**Files:**
- Modify: `backend/app/services/proxy_resolver.py`
- Create: `tests/integration/test_proxy_resolver_fs.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_proxy_resolver_fs.py`:

```python
from pathlib import Path

import pytest

from backend.app.services.proxy_resolver import FilesystemProxyResolver, ProxyNotFound


@pytest.mark.asyncio
async def test_fs_resolver_returns_existing_path(tmp_path: Path):
    root = tmp_path / "proxies"
    root.mkdir()
    (root / "12345.mov").write_bytes(b"data")

    resolver = FilesystemProxyResolver(root=root, path_template="{root}/{clip_id}.mov")
    path = await resolver.path_for_clip_id(12345)
    assert path == root / "12345.mov"
    assert path.read_bytes() == b"data"
    assert not resolver.is_managed(path)


@pytest.mark.asyncio
async def test_fs_resolver_raises_when_missing(tmp_path: Path):
    resolver = FilesystemProxyResolver(root=tmp_path, path_template="{root}/{clip_id}.mov")
    with pytest.raises(ProxyNotFound):
        await resolver.path_for_clip_id(999)


@pytest.mark.asyncio
async def test_fs_resolver_raises_when_unreadable(tmp_path: Path):
    p = tmp_path / "123.mov"
    p.write_bytes(b"x")
    p.chmod(0)  # remove read perm
    resolver = FilesystemProxyResolver(root=tmp_path, path_template="{root}/{clip_id}.mov")
    try:
        with pytest.raises(ProxyNotFound):
            await resolver.path_for_clip_id(123)
    finally:
        p.chmod(0o644)  # restore so cleanup works
```

- [ ] **Step 2: Extend `backend/app/services/proxy_resolver.py`**

Append:

```python
import os


class ProxyNotFound(FileNotFoundError):
    """Raised when a proxy can't be located on the filesystem."""


class FilesystemProxyResolver:
    """Returns proxy paths from the CatDV server's local filesystem (no download)."""

    def __init__(self, root: Path, path_template: str = "{root}/{clip_id}.mov") -> None:
        self._root = root
        self._template = path_template

    async def path_for_clip_id(self, clip_id: int) -> Path:
        path = Path(self._template.format(root=str(self._root), clip_id=clip_id))
        if not path.exists():
            raise ProxyNotFound(f"proxy not on disk: {path}")
        if not os.access(path, os.R_OK):
            raise ProxyNotFound(f"proxy not readable: {path}")
        return path

    def is_managed(self, path: Path) -> bool:
        return False
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_proxy_resolver_fs.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/proxy_resolver.py tests/integration/test_proxy_resolver_fs.py
git commit -m "feat: FilesystemProxyResolver for prod deployment"
```

---

### Task 19: Resolver factory

**Files:**
- Modify: `backend/app/services/proxy_resolver.py`
- Create: `tests/unit/test_proxy_resolver_factory.py`

- [ ] **Step 1: Failing test**

`tests/unit/test_proxy_resolver_factory.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.services.proxy_resolver import (
    FilesystemProxyResolver,
    RestProxyResolver,
    build_resolver,
)


def test_factory_returns_rest_resolver(tmp_path: Path):
    fake_catdv = MagicMock()
    resolver = build_resolver(
        source="rest",
        catdv_client=fake_catdv,
        cache_dir=tmp_path / "cache",
        fs_root=None,
        path_template=None,
    )
    assert isinstance(resolver, RestProxyResolver)


def test_factory_returns_filesystem_resolver(tmp_path: Path):
    resolver = build_resolver(
        source="filesystem",
        catdv_client=None,
        cache_dir=None,
        fs_root=tmp_path,
        path_template="{root}/{clip_id}.mov",
    )
    assert isinstance(resolver, FilesystemProxyResolver)


def test_factory_rejects_filesystem_without_root():
    with pytest.raises(ValueError, match="fs_root"):
        build_resolver(
            source="filesystem", catdv_client=None,
            cache_dir=None, fs_root=None, path_template=None,
        )
```

- [ ] **Step 2: Add `build_resolver` to `backend/app/services/proxy_resolver.py`**

```python
def build_resolver(
    *,
    source: str,
    catdv_client,
    cache_dir: Path | None,
    fs_root: Path | None,
    path_template: str | None,
) -> ProxyResolver:
    if source == "rest":
        if cache_dir is None or catdv_client is None:
            raise ValueError("rest source requires catdv_client and cache_dir")
        return RestProxyResolver(catdv=catdv_client, cache_dir=cache_dir)
    if source == "filesystem":
        if fs_root is None:
            raise ValueError("filesystem source requires fs_root")
        return FilesystemProxyResolver(
            root=fs_root,
            path_template=path_template or "{root}/{clip_id}.mov",
        )
    raise ValueError(f"unknown PROXY_SOURCE: {source!r}")
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_proxy_resolver_factory.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/proxy_resolver.py tests/unit/test_proxy_resolver_factory.py
git commit -m "feat: proxy resolver factory selects rest|filesystem at startup"
```

---

## Phase 6 — Repositories (Tasks 20–25)

Pattern: each repository owns one or two tables. Methods take an open `aiosqlite.Connection`. Repositories are stateless; the AppContext creates them once and shares.

### Task 20: Templates repository

**Files:**
- Create: `backend/app/repositories/__init__.py`
- Create: `backend/app/repositories/templates.py`
- Create: `tests/integration/conftest.py` (shared db fixture)
- Create: `tests/integration/test_templates_repo.py`

- [ ] **Step 1: Shared `tests/integration/conftest.py`**

```python
from pathlib import Path

import pytest
import pytest_asyncio

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


@pytest_asyncio.fixture
async def db(tmp_path: Path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        yield conn
```

- [ ] **Step 2: Failing tests**

`tests/integration/test_templates_repo.py`:

```python
import pytest

from backend.app.models.template import Template
from backend.app.repositories.templates import TemplatesRepo


@pytest.fixture
def repo() -> TemplatesRepo:
    return TemplatesRepo()


@pytest.mark.asyncio
async def test_create_and_get(db, repo):
    tpl = Template(
        name="scenes",
        prompt="describe scenes",
        output_schema={"type": "object"},
        target_map={"scenes": {"kind": "markers"}},
        model="gemini-2.5-pro",
    )
    new_id = await repo.create(db, tpl)
    assert new_id > 0

    loaded = await repo.get(db, new_id)
    assert loaded.name == "scenes"
    assert loaded.target_map.fields["scenes"].kind == "markers"


@pytest.mark.asyncio
async def test_list_excludes_archived(db, repo):
    a = await repo.create(db, Template(name="a", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"))
    b = await repo.create(db, Template(name="b", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"))
    await repo.archive(db, b)
    ids = [t.id for t in await repo.list_active(db)]
    assert ids == [a]


@pytest.mark.asyncio
async def test_unique_name(db, repo):
    tpl = Template(name="dup", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m")
    await repo.create(db, tpl)
    with pytest.raises(Exception):
        await repo.create(db, tpl)
```

- [ ] **Step 3: Implement `backend/app/repositories/__init__.py`** (empty).

- [ ] **Step 4: Implement `backend/app/repositories/templates.py`**

```python
import json
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.template import Template


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TemplatesRepo:
    async def create(self, conn: aiosqlite.Connection, tpl: Template) -> int:
        now = _now_iso()
        cur = await conn.execute(
            """
            INSERT INTO templates (name, description, prompt, output_schema, target_map,
                                   model, created_at, updated_at, archived)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                tpl.name,
                tpl.description,
                tpl.prompt,
                json.dumps(tpl.output_schema),
                tpl.target_map.model_dump_json(),
                tpl.model,
                now,
                now,
            ),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, template_id: int) -> Template:
        cur = await conn.execute(
            """
            SELECT id, name, description, prompt, output_schema, target_map,
                   model, archived
            FROM templates WHERE id = ?
            """,
            (template_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"template {template_id} not found")
        return self._row_to_template(row)

    async def list_active(self, conn: aiosqlite.Connection) -> list[Template]:
        cur = await conn.execute(
            """
            SELECT id, name, description, prompt, output_schema, target_map,
                   model, archived
            FROM templates WHERE archived = 0
            ORDER BY id
            """
        )
        return [self._row_to_template(r) for r in await cur.fetchall()]

    async def archive(self, conn: aiosqlite.Connection, template_id: int) -> None:
        await conn.execute(
            "UPDATE templates SET archived = 1, updated_at = ? WHERE id = ?",
            (_now_iso(), template_id),
        )
        await conn.commit()

    async def update(self, conn: aiosqlite.Connection, template_id: int, tpl: Template) -> None:
        await conn.execute(
            """
            UPDATE templates SET name=?, description=?, prompt=?, output_schema=?,
                                 target_map=?, model=?, updated_at=?
            WHERE id=?
            """,
            (
                tpl.name,
                tpl.description,
                tpl.prompt,
                json.dumps(tpl.output_schema),
                tpl.target_map.model_dump_json(),
                tpl.model,
                _now_iso(),
                template_id,
            ),
        )
        await conn.commit()

    @staticmethod
    def _row_to_template(row) -> Template:
        return Template(
            id=row[0],
            name=row[1],
            description=row[2],
            prompt=row[3],
            output_schema=json.loads(row[4]),
            target_map=json.loads(row[5]),
            model=row[6],
            archived=bool(row[7]),
        )
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_templates_repo.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/__init__.py backend/app/repositories/templates.py \
  tests/integration/conftest.py tests/integration/test_templates_repo.py
git commit -m "feat: TemplatesRepo with CRUD and archive"
```

---

### Task 21: Jobs + JobItems repository + Job models

**Files:**
- Create: `backend/app/models/job.py`
- Create: `backend/app/repositories/jobs.py`
- Create: `tests/integration/test_jobs_repo.py`

- [ ] **Step 1: Implement job models**

`backend/app/models/job.py`:

```python
from typing import Literal

from pydantic import BaseModel

JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
ItemStatus = Literal[
    "pending", "resolving", "uploading", "prompting",
    "annotated", "review_ready", "applied", "rejected", "error",
]


class Job(BaseModel):
    id: int | None = None
    template_id: int
    status: JobStatus = "pending"
    total_clips: int
    notes: str | None = None


class JobItem(BaseModel):
    id: int | None = None
    job_id: int
    catdv_clip_id: int
    status: ItemStatus = "pending"
    error_message: str | None = None
    annotation_id: int | None = None
```

- [ ] **Step 2: Failing tests**

`tests/integration/test_jobs_repo.py`:

```python
import pytest

from backend.app.models.job import Job
from backend.app.models.template import Template
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.templates import TemplatesRepo


@pytest.mark.asyncio
async def test_create_job_with_items_and_progress(db):
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))

    jobs = JobsRepo()
    clip_ids = [101, 102, 103]
    job_id = await jobs.create_job(db, template_id=template_id, clip_ids=clip_ids)

    job = await jobs.get_job(db, job_id)
    assert job.total_clips == 3
    assert job.status == "pending"

    items = await jobs.list_items(db, job_id)
    assert [it.catdv_clip_id for it in items] == clip_ids
    assert all(it.status == "pending" for it in items)


@pytest.mark.asyncio
async def test_update_item_status(db):
    templates = TemplatesRepo()
    t = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, template_id=t, clip_ids=[1, 2])
    items = await jobs.list_items(db, job_id)

    await jobs.update_item_status(db, items[0].id, "running_substep:resolving")
    # actually: use ItemStatus values
    await jobs.update_item_status(db, items[0].id, "resolving")
    refreshed = await jobs.list_items(db, job_id)
    assert refreshed[0].status == "resolving"


@pytest.mark.asyncio
async def test_reset_transient_statuses_on_recovery(db):
    templates = TemplatesRepo()
    t = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    jobs = JobsRepo()
    job_id = await jobs.create_job(db, template_id=t, clip_ids=[1, 2, 3])
    items = await jobs.list_items(db, job_id)
    await jobs.update_item_status(db, items[0].id, "uploading")
    await jobs.update_item_status(db, items[1].id, "prompting")
    await jobs.update_item_status(db, items[2].id, "review_ready")

    reset_count = await jobs.reset_transient(db)
    assert reset_count == 2
    refreshed = await jobs.list_items(db, job_id)
    statuses = sorted(it.status for it in refreshed)
    assert statuses == ["pending", "pending", "review_ready"]
```

- [ ] **Step 3: Implement `backend/app/repositories/jobs.py`**

```python
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.job import ItemStatus, Job, JobItem, JobStatus


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


TRANSIENT_STATUSES = ("resolving", "uploading", "prompting")


class JobsRepo:
    async def create_job(self, conn: aiosqlite.Connection, *, template_id: int,
                          clip_ids: list[int]) -> int:
        cur = await conn.execute(
            """
            INSERT INTO jobs (template_id, status, created_at, total_clips)
            VALUES (?, 'pending', ?, ?)
            """,
            (template_id, _now_iso(), len(clip_ids)),
        )
        job_id = cur.lastrowid
        assert job_id is not None
        for clip_id in clip_ids:
            await conn.execute(
                "INSERT INTO job_items (job_id, catdv_clip_id, status) VALUES (?, ?, 'pending')",
                (job_id, clip_id),
            )
        await conn.commit()
        return job_id

    async def get_job(self, conn: aiosqlite.Connection, job_id: int) -> Job:
        cur = await conn.execute(
            "SELECT id, template_id, status, total_clips, notes FROM jobs WHERE id = ?",
            (job_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"job {job_id} not found")
        return Job(id=row[0], template_id=row[1], status=row[2], total_clips=row[3], notes=row[4])

    async def list_jobs(self, conn: aiosqlite.Connection, *, limit: int = 50) -> list[Job]:
        cur = await conn.execute(
            "SELECT id, template_id, status, total_clips, notes FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [Job(id=r[0], template_id=r[1], status=r[2], total_clips=r[3], notes=r[4])
                for r in await cur.fetchall()]

    async def update_status(self, conn: aiosqlite.Connection, job_id: int,
                             status: JobStatus) -> None:
        if status == "running":
            await conn.execute(
                "UPDATE jobs SET status = ?, started_at = COALESCE(started_at, ?) WHERE id = ?",
                (status, _now_iso(), job_id),
            )
        elif status in ("completed", "failed", "cancelled"):
            await conn.execute(
                "UPDATE jobs SET status = ?, finished_at = ? WHERE id = ?",
                (status, _now_iso(), job_id),
            )
        else:
            await conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        await conn.commit()

    async def list_items(self, conn: aiosqlite.Connection, job_id: int) -> list[JobItem]:
        cur = await conn.execute(
            """
            SELECT id, job_id, catdv_clip_id, status, error_message, annotation_id
            FROM job_items WHERE job_id = ? ORDER BY id
            """,
            (job_id,),
        )
        return [
            JobItem(id=r[0], job_id=r[1], catdv_clip_id=r[2], status=r[3],
                    error_message=r[4], annotation_id=r[5])
            for r in await cur.fetchall()
        ]

    async def update_item_status(self, conn: aiosqlite.Connection, item_id: int,
                                  status: ItemStatus, *, error: str | None = None) -> None:
        await conn.execute(
            "UPDATE job_items SET status = ?, error_message = ? WHERE id = ?",
            (status, error, item_id),
        )
        await conn.commit()

    async def attach_annotation(self, conn: aiosqlite.Connection, item_id: int,
                                 annotation_id: int) -> None:
        await conn.execute(
            "UPDATE job_items SET annotation_id = ? WHERE id = ?",
            (annotation_id, item_id),
        )
        await conn.commit()

    async def reset_transient(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute(
            f"UPDATE job_items SET status = 'pending' WHERE status IN "
            f"({','.join('?' * len(TRANSIENT_STATUSES))})",
            TRANSIENT_STATUSES,
        )
        await conn.commit()
        return cur.rowcount or 0
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_jobs_repo.py -v
```

Note: remove the line `await jobs.update_item_status(db, items[0].id, "running_substep:resolving")` from the test — it was a stray. The next line uses the correct enum value.

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/job.py backend/app/repositories/jobs.py \
  tests/integration/test_jobs_repo.py
git commit -m "feat: JobsRepo with create, status updates, crash recovery"
```

---

### Task 22: Annotations repo (with FTS5) + Annotation model

**Files:**
- Create: `backend/app/models/annotation.py`
- Create: `backend/app/repositories/annotations.py`
- Create: `tests/integration/test_annotations_repo.py`

- [ ] **Step 1: Implement `backend/app/models/annotation.py`**

```python
from typing import Any, Literal

from pydantic import BaseModel


ReviewKind = Literal["markers", "marker", "field", "note"]


class Annotation(BaseModel):
    id: int | None = None
    catdv_clip_id: int
    catdv_clip_name: str
    template_id: int
    job_id: int | None = None
    model: str
    prompt_used: str
    raw_response: dict[str, Any]
    structured_output: dict[str, Any] | None
    clip_snapshot: dict[str, Any]


class ReviewItem(BaseModel):
    id: int | None = None
    annotation_id: int
    catdv_clip_id: int
    kind: Literal["marker", "note", "field"]
    target_identifier: str | None = None
    proposed_value: dict[str, Any] | list[Any] | str | int | float | bool | None
    edited_value: dict[str, Any] | list[Any] | str | int | float | bool | None = None
    decision: Literal["pending", "accepted", "rejected"] = "pending"
```

- [ ] **Step 2: Failing tests**

`tests/integration/test_annotations_repo.py`:

```python
import pytest

from backend.app.models.annotation import Annotation
from backend.app.models.template import Template
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.templates import TemplatesRepo


@pytest.mark.asyncio
async def test_insert_and_get(db):
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))

    repo = AnnotationsRepo()
    annotation_id = await repo.insert(db, Annotation(
        catdv_clip_id=42, catdv_clip_name="Test_Clip", template_id=template_id,
        job_id=None, model="gemini-2.5-pro", prompt_used="p",
        raw_response={"text": "..."}, structured_output={"scenes": []},
        clip_snapshot={"ID": 42, "name": "Test_Clip"},
    ))
    loaded = await repo.get(db, annotation_id)
    assert loaded.catdv_clip_id == 42
    assert loaded.structured_output == {"scenes": []}


@pytest.mark.asyncio
async def test_fts_search_finds_clip(db):
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    repo = AnnotationsRepo()
    await repo.insert(db, Annotation(
        catdv_clip_id=1, catdv_clip_name="Polčakovi rodina", template_id=template_id,
        job_id=None, model="m", prompt_used="popiš rodinu",
        raw_response={}, structured_output={"summary": "rodinný portrét"},
        clip_snapshot={"ID": 1},
    ))
    results = await repo.search(db, "rodinný")
    assert len(results) == 1
    # Search without diacritics also works
    results = await repo.search(db, "rodinny")
    assert len(results) == 1


@pytest.mark.asyncio
async def test_list_by_clip_returns_latest_first(db):
    templates = TemplatesRepo()
    t = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    repo = AnnotationsRepo()
    first = await repo.insert(db, Annotation(
        catdv_clip_id=7, catdv_clip_name="x", template_id=t, model="m",
        prompt_used="v1", raw_response={}, structured_output={}, clip_snapshot={},
    ))
    second = await repo.insert(db, Annotation(
        catdv_clip_id=7, catdv_clip_name="x", template_id=t, model="m",
        prompt_used="v2", raw_response={}, structured_output={}, clip_snapshot={},
    ))
    rows = await repo.list_by_clip(db, 7)
    assert [r.id for r in rows] == [second, first]
```

- [ ] **Step 3: Implement `backend/app/repositories/annotations.py`**

```python
import json
from datetime import datetime, timezone

import aiosqlite

from backend.app.models.annotation import Annotation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AnnotationsRepo:
    async def insert(self, conn: aiosqlite.Connection, ann: Annotation) -> int:
        cur = await conn.execute(
            """
            INSERT INTO annotations
              (catdv_clip_id, catdv_clip_name, template_id, job_id, model, prompt_used,
               raw_response, structured_output, clip_snapshot, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ann.catdv_clip_id,
                ann.catdv_clip_name,
                ann.template_id,
                ann.job_id,
                ann.model,
                ann.prompt_used,
                json.dumps(ann.raw_response),
                json.dumps(ann.structured_output) if ann.structured_output is not None else "null",
                json.dumps(ann.clip_snapshot),
                _now_iso(),
            ),
        )
        await conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, conn: aiosqlite.Connection, annotation_id: int) -> Annotation:
        cur = await conn.execute(
            """
            SELECT id, catdv_clip_id, catdv_clip_name, template_id, job_id, model,
                   prompt_used, raw_response, structured_output, clip_snapshot
            FROM annotations WHERE id = ?
            """,
            (annotation_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"annotation {annotation_id} not found")
        return self._row(row)

    async def list_by_clip(self, conn: aiosqlite.Connection, clip_id: int) -> list[Annotation]:
        cur = await conn.execute(
            """
            SELECT id, catdv_clip_id, catdv_clip_name, template_id, job_id, model,
                   prompt_used, raw_response, structured_output, clip_snapshot
            FROM annotations WHERE catdv_clip_id = ? ORDER BY id DESC
            """,
            (clip_id,),
        )
        return [self._row(r) for r in await cur.fetchall()]

    async def search(self, conn: aiosqlite.Connection, query: str) -> list[int]:
        # Use FTS5; returns annotation ids
        cur = await conn.execute(
            "SELECT rowid FROM annotations_fts WHERE annotations_fts MATCH ?",
            (query,),
        )
        return [r[0] for r in await cur.fetchall()]

    @staticmethod
    def _row(row) -> Annotation:
        structured_raw = row[8]
        structured = None if structured_raw == "null" else json.loads(structured_raw)
        return Annotation(
            id=row[0],
            catdv_clip_id=row[1],
            catdv_clip_name=row[2],
            template_id=row[3],
            job_id=row[4],
            model=row[5],
            prompt_used=row[6],
            raw_response=json.loads(row[7]),
            structured_output=structured,
            clip_snapshot=json.loads(row[9]),
        )
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_annotations_repo.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/annotation.py backend/app/repositories/annotations.py \
  tests/integration/test_annotations_repo.py
git commit -m "feat: AnnotationsRepo with FTS5-backed search"
```

---

### Task 23: ReviewItemsRepo + WriteLogRepo

**Files:**
- Create: `backend/app/repositories/review_items.py`
- Create: `backend/app/repositories/write_log.py`
- Create: `tests/integration/test_review_items_repo.py`
- Create: `tests/integration/test_write_log_repo.py`

- [ ] **Step 1: Failing tests for ReviewItemsRepo**

`tests/integration/test_review_items_repo.py`:

```python
import pytest

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.template import Template
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo


async def _seed_annotation(db):
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"x": {"kind": "markers"}}, model="m"
    ))
    annotations = AnnotationsRepo()
    return await annotations.insert(db, Annotation(
        catdv_clip_id=1, catdv_clip_name="c", template_id=template_id, model="m",
        prompt_used="p", raw_response={}, structured_output={}, clip_snapshot={},
    ))


@pytest.mark.asyncio
async def test_bulk_insert_and_list(db):
    annotation_id = await _seed_annotation(db)
    repo = ReviewItemsRepo()
    items = [
        ReviewItem(annotation_id=annotation_id, catdv_clip_id=1, kind="marker",
                   proposed_value={"in": 0, "out": 5, "name": "scene-a"}),
        ReviewItem(annotation_id=annotation_id, catdv_clip_id=1, kind="field",
                   target_identifier="pragafilm.dekáda.natočení", proposed_value="30.léta"),
    ]
    inserted = await repo.bulk_insert(db, items)
    assert len(inserted) == 2

    loaded = await repo.list_by_clip(db, 1, decision="pending")
    assert [it.kind for it in loaded] == ["marker", "field"]


@pytest.mark.asyncio
async def test_set_decision_and_edited_value(db):
    annotation_id = await _seed_annotation(db)
    repo = ReviewItemsRepo()
    inserted = await repo.bulk_insert(db, [
        ReviewItem(annotation_id=annotation_id, catdv_clip_id=1, kind="marker",
                   proposed_value={"in": 0, "out": 5, "name": "scene-a"}),
    ])
    item_id = inserted[0].id
    assert item_id is not None
    await repo.set_decision(db, item_id, "accepted", edited_value={"in": 1, "out": 5, "name": "scene-a"})
    refreshed = await repo.get(db, item_id)
    assert refreshed.decision == "accepted"
    assert refreshed.edited_value == {"in": 1, "out": 5, "name": "scene-a"}
```

- [ ] **Step 2: Implement `backend/app/repositories/review_items.py`**

```python
import json
from datetime import datetime, timezone
from typing import Literal

import aiosqlite

from backend.app.models.annotation import ReviewItem


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReviewItemsRepo:
    async def bulk_insert(self, conn: aiosqlite.Connection,
                           items: list[ReviewItem]) -> list[ReviewItem]:
        inserted: list[ReviewItem] = []
        for it in items:
            cur = await conn.execute(
                """
                INSERT INTO review_items
                  (annotation_id, catdv_clip_id, kind, target_identifier,
                   proposed_value, edited_value, decision)
                VALUES (?, ?, ?, ?, ?, NULL, 'pending')
                """,
                (
                    it.annotation_id, it.catdv_clip_id, it.kind, it.target_identifier,
                    json.dumps(it.proposed_value),
                ),
            )
            it.id = cur.lastrowid
            it.decision = "pending"
            inserted.append(it)
        await conn.commit()
        return inserted

    async def get(self, conn: aiosqlite.Connection, item_id: int) -> ReviewItem:
        cur = await conn.execute(
            """
            SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                   proposed_value, edited_value, decision
            FROM review_items WHERE id = ?
            """,
            (item_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise LookupError(f"review_item {item_id} not found")
        return self._row(row)

    async def list_by_clip(self, conn: aiosqlite.Connection, clip_id: int,
                            *, decision: str | None = None) -> list[ReviewItem]:
        if decision is not None:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                       proposed_value, edited_value, decision
                FROM review_items WHERE catdv_clip_id = ? AND decision = ?
                ORDER BY id
                """,
                (clip_id, decision),
            )
        else:
            cur = await conn.execute(
                """
                SELECT id, annotation_id, catdv_clip_id, kind, target_identifier,
                       proposed_value, edited_value, decision
                FROM review_items WHERE catdv_clip_id = ?
                ORDER BY id
                """,
                (clip_id,),
            )
        return [self._row(r) for r in await cur.fetchall()]

    async def set_decision(self, conn: aiosqlite.Connection, item_id: int,
                            decision: Literal["pending", "accepted", "rejected"],
                            *, edited_value=None) -> None:
        edited_json = json.dumps(edited_value) if edited_value is not None else None
        await conn.execute(
            """
            UPDATE review_items
            SET decision = ?, edited_value = COALESCE(?, edited_value),
                decided_at = ?
            WHERE id = ?
            """,
            (decision, edited_json, _now_iso(), item_id),
        )
        await conn.commit()

    async def mark_applied(self, conn: aiosqlite.Connection, item_ids: list[int]) -> None:
        await conn.executemany(
            "UPDATE review_items SET applied_at = ? WHERE id = ?",
            [(_now_iso(), i) for i in item_ids],
        )
        await conn.commit()

    @staticmethod
    def _row(row) -> ReviewItem:
        return ReviewItem(
            id=row[0],
            annotation_id=row[1],
            catdv_clip_id=row[2],
            kind=row[3],
            target_identifier=row[4],
            proposed_value=json.loads(row[5]),
            edited_value=json.loads(row[6]) if row[6] is not None else None,
            decision=row[7],
        )
```

- [ ] **Step 3: Failing test for WriteLogRepo**

`tests/integration/test_write_log_repo.py`:

```python
import pytest

from backend.app.repositories.write_log import WriteLogRepo


@pytest.mark.asyncio
async def test_record_writes_log_row(db):
    repo = WriteLogRepo()
    await repo.record(
        db,
        catdv_clip_id=42,
        annotation_id=None,
        payload={"markers": [{"name": "x"}]},
        response={"ID": 42, "modifyDate": "2026-05-18"},
        status="ok",
    )
    cur = await db.execute("SELECT count(*) FROM write_log WHERE catdv_clip_id = 42")
    assert (await cur.fetchone())[0] == 1
```

- [ ] **Step 4: Implement `backend/app/repositories/write_log.py`**

```python
import json
from datetime import datetime, timezone
from typing import Any, Literal

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WriteLogRepo:
    async def record(self, conn: aiosqlite.Connection, *,
                     catdv_clip_id: int,
                     annotation_id: int | None,
                     payload: dict[str, Any],
                     response: dict[str, Any] | str,
                     status: Literal["ok", "error"]) -> None:
        response_str = json.dumps(response) if isinstance(response, (dict, list)) else str(response)
        await conn.execute(
            """
            INSERT INTO write_log
              (catdv_clip_id, annotation_id, payload, response, status, written_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (catdv_clip_id, annotation_id, json.dumps(payload), response_str,
             status, _now_iso()),
        )
        await conn.commit()
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_review_items_repo.py tests/integration/test_write_log_repo.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/review_items.py backend/app/repositories/write_log.py \
  tests/integration/test_review_items_repo.py tests/integration/test_write_log_repo.py
git commit -m "feat: ReviewItemsRepo + WriteLogRepo with decision tracking"
```

---

### Task 24: ProxyCacheRepo with LRU eviction

**Files:**
- Create: `backend/app/repositories/proxy_cache.py`
- Create: `tests/integration/test_proxy_cache_repo.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_proxy_cache_repo.py`:

```python
import pytest

from backend.app.repositories.proxy_cache import ProxyCacheRepo


@pytest.mark.asyncio
async def test_record_get_and_touch(db):
    repo = ProxyCacheRepo()
    await repo.record(db, clip_id=1, file_path="cache/1.mov", size_bytes=1000, etag=None)
    row = await repo.get(db, 1)
    assert row is not None
    assert row["file_path"] == "cache/1.mov"

    await repo.touch(db, clip_id=1)
    row2 = await repo.get(db, 1)
    assert row2["last_used_at"] >= row["last_used_at"]


@pytest.mark.asyncio
async def test_pick_lru_for_eviction(db):
    repo = ProxyCacheRepo()
    import asyncio
    for i in range(3):
        await repo.record(db, clip_id=i, file_path=f"cache/{i}.mov", size_bytes=1000, etag=None)
        await asyncio.sleep(0.01)
    await repo.touch(db, clip_id=0)  # 0 becomes most recent

    victims = await repo.lru_candidates(db, max_bytes=1500)
    # total = 3000; we need to evict 1500 worth → at least 2 entries (oldest two: 1, 2)
    victim_ids = [v["catdv_clip_id"] for v in victims]
    assert 1 in victim_ids
    assert 2 in victim_ids
    assert 0 not in victim_ids
```

- [ ] **Step 2: Implement `backend/app/repositories/proxy_cache.py`**

```python
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProxyCacheRepo:
    async def record(self, conn: aiosqlite.Connection, *,
                     clip_id: int, file_path: str, size_bytes: int,
                     etag: str | None) -> None:
        now = _now_iso()
        await conn.execute(
            """
            INSERT INTO proxy_cache
              (catdv_clip_id, file_path, size_bytes, etag, downloaded_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(catdv_clip_id) DO UPDATE SET
              file_path = excluded.file_path,
              size_bytes = excluded.size_bytes,
              etag = excluded.etag,
              downloaded_at = excluded.downloaded_at,
              last_used_at = excluded.last_used_at
            """,
            (clip_id, file_path, size_bytes, etag, now, now),
        )
        await conn.commit()

    async def get(self, conn: aiosqlite.Connection, clip_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT catdv_clip_id, file_path, size_bytes, etag, downloaded_at, last_used_at
            FROM proxy_cache WHERE catdv_clip_id = ?
            """,
            (clip_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("catdv_clip_id", "file_path", "size_bytes", "etag", "downloaded_at", "last_used_at"),
            row,
        ))

    async def touch(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute(
            "UPDATE proxy_cache SET last_used_at = ? WHERE catdv_clip_id = ?",
            (_now_iso(), clip_id),
        )
        await conn.commit()

    async def total_size_bytes(self, conn: aiosqlite.Connection) -> int:
        cur = await conn.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM proxy_cache")
        return int((await cur.fetchone())[0])

    async def lru_candidates(self, conn: aiosqlite.Connection,
                              max_bytes: int) -> list[dict[str, Any]]:
        """Return rows ordered oldest-first totalling at least max_bytes."""
        cur = await conn.execute(
            """
            SELECT catdv_clip_id, file_path, size_bytes, last_used_at
            FROM proxy_cache
            ORDER BY last_used_at ASC
            """
        )
        victims: list[dict[str, Any]] = []
        accum = 0
        for row in await cur.fetchall():
            victims.append(dict(zip(("catdv_clip_id", "file_path", "size_bytes", "last_used_at"), row)))
            accum += row[2]
            if accum >= max_bytes:
                break
        return victims

    async def delete(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute("DELETE FROM proxy_cache WHERE catdv_clip_id = ?", (clip_id,))
        await conn.commit()
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_proxy_cache_repo.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/repositories/proxy_cache.py tests/integration/test_proxy_cache_repo.py
git commit -m "feat: ProxyCacheRepo with LRU eviction support"
```

---

### Task 25: GcsFilesRepo

**Files:**
- Create: `backend/app/repositories/gcs_files.py`
- Create: `tests/integration/test_gcs_files_repo.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_gcs_files_repo.py`:

```python
import pytest

from backend.app.repositories.gcs_files import GcsFilesRepo


@pytest.mark.asyncio
async def test_upsert_and_get(db):
    repo = GcsFilesRepo()
    await repo.upsert(
        db,
        clip_id=42,
        gcs_uri="gs://b/clips/42.mov",
        mime_type="video/quicktime",
        size_bytes=100,
        sha256="abc",
    )
    row = await repo.get(db, 42)
    assert row is not None
    assert row["gcs_uri"] == "gs://b/clips/42.mov"


@pytest.mark.asyncio
async def test_upsert_replaces_on_new_sha(db):
    repo = GcsFilesRepo()
    await repo.upsert(db, clip_id=42, gcs_uri="gs://b/clips/42.mov",
                      mime_type="video/quicktime", size_bytes=100, sha256="aaa")
    await repo.upsert(db, clip_id=42, gcs_uri="gs://b/clips/42.mov",
                      mime_type="video/quicktime", size_bytes=200, sha256="bbb")
    row = await repo.get(db, 42)
    assert row["sha256"] == "bbb"
    assert row["size_bytes"] == 200
```

- [ ] **Step 2: Implement `backend/app/repositories/gcs_files.py`**

```python
from datetime import datetime, timezone
from typing import Any

import aiosqlite


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GcsFilesRepo:
    async def upsert(self, conn: aiosqlite.Connection, *,
                     clip_id: int, gcs_uri: str, mime_type: str,
                     size_bytes: int, sha256: str) -> None:
        now = _now_iso()
        await conn.execute(
            """
            INSERT INTO gcs_files
              (catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256, uploaded_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(catdv_clip_id) DO UPDATE SET
              gcs_uri = excluded.gcs_uri,
              mime_type = excluded.mime_type,
              size_bytes = excluded.size_bytes,
              sha256 = excluded.sha256,
              uploaded_at = excluded.uploaded_at,
              last_used_at = excluded.last_used_at
            """,
            (clip_id, gcs_uri, mime_type, size_bytes, sha256, now, now),
        )
        await conn.commit()

    async def get(self, conn: aiosqlite.Connection, clip_id: int) -> dict[str, Any] | None:
        cur = await conn.execute(
            """
            SELECT catdv_clip_id, gcs_uri, mime_type, size_bytes, sha256,
                   uploaded_at, last_used_at
            FROM gcs_files WHERE catdv_clip_id = ?
            """,
            (clip_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return dict(zip(
            ("catdv_clip_id", "gcs_uri", "mime_type", "size_bytes", "sha256",
             "uploaded_at", "last_used_at"),
            row,
        ))

    async def touch(self, conn: aiosqlite.Connection, clip_id: int) -> None:
        await conn.execute(
            "UPDATE gcs_files SET last_used_at = ? WHERE catdv_clip_id = ?",
            (_now_iso(), clip_id),
        )
        await conn.commit()
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_gcs_files_repo.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/repositories/gcs_files.py tests/integration/test_gcs_files_repo.py
git commit -m "feat: GcsFilesRepo to remember reusable GCS URIs"
```

---

## Phase 7 — Annotator core (Tasks 26–29)

This is the safety-critical phase. Pure-logic units come first (target_map expansion, payload merge), then the worker that orchestrates them.

### Task 26: target_map expansion (annotation → review_items)

**Files:**
- Create: `backend/app/services/target_map.py`
- Create: `tests/unit/test_target_map.py`

- [ ] **Step 1: Failing tests**

`tests/unit/test_target_map.py`:

```python
import pytest

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap
from backend.app.services.target_map import expand


def _tm(d):
    return TargetMap.model_validate(d)


def test_expand_markers_produces_one_review_item_per_scene():
    structured = {
        "scenes": [
            {"name": "scene-a", "in": {"secs": 0.0}, "out": {"secs": 5.0}},
            {"name": "scene-b", "in": {"secs": 5.0}, "out": {"secs": 10.0}},
        ]
    }
    tm = _tm({"scenes": {"kind": "markers"}})
    items = expand(structured, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 2
    assert all(it.kind == "marker" for it in items)
    assert items[0].proposed_value["name"] == "scene-a"


def test_expand_field_value():
    tm = _tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}})
    items = expand({"decade": "30.léta"}, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 1
    assert items[0].kind == "field"
    assert items[0].target_identifier == "pragafilm.dekáda.natočení"
    assert items[0].proposed_value == "30.léta"


def test_expand_note():
    tm = _tm({"summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"}})
    items = expand({"summary": "Rodinný portrét"}, tm, annotation_id=1, catdv_clip_id=42)
    assert len(items) == 1
    assert items[0].kind == "note"
    assert items[0].target_identifier == "pragafilm.popis.materialu"
    assert items[0].proposed_value == "Rodinný portrét"


def test_expand_skips_missing_schema_keys():
    tm = _tm({
        "scenes": {"kind": "markers"},
        "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
    })
    items = expand({"scenes": []}, tm, annotation_id=1, catdv_clip_id=42)
    assert items == []  # no scenes, decade missing — both skipped


def test_expand_handles_array_field():
    tm = _tm({"years": {"kind": "field", "identifier": "pragafilm.rok.natočení"}})
    items = expand({"years": ["1933", "1934"]}, tm, annotation_id=1, catdv_clip_id=42)
    assert items[0].proposed_value == ["1933", "1934"]


def test_expand_unwraps_value_evidence_pattern():
    """When schema returns {value, evidence_secs}, store as-is for UI to render."""
    tm = _tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}})
    items = expand(
        {"decade": {"value": "30.léta", "evidence_secs": [4.0, 12.0]}},
        tm, annotation_id=1, catdv_clip_id=42,
    )
    assert items[0].proposed_value == {"value": "30.léta", "evidence_secs": [4.0, 12.0]}
```

- [ ] **Step 2: Implement `backend/app/services/target_map.py`**

```python
from typing import Any

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetEntry, TargetMap


def expand(
    structured: dict[str, Any],
    target_map: TargetMap,
    *,
    annotation_id: int,
    catdv_clip_id: int,
) -> list[ReviewItem]:
    """Walk target_map; emit one ReviewItem per concrete change."""
    items: list[ReviewItem] = []
    for key, entry in target_map.fields.items():
        if key not in structured or structured[key] is None:
            continue
        value = structured[key]
        items.extend(_expand_one(entry, value, annotation_id, catdv_clip_id))
    return items


def _expand_one(entry: TargetEntry, value: Any,
                annotation_id: int, catdv_clip_id: int) -> list[ReviewItem]:
    if entry.kind == "markers":
        if not isinstance(value, list):
            return []
        return [
            ReviewItem(
                annotation_id=annotation_id,
                catdv_clip_id=catdv_clip_id,
                kind="marker",
                proposed_value=m,
            )
            for m in value
        ]
    if entry.kind == "field":
        return [ReviewItem(
            annotation_id=annotation_id,
            catdv_clip_id=catdv_clip_id,
            kind="field",
            target_identifier=entry.identifier,
            proposed_value=value,
        )]
    if entry.kind == "note":
        return [ReviewItem(
            annotation_id=annotation_id,
            catdv_clip_id=catdv_clip_id,
            kind="note",
            target_identifier=entry.target,
            proposed_value=value,
        )]
    return []
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_target_map.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/target_map.py tests/unit/test_target_map.py
git commit -m "feat: target_map expansion to review_items"
```

---

### Task 27: Payload builder (review_items → CatDV PUT payload)

This is the most safety-critical code in the app — PUT replaces arrays. A bug here destroys CatDV data. Heaviest unit-test coverage.

**Files:**
- Create: `backend/app/services/payload_builder.py`
- Create: `tests/unit/test_payload_builder.py`

- [ ] **Step 1: Failing tests**

`tests/unit/test_payload_builder.py`:

```python
import pytest

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap
from backend.app.services.payload_builder import build_put_payload


SAMPLE_CLIP = {
    "ID": 42,
    "name": "x",
    "markers": [
        {"name": "existing-a", "in": {"frm": 0, "secs": 0.0}, "out": {"frm": 25, "secs": 1.0}},
    ],
    "fields": {
        "pragafilm.dekáda.natočení": "20.léta",  # will be overwritten by accepted change
        "pragafilm.popis.materialu": "Existing notes.",
        "pragafilm.barva": "false",  # untouched by changes
    },
    "notes": "old notes",
}


def _tm(d):
    return TargetMap.model_validate(d)


def test_no_accepted_items_returns_empty_payload():
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[], target_map=_tm({}),
    )
    assert payload == {}


def test_accepted_marker_appends_to_existing():
    new_marker = {"name": "scene-b", "in": {"frm": 100, "secs": 4.0},
                  "out": {"frm": 200, "secs": 8.0}}
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="marker",
                     proposed_value=new_marker, decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"scenes": {"kind": "markers"}}),
    )
    assert payload["markers"][0]["name"] == "existing-a"
    assert payload["markers"][1]["name"] == "scene-b"


def test_dedupes_marker_on_overlapping_in_frm():
    """If new marker shares in.frm with existing, keep the existing one (no dup)."""
    new_marker = {"name": "duplicate", "in": {"frm": 0, "secs": 0.0},
                  "out": {"frm": 25, "secs": 1.0}}
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="marker",
                     proposed_value=new_marker, decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"scenes": {"kind": "markers"}}),
    )
    assert len(payload["markers"]) == 1
    assert payload["markers"][0]["name"] == "existing-a"


def test_edited_value_used_over_proposed():
    new_marker = {"name": "scene-x", "in": {"frm": 100, "secs": 4.0}}
    edited = {"name": "scene-x (edited)", "in": {"frm": 110, "secs": 4.4}}
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="marker",
                     proposed_value=new_marker, edited_value=edited, decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"scenes": {"kind": "markers"}}),
    )
    assert payload["markers"][-1]["name"] == "scene-x (edited)"
    assert payload["markers"][-1]["in"]["frm"] == 110


def test_field_set_replaces_value():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                     target_identifier="pragafilm.dekáda.natočení",
                     proposed_value="30.léta", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert payload["fields"]["pragafilm.dekáda.natočení"] == "30.léta"
    # untouched fields not in payload (only changed keys)
    assert "pragafilm.barva" not in payload.get("fields", {})


def test_field_unwraps_value_evidence_pattern():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                     target_identifier="pragafilm.dekáda.natočení",
                     proposed_value={"value": "30.léta", "evidence_secs": [4.0]},
                     decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    # Only the "value" goes to CatDV; evidence_secs stays in annotation archive.
    assert payload["fields"]["pragafilm.dekáda.natočení"] == "30.léta"


def test_note_append_mode_joins_with_separator():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="note",
                     target_identifier="pragafilm.popis.materialu",
                     proposed_value="New AI annotation.", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({
            "summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "append"}
        }),
    )
    assert payload["fields"]["pragafilm.popis.materialu"] == \
        "Existing notes.\n\n---\n\nNew AI annotation."


def test_note_replace_mode_overwrites():
    item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="note",
                     target_identifier="pragafilm.popis.materialu",
                     proposed_value="Fresh.", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[item],
        target_map=_tm({
            "summary": {"kind": "note", "target": "pragafilm.popis.materialu", "mode": "replace"}
        }),
    )
    assert payload["fields"]["pragafilm.popis.materialu"] == "Fresh."


def test_rejected_items_are_ignored():
    rejected = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                         target_identifier="pragafilm.dekáda.natočení",
                         proposed_value="30.léta", decision="rejected")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[rejected],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert payload == {}


def test_payload_omits_unchanged_arrays():
    """If no markers proposed, 'markers' key must NOT appear (PUT would replace array)."""
    field_item = ReviewItem(annotation_id=1, catdv_clip_id=42, kind="field",
                           target_identifier="pragafilm.dekáda.natočení",
                           proposed_value="30.léta", decision="accepted")
    payload = build_put_payload(
        current=SAMPLE_CLIP, accepted_items=[field_item],
        target_map=_tm({"decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"}}),
    )
    assert "markers" not in payload
```

- [ ] **Step 2: Implement `backend/app/services/payload_builder.py`**

```python
from typing import Any

from backend.app.models.annotation import ReviewItem
from backend.app.models.template import TargetMap


NOTE_SEPARATOR = "\n\n---\n\n"


def build_put_payload(
    *,
    current: dict[str, Any],
    accepted_items: list[ReviewItem],
    target_map: TargetMap,
) -> dict[str, Any]:
    """Build the minimal PUT payload for CatDV from accepted review_items.

    Critical invariants:
    - PUT replaces the markers array wholesale; we MUST include all existing markers
      whenever any new marker is added.
    - Other arrays/fields not touched by accepted_items must NOT appear in the payload.
    - Edited values win over proposed values.
    - Markers are deduped on existing in.frm to avoid double-writes on retry.
    """
    payload: dict[str, Any] = {}

    accepted = [it for it in accepted_items if it.decision == "accepted"]

    marker_items = [it for it in accepted if it.kind == "marker"]
    if marker_items:
        existing = current.get("markers", [])
        existing_in_frms = {_in_frm(m) for m in existing if _in_frm(m) is not None}
        new_markers = []
        for it in marker_items:
            value = it.edited_value if it.edited_value is not None else it.proposed_value
            if not isinstance(value, dict):
                continue
            if _in_frm(value) in existing_in_frms:
                continue  # dedupe
            new_markers.append(value)
            existing_in_frms.add(_in_frm(value))
        if new_markers:
            payload["markers"] = list(existing) + new_markers

    field_changes: dict[str, Any] = {}

    for it in accepted:
        value = it.edited_value if it.edited_value is not None else it.proposed_value
        if it.kind == "field":
            if it.target_identifier is None:
                continue
            field_changes[it.target_identifier] = _unwrap_value(value)
        elif it.kind == "note":
            if it.target_identifier is None:
                continue
            mode = _note_mode(target_map, it.target_identifier)
            new_text = _unwrap_value(value)
            if mode == "append":
                existing_text = _existing_text(current, it.target_identifier)
                if existing_text:
                    field_changes[it.target_identifier] = (
                        existing_text + NOTE_SEPARATOR + str(new_text)
                    )
                else:
                    field_changes[it.target_identifier] = str(new_text)
            else:
                field_changes[it.target_identifier] = str(new_text)

    if field_changes:
        payload["fields"] = field_changes

    return payload


def _in_frm(marker: dict[str, Any]) -> int | None:
    in_obj = marker.get("in") if isinstance(marker, dict) else None
    if isinstance(in_obj, dict):
        v = in_obj.get("frm")
        if isinstance(v, int):
            return v
    return None


def _unwrap_value(value: Any) -> Any:
    """If schema returned {value, evidence_secs}, take only 'value'."""
    if isinstance(value, dict) and "value" in value and "evidence_secs" in value:
        return value["value"]
    return value


def _note_mode(target_map: TargetMap, identifier: str) -> str:
    for entry in target_map.fields.values():
        if entry.kind == "note" and entry.target == identifier:
            return entry.mode
    return "append"


def _existing_text(current: dict[str, Any], identifier: str) -> str | None:
    if identifier in current.get("fields", {}):
        v = current["fields"][identifier]
        return v if isinstance(v, str) else None
    if identifier in ("notes", "bigNotes") and identifier in current:
        v = current[identifier]
        return v if isinstance(v, str) else None
    return None
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_payload_builder.py -v
```

Expected: 10 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/payload_builder.py tests/unit/test_payload_builder.py
git commit -m "feat: payload_builder with safe merge for CatDV PUT"
```

---

### Task 28: SSE event bus

**Files:**
- Create: `backend/app/services/events.py`
- Create: `tests/unit/test_events.py`

This goes before the worker because the worker pushes to it.

- [ ] **Step 1: Failing tests**

`tests/unit/test_events.py`:

```python
import asyncio

import pytest

from backend.app.services.events import EventBus


@pytest.mark.asyncio
async def test_subscribers_receive_events_for_their_topic():
    bus = EventBus()
    q1 = bus.subscribe("job:42")
    q2 = bus.subscribe("job:99")

    await bus.publish("job:42", {"item_id": 1, "status": "uploading"})
    await bus.publish("job:99", {"item_id": 7, "status": "prompting"})
    await bus.publish("job:42", {"item_id": 2, "status": "annotated"})

    e1 = await asyncio.wait_for(q1.get(), timeout=1)
    e2 = await asyncio.wait_for(q1.get(), timeout=1)
    e3 = await asyncio.wait_for(q2.get(), timeout=1)

    assert e1 == {"item_id": 1, "status": "uploading"}
    assert e2 == {"item_id": 2, "status": "annotated"}
    assert e3 == {"item_id": 7, "status": "prompting"}


@pytest.mark.asyncio
async def test_unsubscribe_removes_queue():
    bus = EventBus()
    q = bus.subscribe("topic")
    bus.unsubscribe("topic", q)
    await bus.publish("topic", {"x": 1})  # should not raise; q is no longer registered
    assert q.empty()


@pytest.mark.asyncio
async def test_publish_to_topic_with_no_subscribers_is_noop():
    bus = EventBus()
    await bus.publish("nobody", {"x": 1})  # no exception
```

- [ ] **Step 2: Implement `backend/app/services/events.py`**

```python
import asyncio
from collections import defaultdict
from typing import Any


class EventBus:
    """Minimal in-process pub/sub for SSE. One queue per subscriber per topic."""

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, topic: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self._subs[topic].append(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue) -> None:
        if topic in self._subs and q in self._subs[topic]:
            self._subs[topic].remove(q)
            if not self._subs[topic]:
                del self._subs[topic]

    async def publish(self, topic: str, payload: dict[str, Any]) -> None:
        for q in list(self._subs.get(topic, [])):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Drop oldest, push new (slow consumer protection)
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                q.put_nowait(payload)
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/unit/test_events.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/events.py tests/unit/test_events.py
git commit -m "feat: in-process EventBus for SSE job progress"
```

---

### Task 29: Job worker (orchestrates resolve → upload → annotate → expand)

**Files:**
- Create: `backend/app/services/annotator.py`
- Create: `tests/integration/test_annotator_worker.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_annotator_worker.py`:

```python
import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.models.template import Template
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.gcs_files import GcsFilesRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo
from backend.app.services.annotator import run_job
from backend.app.services.events import EventBus
from tests.fakes.fake_gemini import FakeResponse


class FakeResolver:
    def __init__(self, files: dict[int, Path]):
        self.files = files

    async def path_for_clip_id(self, clip_id: int) -> Path:
        return self.files[clip_id]

    def is_managed(self, path):
        return True


class FakeGcs:
    def __init__(self, bucket: str):
        self.bucket_name = bucket
        self.uploads: list[tuple[int, Path]] = []

    def gs_uri(self, clip_id: int) -> str:
        return f"gs://{self.bucket_name}/clips/{clip_id}.mov"

    def upload_if_absent(self, *, clip_id: int, local_path: Path, mime: str) -> str:
        self.uploads.append((clip_id, local_path))
        return self.gs_uri(clip_id)


class FakeCatdv:
    def __init__(self, clips: dict[int, dict]):
        self.clips = clips

    async def get_clip(self, clip_id: int) -> dict:
        return self.clips[clip_id]


class FakeGemini:
    def __init__(self, response: dict):
        self._response = response
        self.calls = []

    def annotate(self, *, gcs_uri, mime, prompt, schema, model):
        self.calls.append({"gcs_uri": gcs_uri, "prompt": prompt, "model": model})
        return self._response


@pytest.mark.asyncio
async def test_run_job_processes_two_clips_end_to_end(db, tmp_path):
    # Seed a template
    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t",
        prompt="describe scenes",
        output_schema={"type": "object"},
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        model="gemini-2.5-pro",
    ))

    # Seed a job with 2 items
    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, template_id=template_id, clip_ids=[101, 102])

    # Two on-disk fake proxies
    files = {}
    for clip_id in [101, 102]:
        p = tmp_path / f"{clip_id}.mov"
        p.write_bytes(b"X" * 100)
        files[clip_id] = p

    catdv = FakeCatdv({
        101: {"ID": 101, "name": "Clip_101", "markers": []},
        102: {"ID": 102, "name": "Clip_102", "markers": []},
    })
    resolver = FakeResolver(files)
    gcs = FakeGcs("bucket")
    gemini_response = {
        "text": "{}",
        "raw": {},
    }
    # Use a callable that returns parsed structured output
    structured = {
        "scenes": [{"name": "scene-1", "in": {"frm": 0, "secs": 0.0}, "out": {"frm": 25, "secs": 1.0}}],
        "decade": "30.léta",
    }

    class FakeGeminiStructured:
        def annotate(self, *, gcs_uri, mime, prompt, schema, model):
            import json
            return {"text": json.dumps(structured), "raw": {"candidates": [{"text": json.dumps(structured)}]}}

    bus = EventBus()
    sub_101 = bus.subscribe(f"job:{job_id}")

    await run_job(
        db=db,
        job_id=job_id,
        catdv=catdv,
        proxy_resolver=resolver,
        gcs=gcs,
        gemini=FakeGeminiStructured(),
        event_bus=bus,
        gcs_files_repo=GcsFilesRepo(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        templates_repo=templates,
    )

    # Both items reached review_ready
    items = await jobs_repo.list_items(db, job_id)
    assert [it.status for it in items] == ["review_ready", "review_ready"]

    # Annotations and review_items written
    annotations = AnnotationsRepo()
    rows_101 = await annotations.list_by_clip(db, 101)
    assert len(rows_101) == 1
    review = ReviewItemsRepo()
    items_101 = await review.list_by_clip(db, 101)
    # 1 marker + 1 field = 2
    assert {it.kind for it in items_101} == {"marker", "field"}

    # SSE events fired for clip 101
    assert not sub_101.empty()


@pytest.mark.asyncio
async def test_run_job_marks_item_error_when_gemini_raises(db, tmp_path):
    from backend.app.services.gemini import GeminiSafetyError

    templates = TemplatesRepo()
    template_id = await templates.create(db, Template(
        name="t", prompt="p", output_schema={}, target_map={"scenes": {"kind": "markers"}},
        model="m",
    ))
    jobs_repo = JobsRepo()
    job_id = await jobs_repo.create_job(db, template_id=template_id, clip_ids=[1])

    p = tmp_path / "1.mov"
    p.write_bytes(b"x")
    resolver = FakeResolver({1: p})
    catdv = FakeCatdv({1: {"ID": 1, "name": "c", "markers": []}})

    class FailingGemini:
        def annotate(self, **kwargs):
            raise GeminiSafetyError("blocked")

    await run_job(
        db=db,
        job_id=job_id,
        catdv=catdv,
        proxy_resolver=resolver,
        gcs=FakeGcs("b"),
        gemini=FailingGemini(),
        event_bus=EventBus(),
        gcs_files_repo=GcsFilesRepo(),
        annotations_repo=AnnotationsRepo(),
        review_items_repo=ReviewItemsRepo(),
        jobs_repo=jobs_repo,
        templates_repo=templates,
    )
    items = await jobs_repo.list_items(db, job_id)
    assert items[0].status == "error"
    assert "blocked" in (items[0].error_message or "")
```

- [ ] **Step 2: Implement `backend/app/services/annotator.py`**

```python
import hashlib
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any

import aiosqlite

from backend.app.models.annotation import Annotation
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.gcs_files import GcsFilesRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo
from backend.app.services.events import EventBus
from backend.app.services.target_map import expand

log = logging.getLogger(__name__)


async def run_job(
    *,
    db: aiosqlite.Connection,
    job_id: int,
    catdv,                            # CatdvClient-like (has .get_clip)
    proxy_resolver,                   # ProxyResolver
    gcs,                              # GcsService (upload_if_absent)
    gemini,                           # GeminiService (annotate)
    event_bus: EventBus,
    gcs_files_repo: GcsFilesRepo,
    annotations_repo: AnnotationsRepo,
    review_items_repo: ReviewItemsRepo,
    jobs_repo: JobsRepo,
    templates_repo: TemplatesRepo,
) -> None:
    """Run a job to completion (or cancellation).

    Serial per job; one VPN-pipe, one Gemini call at a time.
    """
    job = await jobs_repo.get_job(db, job_id)
    template = await templates_repo.get(db, job.template_id)
    await jobs_repo.update_status(db, job_id, "running")

    items = await jobs_repo.list_items(db, job_id)
    topic = f"job:{job_id}"

    for item in items:
        # Re-read job status to honor cancellation between items
        live = await jobs_repo.get_job(db, job_id)
        if live.status == "cancelled":
            log.info("job %s cancelled mid-run; stopping", job_id, extra={"job_id": job_id})
            break

        if item.status not in ("pending", "error"):
            continue

        try:
            await _process_item(
                db=db, item=item, template=template, catdv=catdv,
                proxy_resolver=proxy_resolver, gcs=gcs, gemini=gemini,
                gcs_files_repo=gcs_files_repo, annotations_repo=annotations_repo,
                review_items_repo=review_items_repo, jobs_repo=jobs_repo,
                event_bus=event_bus, topic=topic,
            )
        except Exception as exc:  # noqa: BLE001 — boundary catch, surface to UI
            log.exception("job %s clip %s failed", job_id, item.catdv_clip_id,
                          extra={"job_id": job_id, "clip_id": item.catdv_clip_id})
            await jobs_repo.update_item_status(
                db, item.id, "error", error=str(exc)
            )
            await event_bus.publish(topic, {"item_id": item.id, "status": "error",
                                              "error": str(exc)})

    # Finalize job status
    refreshed = await jobs_repo.list_items(db, job_id)
    final_status = "completed"
    if any(it.status == "error" for it in refreshed):
        final_status = "failed"
    if (await jobs_repo.get_job(db, job_id)).status == "cancelled":
        final_status = "cancelled"
    await jobs_repo.update_status(db, job_id, final_status)


async def _process_item(
    *, db, item, template, catdv, proxy_resolver, gcs, gemini,
    gcs_files_repo, annotations_repo, review_items_repo, jobs_repo,
    event_bus, topic,
) -> None:
    # 1. Resolve local path
    await jobs_repo.update_item_status(db, item.id, "resolving")
    await event_bus.publish(topic, {"item_id": item.id, "status": "resolving"})
    local_path: Path = await proxy_resolver.path_for_clip_id(item.catdv_clip_id)

    # 2. Upload to GCS (skip if already there + sha matches)
    await jobs_repo.update_item_status(db, item.id, "uploading")
    await event_bus.publish(topic, {"item_id": item.id, "status": "uploading"})

    sha = _sha256(local_path)
    existing = await gcs_files_repo.get(db, item.catdv_clip_id)
    if existing and existing["sha256"] == sha:
        gcs_uri = existing["gcs_uri"]
        await gcs_files_repo.touch(db, item.catdv_clip_id)
    else:
        mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
        gcs_uri = gcs.upload_if_absent(
            clip_id=item.catdv_clip_id, local_path=local_path, mime=mime,
        )
        await gcs_files_repo.upsert(
            db, clip_id=item.catdv_clip_id, gcs_uri=gcs_uri,
            mime_type=mime, size_bytes=local_path.stat().st_size, sha256=sha,
        )

    # 3. Fetch clip snapshot
    clip_snapshot: dict[str, Any] = await catdv.get_clip(item.catdv_clip_id)

    # 4. Prompt Gemini
    await jobs_repo.update_item_status(db, item.id, "prompting")
    await event_bus.publish(topic, {"item_id": item.id, "status": "prompting"})
    mime = mimetypes.guess_type(str(local_path))[0] or "video/quicktime"
    result = gemini.annotate(
        gcs_uri=gcs_uri, mime=mime, prompt=template.prompt,
        schema=template.output_schema, model=template.model,
    )

    # 5. Parse structured output
    structured: dict[str, Any] | None
    try:
        structured = json.loads(result["text"]) if result.get("text") else None
    except json.JSONDecodeError:
        structured = None

    # 6. Persist annotation row
    annotation_id = await annotations_repo.insert(
        db,
        Annotation(
            catdv_clip_id=item.catdv_clip_id,
            catdv_clip_name=clip_snapshot.get("name", ""),
            template_id=template.id,
            job_id=item.job_id,
            model=template.model,
            prompt_used=template.prompt,
            raw_response=result.get("raw", {}),
            structured_output=structured,
            clip_snapshot=clip_snapshot,
        ),
    )
    await jobs_repo.attach_annotation(db, item.id, annotation_id)

    # 7. Expand to review_items
    if structured:
        review = expand(
            structured, template.target_map,
            annotation_id=annotation_id, catdv_clip_id=item.catdv_clip_id,
        )
        if review:
            await review_items_repo.bulk_insert(db, review)

    # 8. Done
    await jobs_repo.update_item_status(db, item.id, "review_ready")
    await event_bus.publish(topic, {"item_id": item.id, "status": "review_ready",
                                      "annotation_id": annotation_id})


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_annotator_worker.py -v
```

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/annotator.py tests/integration/test_annotator_worker.py
git commit -m "feat: job worker orchestrating resolve→upload→prompt→annotate→expand"
```

---

## Phase 8 — AppContext + startup self-check (Tasks 30–31)

### Task 30: AppContext dataclass & lifespan wiring

**Files:**
- Create: `backend/app/context.py`
- Modify: `backend/app/main.py`
- Create: `tests/integration/test_context.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_context.py`:

```python
import pytest

from backend.app.context import AppContext
from backend.app.settings import Settings


@pytest.mark.asyncio
async def test_build_context_from_settings(tmp_path, monkeypatch):
    # Minimal env so Settings() construction works
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    settings = Settings()
    ctx = await AppContext.build(settings, init_external=False)
    try:
        assert ctx.settings is settings
        assert ctx.templates_repo is not None
        assert ctx.jobs_repo is not None
        assert ctx.event_bus is not None
        # DB has been initialized and migrations applied
        cur = await ctx.db.execute("SELECT count(*) FROM templates")
        assert (await cur.fetchone())[0] == 0
    finally:
        await ctx.aclose()
```

- [ ] **Step 2: Implement `backend/app/context.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.gcs_files import GcsFilesRepo
from backend.app.repositories.jobs import JobsRepo
from backend.app.repositories.proxy_cache import ProxyCacheRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.templates import TemplatesRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.events import EventBus
from backend.app.settings import Settings

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@dataclass
class AppContext:
    settings: Settings
    db: aiosqlite.Connection
    db_cm: object  # the contextmanager keeping the connection alive

    templates_repo: TemplatesRepo = field(default_factory=TemplatesRepo)
    jobs_repo: JobsRepo = field(default_factory=JobsRepo)
    annotations_repo: AnnotationsRepo = field(default_factory=AnnotationsRepo)
    review_items_repo: ReviewItemsRepo = field(default_factory=ReviewItemsRepo)
    write_log_repo: WriteLogRepo = field(default_factory=WriteLogRepo)
    proxy_cache_repo: ProxyCacheRepo = field(default_factory=ProxyCacheRepo)
    gcs_files_repo: GcsFilesRepo = field(default_factory=GcsFilesRepo)
    event_bus: EventBus = field(default_factory=EventBus)

    # External services (created when init_external=True)
    catdv = None
    gcs = None
    gemini = None
    proxy_resolver = None

    @classmethod
    async def build(cls, settings: Settings, *, init_external: bool = True) -> "AppContext":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db_path = settings.data_dir / "app.db"
        cm = open_db(db_path)
        conn = await cm.__aenter__()
        await apply_migrations(conn, MIGRATIONS)

        ctx = cls(settings=settings, db=conn, db_cm=cm)

        if init_external:
            from backend.app.services.catdv_client import CatdvClient
            from backend.app.services.gcs import GcsService
            from backend.app.services.gemini import GeminiService
            from backend.app.services.proxy_resolver import build_resolver

            ctx.catdv = CatdvClient(
                base_url=settings.catdv_base_url,
                username=settings.catdv_username or "",
                password=settings.catdv_password or "",
            )
            await ctx.catdv.__aenter__()
            ctx.gcs = GcsService(settings.gcs_bucket_name)
            ctx.gemini = GeminiService(
                project=settings.gcp_project_id, location=settings.gcp_location,
            )
            ctx.proxy_resolver = build_resolver(
                source=settings.proxy_source,
                catdv_client=ctx.catdv,
                cache_dir=settings.data_dir / "cache" / "proxies",
                fs_root=settings.proxy_fs_root,
                path_template=settings.proxy_path_template,
            )
        return ctx

    async def aclose(self) -> None:
        if self.catdv is not None:
            await self.catdv.__aexit__(None, None, None)
        await self.db_cm.__aexit__(None, None, None)
```

- [ ] **Step 3: Wire the lifespan in `backend/app/main.py`**

Update `backend/app/main.py`:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.context import AppContext
from backend.app.logging_setup import configure_logging
from backend.app.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = Settings()
    init_external = settings.app_env == "prod" or _real_external_enabled(settings)
    ctx = await AppContext.build(settings, init_external=init_external)
    app.state.ctx = ctx
    try:
        yield
    finally:
        await ctx.aclose()


def _real_external_enabled(s: Settings) -> bool:
    """In dev tests we bypass external services. In real dev usage, set
    APP_ENV=dev and ensure CATDV_* / GCP_* env vars point at real systems."""
    return all([
        s.catdv_base_url, s.catdv_username, s.catdv_password,
        s.gcp_project_id, s.gcs_bucket_name,
    ])


app = FastAPI(title="CatDV Annotator", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_context.py tests/integration/test_health.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/context.py backend/app/main.py tests/integration/test_context.py
git commit -m "feat: AppContext singleton wired via FastAPI lifespan"
```

---

### Task 31: Startup self-check

**Files:**
- Create: `backend/app/startup.py`
- Create: `tests/integration/test_startup_check.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_startup_check.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.app.startup import StartupCheckResult, run_checks


class FakeCatdv:
    def __init__(self, ok: bool):
        self._ok = ok

    async def get_clip(self, clip_id):
        if not self._ok:
            raise RuntimeError("connection refused")
        return {"ID": clip_id, "name": "x"}


class FakeBucket:
    def __init__(self, ok: bool):
        self._ok = ok

    def exists(self):
        return self._ok


class FakeGcs:
    def __init__(self, ok: bool):
        self._bucket = FakeBucket(ok)


@pytest.mark.asyncio
async def test_all_checks_pass():
    result = await run_checks(
        catdv=FakeCatdv(True),
        gcs=FakeGcs(True),
        proxy_resolver=MagicMock(path_for_clip_id=MagicMock()),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert result.ok
    assert result.failures == []


@pytest.mark.asyncio
async def test_catdv_failure_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(False),
        gcs=FakeGcs(True),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("CatDV" in f for f in result.failures)


@pytest.mark.asyncio
async def test_gcs_bucket_missing_is_reported():
    result = await run_checks(
        catdv=FakeCatdv(True),
        gcs=FakeGcs(False),
        proxy_resolver=MagicMock(),
        catalog_id=881507,
        sample_clip_id=1,
        verify_proxy=False,
    )
    assert not result.ok
    assert any("GCS" in f for f in result.failures)
```

- [ ] **Step 2: Implement `backend/app/startup.py`**

```python
from dataclasses import dataclass, field


@dataclass
class StartupCheckResult:
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


async def run_checks(
    *,
    catdv,
    gcs,
    proxy_resolver,
    catalog_id: int,
    sample_clip_id: int | None = None,
    verify_proxy: bool = False,
) -> StartupCheckResult:
    """Verify that external dependencies are reachable. Returns failures, never raises."""
    result = StartupCheckResult()

    # CatDV reachable
    try:
        if sample_clip_id is not None:
            await catdv.get_clip(sample_clip_id)
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"CatDV unreachable or sample clip missing: {exc}")

    # GCS bucket exists
    try:
        if not gcs._bucket.exists():
            result.failures.append(f"GCS bucket not found: {getattr(gcs, 'bucket_name', '?')}")
    except Exception as exc:  # noqa: BLE001
        result.failures.append(f"GCS check failed: {exc}")

    # Proxy resolver verification (only if requested + has sample)
    if verify_proxy and sample_clip_id is not None:
        try:
            await proxy_resolver.path_for_clip_id(sample_clip_id)
        except Exception as exc:  # noqa: BLE001
            result.failures.append(f"Proxy resolver failed for clip {sample_clip_id}: {exc}")

    return result
```

- [ ] **Step 3: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_startup_check.py -v
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/startup.py tests/integration/test_startup_check.py
git commit -m "feat: startup self-check for CatDV, GCS, proxy resolver"
```

---

## Phase 9 — API routes (Tasks 32–37)

End state: the backend exposes a complete HTTP API drivable via curl/pytest. UI is Plan B.

### Task 32: Templates CRUD route

**Files:**
- Create: `backend/app/routes/__init__.py`
- Create: `backend/app/routes/templates.py`
- Modify: `backend/app/main.py` (register router)
- Create: `tests/integration/test_routes_templates.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_routes_templates.py`:

```python
from fastapi.testclient import TestClient

from backend.app.main import app


def test_templates_crud_lifecycle(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    with TestClient(app) as client:
        # Empty list
        r = client.get("/api/templates")
        assert r.status_code == 200
        assert r.json() == []

        # Create
        r = client.post("/api/templates", json={
            "name": "scene-markers",
            "prompt": "describe scenes",
            "output_schema": {"type": "object"},
            "target_map": {"scenes": {"kind": "markers"}},
            "model": "gemini-2.5-pro",
        })
        assert r.status_code == 201
        new_id = r.json()["id"]
        assert new_id > 0

        # Get
        r = client.get(f"/api/templates/{new_id}")
        assert r.status_code == 200
        assert r.json()["name"] == "scene-markers"

        # List
        r = client.get("/api/templates")
        assert len(r.json()) == 1

        # Archive
        r = client.delete(f"/api/templates/{new_id}")
        assert r.status_code == 204

        # List again — archived not shown
        r = client.get("/api/templates")
        assert r.json() == []
```

- [ ] **Step 2: Implement `backend/app/routes/__init__.py`** (empty).

- [ ] **Step 3: Implement `backend/app/routes/templates.py`**

```python
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from backend.app.models.template import Template

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    description: str | None = None
    prompt: str
    output_schema: dict
    target_map: dict
    model: str


@router.get("")
async def list_templates(request: Request):
    ctx = request.app.state.ctx
    rows = await ctx.templates_repo.list_active(ctx.db)
    return [t.model_dump() for t in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(request: Request, body: TemplateCreate):
    ctx = request.app.state.ctx
    tpl = Template(
        name=body.name,
        description=body.description,
        prompt=body.prompt,
        output_schema=body.output_schema,
        target_map=body.target_map,
        model=body.model,
    )
    new_id = await ctx.templates_repo.create(ctx.db, tpl)
    return {"id": new_id}


@router.get("/{template_id}")
async def get_template(request: Request, template_id: int):
    ctx = request.app.state.ctx
    try:
        tpl = await ctx.templates_repo.get(ctx.db, template_id)
    except LookupError:
        raise HTTPException(404, "template not found")
    return tpl.model_dump()


@router.put("/{template_id}")
async def update_template(request: Request, template_id: int, body: TemplateCreate):
    ctx = request.app.state.ctx
    tpl = Template(
        name=body.name,
        description=body.description,
        prompt=body.prompt,
        output_schema=body.output_schema,
        target_map=body.target_map,
        model=body.model,
    )
    await ctx.templates_repo.update(ctx.db, template_id, tpl)
    return {"id": template_id}


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_template(request: Request, template_id: int):
    ctx = request.app.state.ctx
    await ctx.templates_repo.archive(ctx.db, template_id)
```

- [ ] **Step 4: Register the router in `backend/app/main.py`**

Add to the bottom of `main.py`:

```python
from backend.app.routes.templates import router as templates_router

app.include_router(templates_router)
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_routes_templates.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/__init__.py backend/app/routes/templates.py \
  backend/app/main.py tests/integration/test_routes_templates.py
git commit -m "feat: templates CRUD HTTP routes"
```

---

### Task 33: CatDV browse routes

**Files:**
- Create: `backend/app/routes/catdv.py`
- Modify: `backend/app/main.py`
- Create: `tests/integration/test_routes_catdv.py`

These routes proxy through to the live CatDV client. Tests use a mock context to avoid spinning up the fake CatDV in the FastAPI lifespan.

- [ ] **Step 1: Failing test**

`tests/integration/test_routes_catdv.py`:

```python
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.app.main import app
from tests.integration.test_routes_templates import (  # reuse env setup
    test_templates_crud_lifecycle as _setup,
)


def _attach_fake_catdv(monkeypatch, tmp_path, clips_response: dict, clip_obj: dict):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    client = TestClient(app)
    return client


def test_list_clips_proxies_to_catdv(monkeypatch, tmp_path):
    client = _attach_fake_catdv(monkeypatch, tmp_path,
                                clips_response={"total": 1, "clips": [{"ID": 1, "name": "x"}]},
                                clip_obj={"ID": 1, "name": "x"})
    with client:
        # Inject a fake CatDV client into the context
        ctx = client.app.state.ctx
        ctx.catdv = type("FakeC", (), {})()
        async def list_clips(*, catalog_id, offset=0, limit=50, q=None):
            return {"total": 1, "clips": [{"ID": 1, "name": "x"}]}
        async def get_clip(clip_id):
            return {"ID": clip_id, "name": "x"}
        ctx.catdv.list_clips = list_clips
        ctx.catdv.get_clip = get_clip

        r = client.get("/api/catdv/clips?limit=10")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["clips"][0]["ID"] == 1

        r = client.get("/api/catdv/clips/1")
        assert r.status_code == 200
        assert r.json()["ID"] == 1
```

- [ ] **Step 2: Implement `backend/app/routes/catdv.py`**

```python
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/catdv", tags=["catdv"])


@router.get("/clips")
async def list_clips(request: Request, q: str | None = None,
                      offset: int = 0, limit: int = 50):
    ctx = request.app.state.ctx
    if ctx.catdv is None:
        raise HTTPException(503, "CatDV client not initialized")
    return await ctx.catdv.list_clips(
        catalog_id=ctx.settings.catdv_catalog_id, offset=offset, limit=limit, q=q,
    )


@router.get("/clips/{clip_id}")
async def get_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.catdv is None:
        raise HTTPException(503, "CatDV client not initialized")
    try:
        return await ctx.catdv.get_clip(clip_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"upstream CatDV error: {exc}")
```

- [ ] **Step 3: Register in `backend/app/main.py`**

```python
from backend.app.routes.catdv import router as catdv_router
app.include_router(catdv_router)
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_routes_catdv.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/catdv.py backend/app/main.py tests/integration/test_routes_catdv.py
git commit -m "feat: CatDV browse routes (list, get)"
```

---

### Task 34: Jobs routes + worker spawn

**Files:**
- Create: `backend/app/routes/jobs.py`
- Modify: `backend/app/context.py` (add `_running_jobs` map)
- Modify: `backend/app/main.py`
- Create: `tests/integration/test_routes_jobs.py`

- [ ] **Step 1: Extend `AppContext` to track running jobs**

Add to `backend/app/context.py` inside the dataclass:

```python
    _running_jobs: dict[int, "object"] = field(default_factory=dict)
```

- [ ] **Step 2: Failing test**

`tests/integration/test_routes_jobs.py`:

```python
from fastapi.testclient import TestClient

from backend.app.main import app


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_create_job_lists_and_cancels(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        # Seed a template via direct repo call
        import asyncio
        from backend.app.models.template import Template
        tid = asyncio.get_event_loop().run_until_complete(
            ctx.templates_repo.create(ctx.db, Template(
                name="t", prompt="p", output_schema={}, target_map={"scenes": {"kind": "markers"}}, model="m",
            ))
        )

        r = client.post("/api/jobs", json={"template_id": tid, "clip_ids": [1, 2, 3], "auto_start": False})
        assert r.status_code == 201
        job_id = r.json()["id"]

        r = client.get("/api/jobs")
        assert any(j["id"] == job_id for j in r.json())

        r = client.get(f"/api/jobs/{job_id}")
        assert r.json()["total_clips"] == 3
        assert len(r.json()["items"]) == 3

        r = client.post(f"/api/jobs/{job_id}/cancel")
        assert r.status_code == 200
        r = client.get(f"/api/jobs/{job_id}")
        assert r.json()["status"] == "cancelled"
```

- [ ] **Step 3: Implement `backend/app/routes/jobs.py`**

```python
import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from pydantic import BaseModel

from backend.app.services.annotator import run_job

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    template_id: int
    clip_ids: list[int]
    auto_start: bool = True


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(request: Request, body: JobCreate, background: BackgroundTasks):
    ctx = request.app.state.ctx
    job_id = await ctx.jobs_repo.create_job(
        ctx.db, template_id=body.template_id, clip_ids=body.clip_ids,
    )
    if body.auto_start and ctx.catdv and ctx.gcs and ctx.gemini and ctx.proxy_resolver:
        task = asyncio.create_task(_run_in_bg(ctx, job_id))
        ctx._running_jobs[job_id] = task
    return {"id": job_id}


async def _run_in_bg(ctx, job_id: int) -> None:
    try:
        await run_job(
            db=ctx.db,
            job_id=job_id,
            catdv=ctx.catdv,
            proxy_resolver=ctx.proxy_resolver,
            gcs=ctx.gcs,
            gemini=ctx.gemini,
            event_bus=ctx.event_bus,
            gcs_files_repo=ctx.gcs_files_repo,
            annotations_repo=ctx.annotations_repo,
            review_items_repo=ctx.review_items_repo,
            jobs_repo=ctx.jobs_repo,
            templates_repo=ctx.templates_repo,
        )
    finally:
        ctx._running_jobs.pop(job_id, None)


@router.get("")
async def list_jobs(request: Request, limit: int = 50):
    ctx = request.app.state.ctx
    return [j.model_dump() for j in await ctx.jobs_repo.list_jobs(ctx.db, limit=limit)]


@router.get("/{job_id}")
async def get_job(request: Request, job_id: int):
    ctx = request.app.state.ctx
    try:
        job = await ctx.jobs_repo.get_job(ctx.db, job_id)
    except LookupError:
        raise HTTPException(404, "job not found")
    items = await ctx.jobs_repo.list_items(ctx.db, job_id)
    return {**job.model_dump(), "items": [it.model_dump() for it in items]}


@router.post("/{job_id}/cancel")
async def cancel_job(request: Request, job_id: int):
    ctx = request.app.state.ctx
    await ctx.jobs_repo.update_status(ctx.db, job_id, "cancelled")
    return {"id": job_id, "status": "cancelled"}
```

- [ ] **Step 4: Register router in `backend/app/main.py`**

```python
from backend.app.routes.jobs import router as jobs_router
app.include_router(jobs_router)
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_routes_jobs.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/jobs.py backend/app/context.py backend/app/main.py \
  tests/integration/test_routes_jobs.py
git commit -m "feat: jobs routes (create with worker spawn, list, get, cancel)"
```

---

### Task 35: Review routes (accept/edit/reject + apply-to-CatDV)

**Files:**
- Create: `backend/app/routes/review.py`
- Modify: `backend/app/main.py`
- Create: `tests/integration/test_routes_review.py`

This route is the safety boundary between AI output and CatDV. It calls `payload_builder` and writes through to CatDV via `catdv_client.put_clip`. Every write is logged in `write_log`.

- [ ] **Step 1: Failing test**

`tests/integration/test_routes_review.py`:

```python
import asyncio

from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.models.template import Template


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


async def _seed(ctx):
    tid = await ctx.templates_repo.create(ctx.db, Template(
        name="t", prompt="p", output_schema={},
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        model="m",
    ))
    aid = await ctx.annotations_repo.insert(ctx.db, Annotation(
        catdv_clip_id=1, catdv_clip_name="Clip_1", template_id=tid, model="m",
        prompt_used="p", raw_response={}, structured_output={},
        clip_snapshot={"ID": 1, "name": "Clip_1", "markers": [], "fields": {}},
    ))
    items = await ctx.review_items_repo.bulk_insert(ctx.db, [
        ReviewItem(annotation_id=aid, catdv_clip_id=1, kind="marker",
                   proposed_value={"name": "scene-a", "in": {"frm": 0, "secs": 0.0},
                                    "out": {"frm": 25, "secs": 1.0}}),
        ReviewItem(annotation_id=aid, catdv_clip_id=1, kind="field",
                   target_identifier="pragafilm.dekáda.natočení", proposed_value="30.léta"),
    ])
    return tid, aid, items


def test_list_pending_items(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        asyncio.get_event_loop().run_until_complete(_seed(ctx))
        r = client.get("/api/review/clips/1/items")
        assert r.status_code == 200
        assert len(r.json()) == 2


def test_set_decision_accept(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _, _, items = asyncio.get_event_loop().run_until_complete(_seed(ctx))
        item_id = items[0].id

        r = client.post(f"/api/review/items/{item_id}/decision",
                        json={"decision": "accepted"})
        assert r.status_code == 200
        r = client.get("/api/review/clips/1/items")
        accepted = [it for it in r.json() if it["decision"] == "accepted"]
        assert len(accepted) == 1


def test_apply_clip_writes_to_catdv_and_logs(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _, _, items = asyncio.get_event_loop().run_until_complete(_seed(ctx))

        async def get_clip(clip_id):
            return {"ID": clip_id, "name": "Clip_1", "markers": [], "fields": {}}
        async def put_clip(clip_id, payload):
            put_clip.last_payload = payload
            return {"ID": clip_id, "modifyDate": "2026-05-18"}
        put_clip.last_payload = None
        ctx.catdv = type("FakeC", (), {"get_clip": get_clip, "put_clip": put_clip})()

        # Accept both
        for it in items:
            client.post(f"/api/review/items/{it.id}/decision", json={"decision": "accepted"})

        r = client.post("/api/review/clips/1/apply")
        assert r.status_code == 200
        assert put_clip.last_payload is not None
        assert "markers" in put_clip.last_payload
        assert put_clip.last_payload["fields"]["pragafilm.dekáda.natočení"] == "30.léta"
```

- [ ] **Step 2: Implement `backend/app/routes/review.py`**

```python
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.app.services.payload_builder import build_put_payload

router = APIRouter(prefix="/api/review", tags=["review"])


class Decision(BaseModel):
    decision: str  # "accepted" | "rejected" | "pending"
    edited_value: Any = None


@router.get("/clips/{clip_id}/items")
async def list_items_for_clip(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    items = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id)
    return [it.model_dump() for it in items]


@router.post("/items/{item_id}/decision")
async def set_decision(request: Request, item_id: int, body: Decision):
    ctx = request.app.state.ctx
    if body.decision not in ("accepted", "rejected", "pending"):
        raise HTTPException(400, "decision must be accepted|rejected|pending")
    await ctx.review_items_repo.set_decision(
        ctx.db, item_id, body.decision, edited_value=body.edited_value,
    )
    return {"id": item_id, "decision": body.decision}


@router.post("/clips/{clip_id}/apply")
async def apply_clip(request: Request, clip_id: int):
    """Build payload from accepted items, PUT to CatDV, log result."""
    ctx = request.app.state.ctx
    if ctx.catdv is None:
        raise HTTPException(503, "CatDV client not initialized")

    accepted = await ctx.review_items_repo.list_by_clip(ctx.db, clip_id, decision="accepted")
    if not accepted:
        return {"applied": 0, "payload": {}}

    # Fetch fresh CatDV state — never trust clip_snapshot
    try:
        current = await ctx.catdv.get_clip(clip_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"CatDV get_clip failed: {exc}")

    # We need the template's target_map to honor note mode etc.
    # Take target_map from the most-recent annotation among the accepted items.
    annotation = await ctx.annotations_repo.get(ctx.db, accepted[0].annotation_id)
    template = await ctx.templates_repo.get(ctx.db, annotation.template_id)

    payload = build_put_payload(
        current=current, accepted_items=accepted, target_map=template.target_map,
    )

    if not payload:
        return {"applied": 0, "payload": {}}

    try:
        response = await ctx.catdv.put_clip(clip_id, payload)
        status = "ok"
    except Exception as exc:  # noqa: BLE001
        await ctx.write_log_repo.record(
            ctx.db, catdv_clip_id=clip_id, annotation_id=annotation.id,
            payload=payload, response={"error": str(exc)}, status="error",
        )
        raise HTTPException(502, f"CatDV put_clip failed: {exc}")

    await ctx.write_log_repo.record(
        ctx.db, catdv_clip_id=clip_id, annotation_id=annotation.id,
        payload=payload, response=response, status="ok",
    )
    await ctx.review_items_repo.mark_applied(
        ctx.db, [it.id for it in accepted if it.id is not None],
    )
    return {"applied": len(accepted), "payload": payload}
```

- [ ] **Step 3: Register router in `backend/app/main.py`**

```python
from backend.app.routes.review import router as review_router
app.include_router(review_router)
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_routes_review.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/review.py backend/app/main.py tests/integration/test_routes_review.py
git commit -m "feat: review routes with safe payload merge and write_log audit"
```

---

### Task 36: Media streaming route

**Files:**
- Create: `backend/app/routes/media.py`
- Modify: `backend/app/main.py`
- Create: `tests/integration/test_routes_media.py`

This serves the local proxy file (cached in dev, on-disk in prod) to the future UI player with Range support.

- [ ] **Step 1: Failing test**

`tests/integration/test_routes_media.py`:

```python
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from backend.app.main import app


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_media_streams_full_file(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        proxy = tmp_path / "42.mov"
        proxy.write_bytes(b"V" * 1000)

        async def path_for_clip_id(clip_id):
            assert clip_id == 42
            return proxy
        ctx.proxy_resolver = MagicMock(path_for_clip_id=path_for_clip_id)

        r = client.get("/api/media/42")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("video/")
        assert r.content == b"V" * 1000


def test_media_serves_range(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        proxy = tmp_path / "42.mov"
        proxy.write_bytes(b"X" * 100 + b"Y" * 100)

        async def path_for_clip_id(clip_id):
            return proxy
        ctx.proxy_resolver = MagicMock(path_for_clip_id=path_for_clip_id)

        r = client.get("/api/media/42", headers={"Range": "bytes=100-199"})
        assert r.status_code == 206
        assert r.content == b"Y" * 100
        assert r.headers["content-range"] == "bytes 100-199/200"
```

- [ ] **Step 2: Implement `backend/app/routes/media.py`**

```python
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

router = APIRouter(prefix="/api/media", tags=["media"])

_DEFAULT_CHUNK = 1 << 16  # 64 KiB


@router.get("/{clip_id}")
async def stream_media(request: Request, clip_id: int):
    ctx = request.app.state.ctx
    if ctx.proxy_resolver is None:
        raise HTTPException(503, "proxy resolver not initialized")

    try:
        path: Path = await ctx.proxy_resolver.path_for_clip_id(clip_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404, f"proxy unavailable: {exc}")

    mime = mimetypes.guess_type(str(path))[0] or "video/quicktime"
    size = path.stat().st_size
    range_header = request.headers.get("range")

    if range_header and range_header.startswith("bytes="):
        start_s, _, end_s = range_header[6:].partition("-")
        try:
            start = int(start_s)
            end = int(end_s) if end_s else size - 1
        except ValueError:
            raise HTTPException(400, "bad Range header")
        if start >= size or end >= size or start > end:
            raise HTTPException(416, "Range not satisfiable")
        length = end - start + 1

        def _stream():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(_DEFAULT_CHUNK, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            _stream(),
            status_code=206,
            media_type=mime,
            headers={
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
            },
        )

    return FileResponse(path, media_type=mime, headers={"Accept-Ranges": "bytes"})
```

- [ ] **Step 3: Register router in `backend/app/main.py`**

```python
from backend.app.routes.media import router as media_router
app.include_router(media_router)
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_routes_media.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/media.py backend/app/main.py tests/integration/test_routes_media.py
git commit -m "feat: media streaming route with Range support"
```

---

### Task 37: SSE events route for job progress

**Files:**
- Create: `backend/app/routes/events.py`
- Modify: `backend/app/main.py`
- Create: `tests/integration/test_routes_events.py`

- [ ] **Step 1: Failing test**

`tests/integration/test_routes_events.py`:

```python
import asyncio
import json

from fastapi.testclient import TestClient

from backend.app.main import app


def _setup_env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def test_sse_delivers_published_events(monkeypatch, tmp_path):
    _setup_env(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx

        # Pre-publish two events into the bus for job 7
        async def seed():
            await ctx.event_bus.publish("job:7", {"item_id": 1, "status": "uploading"})
            await ctx.event_bus.publish("job:7", {"item_id": 1, "status": "review_ready"})
        # We must publish AFTER a subscriber exists, so use the streaming endpoint:

        with client.stream("GET", "/api/jobs/7/events?_test_close_after=2") as resp:
            assert resp.status_code == 200
            # Push events from a separate task; consume below
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(seed())
            finally:
                loop.close()
            lines = []
            for raw in resp.iter_lines():
                if raw.startswith("data:"):
                    lines.append(json.loads(raw[len("data:"):].strip()))
                if len(lines) >= 2:
                    break
        statuses = [e["status"] for e in lines]
        assert "uploading" in statuses
        assert "review_ready" in statuses
```

Note: SSE with TestClient is tricky. If this becomes flaky, replace with a direct unit test against `_event_generator` (preferred — see Step 3).

Better: pure-function test on the generator. Replace the test with this:

```python
import asyncio
import json

import pytest

from backend.app.routes.events import _event_generator
from backend.app.services.events import EventBus


@pytest.mark.asyncio
async def test_event_generator_yields_sse_frames():
    bus = EventBus()
    gen = _event_generator(bus, topic="job:7", close_after=2)
    # Publish from a parallel task
    async def publish():
        await asyncio.sleep(0.01)
        await bus.publish("job:7", {"item_id": 1, "status": "uploading"})
        await bus.publish("job:7", {"item_id": 1, "status": "review_ready"})
    publisher = asyncio.create_task(publish())

    received = []
    async for chunk in gen:
        received.append(chunk)
        if len(received) >= 2:
            break
    await publisher

    parsed = [json.loads(c.removeprefix("data: ").strip()) for c in received]
    assert parsed[0]["status"] == "uploading"
    assert parsed[1]["status"] == "review_ready"
```

- [ ] **Step 2: Implement `backend/app/routes/events.py`**

```python
import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from backend.app.services.events import EventBus

router = APIRouter(tags=["events"])


async def _event_generator(bus: EventBus, *, topic: str,
                            close_after: int | None = None) -> AsyncIterator[str]:
    q = bus.subscribe(topic)
    try:
        emitted = 0
        while True:
            payload = await q.get()
            yield f"data: {json.dumps(payload)}\n\n"
            emitted += 1
            if close_after is not None and emitted >= close_after:
                return
    finally:
        bus.unsubscribe(topic, q)


@router.get("/api/jobs/{job_id}/events")
async def job_events(request: Request, job_id: int):
    ctx = request.app.state.ctx
    topic = f"job:{job_id}"

    async def stream():
        async for frame in _event_generator(ctx.event_bus, topic=topic):
            if await request.is_disconnected():
                return
            yield {"data": frame.removeprefix("data: ").rstrip("\n")}

    return EventSourceResponse(stream())
```

- [ ] **Step 3: Register router in `backend/app/main.py`**

```python
from backend.app.routes.events import router as events_router
app.include_router(events_router)
```

- [ ] **Step 4: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_routes_events.py -v
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/events.py backend/app/main.py \
  tests/integration/test_routes_events.py
git commit -m "feat: SSE route for live job progress events"
```

---

## Phase 10 — Scripts, seeds, deployment (Tasks 38–40)

### Task 38: GCP setup script + DEPLOY.md

**Files:**
- Create: `scripts/setup-gcp.sh`
- Create: `docs/DEPLOY.md`

- [ ] **Step 1: Create `scripts/setup-gcp.sh`**

```bash
#!/usr/bin/env bash
# One-time GCP infrastructure setup for the CatDV Annotator project.
# Usage:
#   export PROJECT_ID=pragafilm-catdv-annotator
#   export REGION=europe-west3
#   export BUCKET_NAME=${PROJECT_ID}-proxies
#   ./scripts/setup-gcp.sh

set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
: "${REGION:=europe-west3}"
: "${BUCKET_NAME:=${PROJECT_ID}-proxies}"

SA_NAME="catdv-annotator"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "== Project: $PROJECT_ID  region: $REGION  bucket: $BUCKET_NAME =="

echo "Enabling APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  secretmanager.googleapis.com \
  iamcredentials.googleapis.com \
  --project="$PROJECT_ID"

echo "Creating bucket (idempotent)..."
if ! gsutil ls -b "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
  gsutil mb -p "$PROJECT_ID" -l "$REGION" "gs://${BUCKET_NAME}"
else
  echo "  bucket exists"
fi

echo "Creating service account (idempotent)..."
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="CatDV Annotator" \
    --project="$PROJECT_ID"
else
  echo "  service account exists"
fi

echo "Granting bucket objectAdmin..."
gsutil iam ch "serviceAccount:${SA_EMAIL}:objectAdmin" "gs://${BUCKET_NAME}"

echo "Granting Vertex AI user..."
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --quiet

echo "Creating secrets (idempotent)..."
for secret in CATDV_USERNAME CATDV_PASSWORD; do
  if ! gcloud secrets describe "$secret" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud secrets create "$secret" --replication-policy=automatic --project="$PROJECT_ID"
  fi
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" --quiet
done

echo
echo "Done. Next steps:"
echo "  1. Populate secrets:"
echo "     echo -n 'klientAI' | gcloud secrets versions add CATDV_USERNAME --data-file=- --project=$PROJECT_ID"
echo "     echo -n '<password>' | gcloud secrets versions add CATDV_PASSWORD --data-file=- --project=$PROJECT_ID"
echo "  2. Generate a service-account key for local dev:"
echo "     gcloud iam service-accounts keys create ~/.gcp/${SA_NAME}-key.json \\"
echo "       --iam-account=${SA_EMAIL} --project=${PROJECT_ID}"
echo "  3. Point GOOGLE_APPLICATION_CREDENTIALS at that key in your .env."
```

- [ ] **Step 2: chmod the script**

```bash
chmod +x scripts/setup-gcp.sh
```

- [ ] **Step 3: Create `docs/DEPLOY.md`**

```markdown
# Deployment

This app runs in two places: the developer's Mac (dev) and the CatDV server (prod).
The same code; only env vars differ.

## Dev (Mac)

```bash
git clone <repo>
cd catdv-annotator
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env
# Edit .env: at minimum set CATDV_PASSWORD and GOOGLE_APPLICATION_CREDENTIALS
./run.sh
```

VPN to CatDV (`192.168.1.41`) must be up before starting.

## Prod (CatDV server)

### Prerequisites (one-time, requires admin access to the CatDV server)

1. **Linux user** with read access to the CatDV proxy directory (typically a
   member of the group owning `/usr/local/catdvServer/<proxies>`). Talk to Honza
   for the exact path and group.
2. **`python3.12` available** (or higher).
3. **Outbound HTTPS** to `*.googleapis.com` (Vertex AI + GCS) — confirm before
   deploying.

### Deploy

```bash
# As the service user, in /opt/catdv-annotator
git clone <repo> .
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env:
#   APP_ENV=prod
#   CATDV_BASE_URL=http://localhost:8080
#   PROXY_SOURCE=filesystem
#   PROXY_FS_ROOT=/usr/local/catdvServer/<proxies>
#   PROXY_PATH_TEMPLATE="{root}/{clip_id}.mov"   # confirm with Honza
#   GOOGLE_APPLICATION_CREDENTIALS=/etc/catdv-annotator/sa.json
# CATDV_USERNAME / CATDV_PASSWORD come from Secret Manager (do NOT set in .env)
sudo cp deploy/catdv-annotator.service /etc/systemd/system/
sudo systemctl enable --now catdv-annotator
```

### systemd service file (`deploy/catdv-annotator.service`)

```ini
[Unit]
Description=CatDV Annotator
After=network.target

[Service]
Type=simple
User=catdv
Group=catdv
WorkingDirectory=/opt/catdv-annotator
EnvironmentFile=/opt/catdv-annotator/.env
ExecStart=/opt/catdv-annotator/.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8765
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Confirming health

```bash
curl -s http://localhost:8765/api/health
# {"status":"ok"}

curl -s http://localhost:8765/api/templates
# []   (or seeded templates)
```

Logs:

```bash
journalctl -u catdv-annotator -f
```

### Rolling out a new version

```bash
cd /opt/catdv-annotator
sudo -u catdv git pull
sudo -u catdv .venv/bin/pip install -e .
sudo systemctl restart catdv-annotator
```
```

- [ ] **Step 4: Create `deploy/catdv-annotator.service`** with the content from DEPLOY.md.

```bash
mkdir -p deploy
# (write the systemd service file from DEPLOY.md above to deploy/catdv-annotator.service)
```

- [ ] **Step 5: Commit**

```bash
git add scripts/setup-gcp.sh docs/DEPLOY.md deploy/catdv-annotator.service
git commit -m "chore: GCP setup script + DEPLOY guide + systemd unit"
```

---

### Task 39: Seed default template

**Files:**
- Create: `backend/seeds/default_template.json`
- Create: `backend/app/seed.py`
- Modify: `backend/app/main.py` (call seed on startup)
- Create: `tests/integration/test_seed.py`

- [ ] **Step 1: Create `backend/seeds/default_template.json`**

```json
{
  "name": "Scene markers + Czech summary + era",
  "description": "Detects scenes, writes a Czech summary, and classifies the era. Default seeded template for the Pragafilm archive.",
  "prompt": "You are annotating archival home-movie footage from a Czech private archive. The video is silent monochrome film from the 1920s–1940s, digitised. Identify distinct scenes (continuous action without a cut), summarise the entire clip in 2–4 Czech sentences, and classify the era based on visual cues (clothing, vehicles, technology). Return JSON matching the schema.",
  "output_schema": {
    "type": "object",
    "required": ["scenes", "summary_cz", "decade", "years"],
    "properties": {
      "scenes": {
        "type": "array",
        "items": {
          "type": "object",
          "required": ["name", "in", "out"],
          "properties": {
            "name": {"type": "string"},
            "description": {"type": "string"},
            "in":  {"type": "object", "properties": {"secs": {"type": "number"}}, "required": ["secs"]},
            "out": {"type": "object", "properties": {"secs": {"type": "number"}}, "required": ["secs"]},
            "category": {"type": "string"}
          }
        }
      },
      "summary_cz": {
        "type": "object",
        "required": ["value"],
        "properties": {
          "value": {"type": "string"},
          "evidence_secs": {"type": "array", "items": {"type": "number"}}
        }
      },
      "decade": {
        "type": "object",
        "required": ["value"],
        "properties": {
          "value": {"type": "string", "enum": ["20.léta", "30.léta", "40.léta", "50.léta", "60.léta"]},
          "evidence_secs": {"type": "array", "items": {"type": "number"}}
        }
      },
      "years": {
        "type": "array",
        "items": {"type": "string"}
      }
    }
  },
  "target_map": {
    "scenes":     {"kind": "markers"},
    "summary_cz": {"kind": "note",  "target": "pragafilm.popis.materialu", "mode": "append"},
    "decade":     {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
    "years":      {"kind": "field", "identifier": "pragafilm.rok.natočení"}
  },
  "model": "gemini-2.5-pro"
}
```

- [ ] **Step 2: Failing test**

`tests/integration/test_seed.py`:

```python
from pathlib import Path

import pytest

from backend.app.repositories.templates import TemplatesRepo
from backend.app.seed import seed_default_template


SEED = Path(__file__).resolve().parents[2] / "backend" / "seeds" / "default_template.json"


@pytest.mark.asyncio
async def test_seed_inserts_template_only_once(db):
    await seed_default_template(db, seed_path=SEED)
    await seed_default_template(db, seed_path=SEED)

    repo = TemplatesRepo()
    rows = await repo.list_active(db)
    assert len(rows) == 1
    assert rows[0].name == "Scene markers + Czech summary + era"
```

- [ ] **Step 3: Implement `backend/app/seed.py`**

```python
import json
from pathlib import Path

import aiosqlite

from backend.app.models.template import Template
from backend.app.repositories.templates import TemplatesRepo


async def seed_default_template(conn: aiosqlite.Connection, *, seed_path: Path) -> None:
    """Insert the default template if no template by the same name exists."""
    data = json.loads(seed_path.read_text())
    cur = await conn.execute("SELECT 1 FROM templates WHERE name = ?", (data["name"],))
    if await cur.fetchone():
        return
    repo = TemplatesRepo()
    tpl = Template(
        name=data["name"],
        description=data.get("description"),
        prompt=data["prompt"],
        output_schema=data["output_schema"],
        target_map=data["target_map"],
        model=data["model"],
    )
    await repo.create(conn, tpl)
```

- [ ] **Step 4: Wire into the lifespan in `backend/app/main.py`**

In the `lifespan` function, after `apply_migrations` has run (i.e. after `AppContext.build`), call:

```python
from pathlib import Path
from backend.app.seed import seed_default_template

SEEDS = Path(__file__).resolve().parents[1] / "seeds"

# inside lifespan, after building ctx:
seed_path = SEEDS / "default_template.json"
if seed_path.exists():
    await seed_default_template(ctx.db, seed_path=seed_path)
```

- [ ] **Step 5: Run, see pass**

```bash
.venv/bin/pytest tests/integration/test_seed.py -v
```

- [ ] **Step 6: Commit**

```bash
git add backend/seeds/default_template.json backend/app/seed.py \
  backend/app/main.py tests/integration/test_seed.py
git commit -m "feat: seed default 'Scene markers + summary + era' template on startup"
```

---

### Task 40: run.sh + full-suite smoke + README update

**Files:**
- Create: `run.sh`
- Modify: `README.md`

- [ ] **Step 1: Create `run.sh`**

```bash
#!/usr/bin/env bash
# One-command dev start.
# - Creates .venv if missing
# - Installs/updates deps
# - Verifies .env
# - Starts uvicorn with reload

set -euo pipefail

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Creating .venv..."
  python3 -m venv .venv
fi

.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e ".[dev]"

if [ ! -f .env ]; then
  echo "ERROR: .env missing. Copy from .env.example and edit." >&2
  exit 1
fi

if [ -n "${CATDV_HEALTH_CHECK:-}" ]; then
  # Optional: ping CatDV before start
  HOST=$(grep -E '^CATDV_BASE_URL=' .env | cut -d= -f2 | sed 's|http://||' | cut -d: -f1)
  if ! ping -c1 -W1 "$HOST" >/dev/null 2>&1; then
    echo "WARN: $HOST not reachable. Is the VPN up?" >&2
  fi
fi

exec .venv/bin/uvicorn backend.app.main:app \
  --host "$(grep -E '^BIND_HOST=' .env | cut -d= -f2)" \
  --port "$(grep -E '^BIND_PORT=' .env | cut -d= -f2)" \
  --reload
```

- [ ] **Step 2: chmod**

```bash
chmod +x run.sh
```

- [ ] **Step 3: Full-suite smoke**

```bash
.venv/bin/pytest -q
```

Expected: all tests pass. Note any flakes; fix them before claiming the task done.

- [ ] **Step 4: Ruff check**

```bash
.venv/bin/ruff check backend tests
.venv/bin/ruff format --check backend tests
```

Fix any issues. If `format --check` reports diffs, run `.venv/bin/ruff format backend tests` and commit the formatting change separately.

- [ ] **Step 5: Update README**

`README.md`:

```markdown
# CatDV Annotator — Backend

Local-first web app for the Pragafilm CatDV archive: AI annotation jobs against
Gemini (Vertex AI) with results written back to CatDV.

**Backend only at this point.** UI is Plan B.

## Quick start (dev)

```bash
git clone <repo>
cd catdv-annotator
cp .env.example .env
# Edit .env — at minimum: CATDV_PASSWORD and GOOGLE_APPLICATION_CREDENTIALS
./run.sh
```

Then:

```bash
curl -s http://localhost:8765/api/health
curl -s http://localhost:8765/api/templates
```

## Tests

```bash
.venv/bin/pytest -q
```

## Layout

- `backend/app/` — FastAPI app, services, repositories, routes
- `backend/migrations/` — SQL migrations (applied at startup)
- `backend/seeds/` — default templates
- `tests/` — unit + integration tests
- `docs/specs/` — design spec
- `docs/plans/` — implementation plans
- `docs/DEPLOY.md` — production deployment guide
- `scripts/setup-gcp.sh` — one-time GCP infra setup

## Status

- Backend plan: see `docs/plans/2026-05-18-catdv-annotator-backend.md`
- UI plan: pending
```

- [ ] **Step 6: Commit**

```bash
git add run.sh README.md
git commit -m "feat: run.sh dev launcher + updated README"
```

---

## End-of-plan smoke checklist

Once all 40 tasks are complete, the following must all be true:

- `.venv/bin/pytest -q` — entire suite passes alone and as a single run (no isolation issues).
- `.venv/bin/ruff check backend tests` — clean.
- `./run.sh` boots the app, `curl http://localhost:8765/api/health` returns `{"status":"ok"}`.
- `curl http://localhost:8765/api/templates` returns the seeded default template.
- With CatDV VPN up and a real Gemini key configured, a manual end-to-end test against one short clip:
  1. `POST /api/jobs` with `{template_id, clip_ids:[<some-clip>]}` — returns job id, status becomes `running`.
  2. `GET /api/jobs/{id}/events` streams SSE frames showing `uploading`, `prompting`, `review_ready`.
  3. `GET /api/jobs/{id}` shows item status `review_ready`, annotation_id set.
  4. `GET /api/review/clips/{clip_id}/items` returns proposed markers/fields/notes.
  5. `POST /api/review/items/{id}/decision` accepts each.
  6. `POST /api/review/clips/{clip_id}/apply` returns success; CatDV web client shows the new markers/fields.
  7. `write_log` table has one row for the PUT.

This is the v1 backend Definition of Done.

---

## Spec coverage map

| Spec section | Plan tasks |
|---|---|
| §2 architecture / AppContext | Tasks 1, 30, 31 |
| §3 schema | Task 5 |
| §3 templates / target_map | Tasks 8, 20 |
| §3 jobs / job_items | Tasks 21 |
| §3 annotations + FTS5 | Task 22 |
| §3 review_items / write_log | Task 23 |
| §3 proxy_cache | Task 24 |
| §3 gcs_files | Task 25 |
| §4.1 worker loop | Task 29 |
| §4.2 apply with safe merge | Tasks 27, 35 |
| §4.3 concurrency / cancellation | Task 29 (cancellation check) |
| §4.4 failure modes | Tasks 10, 13, 16, 29, 35 |
| §5 review pane / video | Tasks 36 (media route); UI plan covers player.js |
| §6 pluggable proxy resolver | Tasks 17, 18, 19 |
| §7 GCP infra + Vertex flow | Tasks 14, 15, 16, 38 |
| §8 tech stack | Task 1 |
| §9.1 test layers | All tasks (TDD) |
| §9.2 DoD | End-of-plan smoke checklist |
| §9.3 operability | Tasks 3, 31, 38, 39, 40 |
| §10 PoC lessons | Reflected in architecture (no auth, no Cloud Run, plain text prompts, single DI context, test-isolation discipline) |
| §11 open questions | Carried into deploy task (PROXY_PATH_TEMPLATE confirmed with Honza at deploy time) |

UI-only spec items (§5 player widget, frontend keyboard shortcuts, Jinja/HTMX UI) are deferred to Plan B (`2026-05-18-catdv-annotator-ui.md`).
