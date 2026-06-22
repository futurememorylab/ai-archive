"""Seed the test data: a real proxy video + a DB draft for the walkthrough clip."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import aiosqlite

from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from tests.walkthrough.fakes import CLIP_ID, CLIP_NAME, DECADE_IDENT


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
