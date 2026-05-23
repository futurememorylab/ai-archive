"""End-to-end offline cycle (spec §13 PR 5 + §15 test 7).

Boots a real `CatdvArchiveAdapter` against `FakeCatdv` plus a real
`RestProxyResolver`, real `WorkspaceManager`, `WriteQueue`,
`SyncEngine`, `ConnectionMonitor` (manual override only, no background
probe). Exercises the full lifecycle:

  1. Two CatDV clips + their proxy blobs seeded in the fake server.
  2. Create a workspace and add both clips.
  3. Run `prepare()` → both clips reach `ready`, proxies on disk,
     clip_cache populated.
  4. Flip `set_manual_offline(True)`.
  5. Enqueue an apply per clip via `WriteQueue.enqueue_apply`.
  6. Run `sync_engine.drain_once()` → no PUTs went out.
  7. Flip `set_manual_offline(False)`.
  8. Run `sync_engine.drain_once()` → both clips were PUT and the
     write_log has two `ok` rows.
"""

import asyncio
from pathlib import Path

import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.archive.providers.catdv.mapping import from_catdv_clip
from backend.app.models.annotation import Annotation, ReviewItem
from backend.app.repositories.annotations import AnnotationsRepo
from backend.app.repositories.clip_cache import ClipCacheRepo
from backend.app.repositories.field_def_cache import FieldDefCacheRepo
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.prompts import PromptsRepo
from backend.app.repositories.review_items import ReviewItemsRepo
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.catdv_client import CatdvClient
from backend.app.services.connection_monitor import ConnectionMonitor
from backend.app.services.proxy_resolver import RestProxyResolver
from backend.app.services.sync_engine import SyncEngine
from backend.app.services.workspace_manager import WorkspaceManager
from backend.app.services.write_queue import (
    WriteQueue,
    etag_from_snapshot,
    fps_from_snapshot,
)
from tests.fakes.fake_catdv import running_fake_catdv


def _seed_clip(fake, clip_id: int, name: str):
    fake.clips[clip_id] = {
        "ID": clip_id,
        "name": name,
        "fps": 25.0,
        "markers": [],
        "fields": [],
        "modifyDate": "2026-05-18",
    }
    fake.proxies[clip_id] = b"VIDEO_BYTES_" + str(clip_id).encode()


async def _seed_prompt_and_annotations(db, clip_ids: list[int]):
    """Insert a prompt+version + an annotation + accepted review_items per clip."""
    prompts = PromptsRepo()
    annotations = AnnotationsRepo()
    items_repo = ReviewItemsRepo()
    _, vid = await prompts.create_with_initial_version(
        db,
        name="t-e2e",
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
    review_items_by_clip: dict[int, list[ReviewItem]] = {}
    for clip_id in clip_ids:
        aid = await annotations.insert(
            db,
            Annotation(
                catdv_clip_id=clip_id,
                catdv_clip_name=f"Clip_{clip_id}",
                prompt_version_id=vid,
                model="m",
                prompt_used="p",
                raw_response={},
                structured_output={},
                clip_snapshot={
                    "ID": clip_id,
                    "name": f"Clip_{clip_id}",
                    "fps": 25.0,
                    "modifyDate": "2026-05-18",
                },
            ),
        )
        items = await items_repo.bulk_insert(
            db,
            [
                ReviewItem(
                    annotation_id=aid,
                    catdv_clip_id=clip_id,
                    kind="field",
                    target_identifier="pragafilm.dekáda.natočení",
                    proposed_value="30.léta",
                )
            ],
        )
        for it in items:
            await items_repo.set_decision(db, it.id, "accepted")
        # re-load with decided + applied_at
        review_items_by_clip[clip_id] = await items_repo.list_by_clip(
            db, clip_id, decision="accepted"
        )
    return vid, review_items_by_clip


@pytest.mark.asyncio
async def test_offline_cycle_full(db, tmp_path: Path):
    cache_dir = tmp_path / "proxies"
    with running_fake_catdv() as (base_url, fake):
        _seed_clip(fake, 101, "Clip_101")
        _seed_clip(fake, 102, "Clip_102")
        # Also stash provider_data so the adapter can derive catalog
        fake.clips[101]["catalogId"] = "881507"
        fake.clips[102]["catalogId"] = "881507"

        async with CatdvClient(base_url, "klientAI", "secret") as client:
            # --- wire services -----------------------------------
            adapter = CatdvArchiveAdapter(
                client=client,
                clip_cache_repo=ClipCacheRepo(),
                field_def_cache_repo=FieldDefCacheRepo(),
                db_provider=lambda: db,
            )
            resolver = RestProxyResolver(catdv=client, cache_dir=cache_dir)
            ws_repo = WorkspacesRepo()
            wm = WorkspaceManager(
                workspaces_repo=ws_repo,
                provider=adapter,
                proxy_resolver=resolver,
                db_provider=lambda: db,
            )
            monitor = ConnectionMonitor(
                provider=adapter,
                db_provider=lambda: db,
                interval_s=99999.0,  # background probe will not run in test
            )
            pending_repo = PendingOperationsRepo()
            write_log_repo = WriteLogRepo()
            engine = SyncEngine(
                provider=adapter,
                pending_ops_repo=pending_repo,
                write_log_repo=write_log_repo,
                connection_monitor=monitor,
                db_provider=lambda: db,
                tick_interval_s=99999.0,
            )
            queue = WriteQueue(
                pending_ops_repo=pending_repo,
                review_items_repo=ReviewItemsRepo(),
            )

            # --- 1. workspace + prep -----------------------------
            ws_id = await wm.create_workspace(
                name="train trip",
                provider_id="catdv",
                catalog_id="881507",
                clip_keys=[("catdv", "101"), ("catdv", "102")],
            )
            async for _ in wm.prepare(ws_id):
                pass

            ws = await wm.get(ws_id)
            assert {c["cache_state"] for c in ws["clips"]} == {"ready"}
            # proxies on disk
            assert (cache_dir / "101.mov").exists()
            assert (cache_dir / "102.mov").exists()
            # clip_cache populated
            cur = await db.execute("SELECT COUNT(*) FROM clip_cache WHERE provider_id='catdv'")
            assert (await cur.fetchone())[0] == 2

            # --- 2. enqueue applies (with engine offline) --------
            monitor.set_manual_offline(True)
            assert monitor.current_state().value == "offline"

            vid, items_by_clip = await _seed_prompt_and_annotations(db, [101, 102])
            prompts_repo = PromptsRepo()
            version = await prompts_repo.get_version(db, vid)
            for clip_id, items in items_by_clip.items():
                ann = await AnnotationsRepo().get(db, items[0].annotation_id)
                await queue.enqueue_apply(
                    db,
                    clip_key=("catdv", str(clip_id)),
                    items=items,
                    target_map=version.target_map,
                    expected_etag=etag_from_snapshot(ann.clip_snapshot),
                    annotation_id=ann.id,
                    fps=fps_from_snapshot(ann.clip_snapshot),
                )

            cur = await db.execute("SELECT COUNT(*) FROM pending_operations WHERE status='pending'")
            assert (await cur.fetchone())[0] == 2

            # --- 3. drain while offline → no PUT -----------------
            fake.put_log.clear()
            n = await engine.drain_once()
            assert n == 0
            assert fake.put_log == []

            # ops still pending
            cur = await db.execute("SELECT COUNT(*) FROM pending_operations WHERE status='pending'")
            assert (await cur.fetchone())[0] == 2

            # --- 4. go back online, drain ------------------------
            monitor.set_manual_offline(False)
            assert monitor.current_state().value == "online"

            # Wait briefly for the manual-online persist task to settle.
            await asyncio.sleep(0)

            n = await engine.drain_once()
            assert n == 2
            assert len(fake.put_log) == 2
            put_clip_ids = sorted(cid for cid, _ in fake.put_log)
            assert put_clip_ids == [101, 102]

            # ops applied
            cur = await db.execute("SELECT COUNT(*) FROM pending_operations WHERE status='applied'")
            assert (await cur.fetchone())[0] == 2

            # write_log has two ok rows
            cur = await db.execute("SELECT COUNT(*) FROM write_log WHERE status='ok'")
            assert (await cur.fetchone())[0] == 2

            # silence unused-import in some envs
            _ = from_catdv_clip
