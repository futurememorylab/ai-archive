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
    from backend.app.models.template import Template

    tid = await ctx.templates_repo.create(
        ctx.db,
        Template(
            name="t",
            prompt="p",
            output_schema={},
            target_map={
                "scenes": {"kind": "markers"},
                "decade": {"kind": "field", "identifier": "pragafilm.dekáda.natočení"},
            },
            model="m",
        ),
    )
    aid = await ctx.annotations_repo.insert(
        ctx.db,
        Annotation(
            catdv_clip_id=1,
            catdv_clip_name="Clip_1",
            template_id=tid,
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
    return tid, aid, items


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


def test_list_pending_items(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _run(_seed(ctx))
        r = client.get("/api/review/clips/1/items")
        assert r.status_code == 200
        assert len(r.json()) == 2


def test_set_decision_accept(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _, _, items = _run(_seed(ctx))
        item_id = items[0].id

        r = client.post(f"/api/review/items/{item_id}/decision", json={"decision": "accepted"})
        assert r.status_code == 200
        r = client.get("/api/review/clips/1/items")
        accepted = [it for it in r.json() if it["decision"] == "accepted"]
        assert len(accepted) == 1


def test_apply_clip_writes_to_catdv_and_logs(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        ctx = client.app.state.ctx
        _, _, items = _run(_seed(ctx))

        async def get_clip(self, clip_id):
            return {"ID": clip_id, "name": "Clip_1", "markers": [], "fields": {}}

        async def put_clip(self, clip_id, payload):
            put_clip.last_payload = payload
            return {"ID": clip_id, "modifyDate": "2026-05-18"}

        put_clip.last_payload = None

        async def _aexit(self, exc_type, exc, tb):
            pass

        ctx.catdv = type(
            "FakeC",
            (),
            {
                "get_clip": get_clip,
                "put_clip": put_clip,
                "__aexit__": _aexit,
            },
        )()

        for it in items:
            client.post(f"/api/review/items/{it.id}/decision", json={"decision": "accepted"})

        r = client.post("/api/review/clips/1/apply")
        assert r.status_code == 200
        assert put_clip.last_payload is not None
        assert "markers" in put_clip.last_payload
        assert put_clip.last_payload["fields"]["pragafilm.dekáda.natočení"] == "30.léta"
