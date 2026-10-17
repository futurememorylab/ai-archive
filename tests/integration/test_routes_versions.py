"""Integration test for the apply route's version id (clip versions are
recorded on apply; the version-switching routes were removed in ADR 0128).

These tests use the same harness as test_routes_review.py (_setenv,
install_live_ctx, _seed, TestClient). The harness boots offline (no
CatDV connection) because CATDV_USERNAME="".
"""

import asyncio
import importlib

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


async def _seed(ctx):
    from backend.app.models.annotation import Annotation, ReviewItem

    _, vid = await ctx.prompts_repo.create_with_initial_version(
        ctx.db,
        name="t",
        description=None,
        body="p",
        target_map={
            "scenes": {"kind": "markers"},
            "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
        },
        output_schema={},
        model="m",
    )
    aid = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Clip_1",
            prompt_version_id=vid,
            model="m",
            prompt_used="p",
            raw_response={},
            structured_output={},
            clip_snapshot={"ID": 1, "name": "Clip_1", "markers": [], "fields": {}},
        ),
    )
    items = await ctx.review_items_repo.bulk_insert(
        ctx.db,
        [
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="marker",
                proposed_value={
                    "name": "scene-a",
                    "in": {"frm": 0, "secs": 0.0},
                    "out": {"frm": 25, "secs": 1.0},
                },
            ),
            ReviewItem(
                annotation_id=aid,
                catdv_clip_id=1,
                kind="field",
                target_identifier="pragafilm.dekáda.natočení",
                proposed_value="30.léta",
            ),
        ],
    )
    return vid, aid, items


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_apply_clip_json_response_has_version_id(monkeypatch, tmp_path):
    """After Task 8 change, the non-HX apply path returns {"version_id": ...}."""
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.core_ctx
        _, _, items = _run(_seed(ctx))

        for it in items:
            client.post(
                f"/api/review/items/{it.id}/decision",
                json={"decision": "accepted"},
            )

        r = client.post("/api/review/clips/1/apply")
        assert r.status_code == 200
        body = r.json()
        # The new response shape has version_id
        assert "version_id" in body
        assert body["version_id"] is not None
