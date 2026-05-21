"""End-to-end: WriteQueue → SyncEngine → FilesystemArchiveProvider.

Proves the whole local-first write stack works against the FS adapter
without CatDV in the loop. Mirrors `test_offline_cycle_e2e.py` but
collapsed because the FS adapter's `media_is_local=True` removes the
proxy-download leg.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.archive.providers.fs import media_probe
from backend.app.archive.providers.fs.adapter import FilesystemArchiveProvider
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.sync_engine import SyncEngine
from backend.app.services.write_queue import WriteQueue


@pytest.fixture(autouse=True)
def _stub_ffprobe(monkeypatch):
    media_probe.reset_warning_flag()
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: None)


@pytest.mark.asyncio
async def test_fs_e2e_setfield_applies_to_sidecar(db, tmp_path: Path):
    # --- FS archive on disk ----------------------------------------
    root = tmp_path / "fs_root"
    cat = root / "cat"
    cat.mkdir(parents=True)
    media = cat / "clip001.mov"
    media.write_bytes(b"")

    adapter = FilesystemArchiveProvider(fs_root=root)

    # --- repos + services -----------------------------------------
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    items_repo = ReviewItemsRepo()
    pending_repo = PendingOperationsRepo()
    write_log_repo = WriteLogRepo()

    _, vid = await prompts.create_with_initial_version(
        db,
        name="t-fs",
        description=None,
        body="p",
        target_map={
            "decade": {
                "kind": "field",
                "identifier": "pragafilm.dekáda.natočení",
            }
        },
        output_schema={},
        model="m",
    )
    aid = await annotations.insert(
        db,
        Annotation(
            catdv_clip_id=0,  # ignored on FS path
            catdv_clip_name="clip001",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={"name": "clip001", "fps": 25.0},
        ),
    )
    items = await items_repo.bulk_insert(
        db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=0,
                kind="field",
                target_identifier="pragafilm.dekáda.natočení",
                proposed_value="30.léta",
            )
        ],
    )
    for it in items:
        await items_repo.set_decision(db, it.id, "accepted")
    items = await items_repo.list_by_clip(db, 0, decision="accepted")

    version = await prompts.get_version(db, vid)
    queue = WriteQueue(
        pending_ops_repo=pending_repo, review_items_repo=items_repo
    )
    await queue.enqueue_apply(
        db,
        clip_key=("fs", "cat/clip001"),
        items=items,
        target_map=version.target_map,
        expected_etag=None,
        annotation_id=aid,
        fps=25.0,
    )

    cur = await db.execute(
        "SELECT COUNT(*) FROM pending_operations WHERE status='pending'"
    )
    assert (await cur.fetchone())[0] == 1

    # --- drain (no ConnectionMonitor → engine assumes online) ------
    engine = SyncEngine(
        provider=adapter,
        pending_ops_repo=pending_repo,
        write_log_repo=write_log_repo,
        connection_monitor=None,
        db_provider=lambda: db,
        tick_interval_s=99999.0,
    )
    n = await engine.drain_once()
    assert n == 1

    # --- sidecar landed on disk -----------------------------------
    sidecar = cat / "clip001.annot.json"
    assert sidecar.exists()
    doc = json.loads(sidecar.read_text())
    assert (
        doc["fields"]["pragafilm.dekáda.natočení"]["value"] == "30.léta"
    )

    cur = await db.execute(
        "SELECT COUNT(*) FROM pending_operations WHERE status='applied'"
    )
    assert (await cur.fetchone())[0] == 1
    cur = await db.execute(
        "SELECT COUNT(*) FROM write_log WHERE status='ok'"
    )
    assert (await cur.fetchone())[0] == 1
