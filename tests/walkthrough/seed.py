"""Seed the test data: a real proxy video + a DB draft for the walkthrough clip."""

from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from backend.app.archive.model import CanonicalClip
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_list_cache import ClipListCacheRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from tests.walkthrough.fakes import (
    CLIP_ID,
    CLIP_NAME,
    DECADE_IDENT,
    PRODUCTION_PROMPT_NAME,
    REVIEW_FIXTURE_CLIP_ID,
    REVIEW_FIXTURE_CLIP_NAME,
)

# backend/migrations (this file is tests/walkthrough/seed.py → parents[2] is repo root).
_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "backend" / "migrations"


def make_proxy_video(out_path: Path, seconds: int = 8, fps: int = 25) -> Path:
    """Generate a short MP4 with a built-in running frame counter (so the player
    visibly plays on camera). Requires ffmpeg."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is required to seed the walkthrough proxy video. Install it "
            "(macOS: brew install ffmpeg)."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size=640x360:rate={fps}",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def make_thumbnail(out_path: Path, source_video: Path) -> Path:
    """Extract one frame from the proxy as a JPEG poster so clip rows render a
    real thumbnail on camera instead of a broken-image placeholder. Requires
    ffmpeg. One poster is reused for every clip (StubThumbnailService)."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg is required to seed the walkthrough thumbnail. Install it "
            "(macOS: brew install ffmpeg)."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0.5",
        "-i",
        str(source_video),
        "-frames:v",
        "1",
        "-vf",
        "scale=320:-1",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


async def seed_draft(db: aiosqlite.Connection) -> int:
    """Insert a prompt version + annotation + review items for the clip.

    The draft proposes a DECADE value that differs from the published clip
    (published='30.léta', draft='20.léta') so the review->correct story is
    visible; the scenario then corrects it to '40.léta'.
    """
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    items = ReviewItemsRepo()

    _, vid = await prompts.create_with_initial_version(
        db,
        name="scene-tagger",
        description=None,
        body="Describe the decade and scenes.",
        target_map={
            "decade": {"kind": "field", "identifier": DECADE_IDENT},
            "scenes": {"kind": "markers"},
        },
        output_schema={},
        model="gemini-2.5-pro",
    )
    aid = await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=CLIP_ID,
            catdv_clip_name=CLIP_NAME,
            prompt_version_id=vid,
            model="gemini-2.5-pro",
            prompt_used="Describe the decade and scenes.",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": CLIP_ID, "name": CLIP_NAME, "markers": [], "fields": {}},
        ),
    )
    await items.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=CLIP_ID,
                kind="field",
                target_identifier=DECADE_IDENT,
                proposed_value="20.léta",
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=CLIP_ID,
                kind="marker",
                proposed_value={
                    "name": "Establishing shot",
                    "in": {"frm": 0, "secs": 0.0},
                    "out": {"frm": 75, "secs": 3.0},
                },
            ),
        ],
    )
    return aid


async def seed_for_review_fixture(db: aiosqlite.Connection) -> int:
    """Seed a second clip with a pending draft that stays in 'Awaiting review'.

    No scenario publishes this clip, so the annotation-status filter has a
    stable member regardless of what the review→publish scenario does to clip
    101. Uses its own prompt name to avoid colliding with seed_draft's prompt.
    """
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    items = ReviewItemsRepo()

    _, vid = await prompts.create_with_initial_version(
        db,
        name="review-fixture-tagger",
        description=None,
        body="Describe the decade.",
        target_map={"decade": {"kind": "field", "identifier": DECADE_IDENT}},
        output_schema={},
        model="gemini-2.5-pro",
    )
    aid = await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=REVIEW_FIXTURE_CLIP_ID,
            catdv_clip_name=REVIEW_FIXTURE_CLIP_NAME,
            prompt_version_id=vid,
            model="gemini-2.5-pro",
            prompt_used="Describe the decade.",
            raw_response={},
            structured_output={},
            clip_snapshot={
                "ID": REVIEW_FIXTURE_CLIP_ID,
                "name": REVIEW_FIXTURE_CLIP_NAME,
                "markers": [],
                "fields": {},
            },
        ),
    )
    await items.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=REVIEW_FIXTURE_CLIP_ID,
                kind="field",
                target_identifier=DECADE_IDENT,
                proposed_value="20.léta",
            ),
        ],
    )
    return aid


async def seed_production_prompt(db: aiosqlite.Connection) -> int:
    """Seed one prompt with a PRODUCTION version matching the video clips.

    The bulk-annotate modal (`_loadPrompts`) and the New-batch picker only list
    prompts whose `current_production_version_id` is non-null, so without a
    promoted version neither surface has anything to pick and no job can be
    kicked off from the UI. `media_kind='video'` matches the seeded clips so it
    shows up for them. Returns the production version id.
    """
    prompts = PromptsRepo()
    prompt_id, vid = await prompts.create_with_initial_version(
        db,
        name=PRODUCTION_PROMPT_NAME,
        description="Production prompt for the bulk-annotate / cancel walkthroughs.",
        body="Identify the decade depicted in this clip.",
        target_map={"decade": {"kind": "field", "identifier": DECADE_IDENT}},
        output_schema={},
        model="gemini-2.5-pro",
        media_kind="video",
    )
    # create_with_initial_version makes a draft; promote it so it becomes the
    # prompt's current production version.
    await prompts.promote_version(db, prompt_id, vid)
    return vid


async def seed_clip_list_cache(
    db: aiosqlite.Connection, clips: tuple[CanonicalClip, ...], catalog_id: str
) -> None:
    """Persist the full catalog into clip_list_cache.

    The annotation-status 'none' (not-annotated) filter resolves against the
    universe of clips we've observed locally; without a cached list it would
    only know the annotated clips and return empty. This also lets the filtered
    list hydrate from cache instead of per-clip provider fetches.
    """
    await ClipListCacheRepo().upsert(
        db,
        provider_id="catdv",
        catalog_id=catalog_id,
        query_text=None,
        offset=0,
        limit=len(clips),
        total=len(clips),
        items=tuple(clips),
        fetched_at_iso=datetime.now(UTC).isoformat(),
    )


async def build_seed_db(
    db_path: Path,
    *,
    clips: tuple[CanonicalClip, ...] | None = None,
    catalog_id: str | None = None,
) -> int:
    """Build a standalone, fully-migrated + seeded SQLite DB at `db_path`.

    This runs on its OWN connection, isolated from the connection the app opens
    for the test run: the caller connects a *copy* of this file for the run, so
    seeding and the live run never share a database. Seeds the review draft
    (clip 101), the awaiting-review fixture (clip 110), and a production prompt
    (so the bulk-annotate / New-batch pickers have a selectable prompt); when
    `clips` + `catalog_id` are given, also seeds the clip-list cache so the
    annotation filters resolve against the full clip universe. The writes are
    committed and
    the WAL is checkpointed into the main file before the connection closes, so
    the file is safe to copy. Returns the seeded annotation id (clip 101).
    """
    async with open_db(db_path) as conn:
        await apply_migrations(conn, _MIGRATIONS_DIR)
        aid = await seed_draft(conn)
        await seed_for_review_fixture(conn)
        await seed_production_prompt(conn)
        if clips is not None and catalog_id is not None:
            await seed_clip_list_cache(conn, clips, catalog_id)
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await conn.commit()
        return aid
