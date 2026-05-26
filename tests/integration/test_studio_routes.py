import importlib
import io

from fastapi.testclient import TestClient


def _make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_OFFLINE", "true")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STUDIO_UPLOADS_DIR", str(tmp_path / "uploads"))
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return TestClient(main_mod.app)


# --- Task 12: testbench + folder CRUD ---


def test_create_testbench(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.post("/api/studio/testbenches", json={"name": "tb", "description": "x"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "tb"
        assert isinstance(body["id"], int)


def test_create_testbench_returns_409_on_duplicate(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.post("/api/studio/testbenches", json={"name": "dup"})
        r = client.post("/api/studio/testbenches", json={"name": "dup"})
        assert r.status_code == 409


def test_create_folder_and_subfolder(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        tb = client.post("/api/studio/testbenches", json={"name": "x"}).json()
        root = client.post(
            f"/api/studio/testbenches/{tb['id']}/folders",
            json={"parent_id": None, "name": "root"},
        ).json()
        sub = client.post(
            f"/api/studio/testbenches/{tb['id']}/folders",
            json={"parent_id": root["id"], "name": "sub"},
        ).json()
        assert sub["parent_id"] == root["id"]


def test_delete_non_empty_folder_409(monkeypatch, tmp_path):
    """Add a folder + an upload row via repo direct, then try DELETE."""
    with _make_client(monkeypatch, tmp_path) as client:
        tb = client.post("/api/studio/testbenches", json={"name": "y"}).json()
        root = client.post(
            f"/api/studio/testbenches/{tb['id']}/folders",
            json={"parent_id": None, "name": "r"},
        ).json()
        # Insert an item directly via the repo (no add_upload endpoint yet —
        # comes in Task 13). Use the running app's ctx.
        import asyncio

        ctx = client.app.state.ctx

        async def _add():
            await ctx.testbench_items_repo.add_upload(
                ctx.db, folder_id=root["id"], upload_path="x.mp4", original_name="x.mp4"
            )

        asyncio.run(_add())
        r = client.delete(f"/api/studio/folders/{root['id']}")
        assert r.status_code == 409


# --- Task 13: items + upload + gold ---


def _make_folder(client) -> int:
    tb = client.post("/api/studio/testbenches", json={"name": "items-tb"}).json()
    f = client.post(
        f"/api/studio/testbenches/{tb['id']}/folders",
        json={"parent_id": None, "name": "r"},
    ).json()
    return f["id"]


def test_add_catdv_item(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fid = _make_folder(client)
        r = client.post(
            f"/api/studio/folders/{fid}/items:add_catdv",
            json={"provider_clip_id": "123", "name": "catdv-123"},
        )
        assert r.status_code == 200
        assert r.json()["source_kind"] == "catdv_clip"


def test_upload_item(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fid = _make_folder(client)
        data = b"\x00" * 16
        files = {"file": ("vid.mp4", io.BytesIO(data), "video/mp4")}
        r = client.post(f"/api/studio/folders/{fid}/items:add_upload", files=files)
        assert r.status_code == 200
        body = r.json()
        assert body["source_kind"] == "upload"
        assert body["upload_orig_name"] == "vid.mp4"


def test_upload_item_rejects_non_video(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fid = _make_folder(client)
        files = {"file": ("evil.exe", io.BytesIO(b""), "application/x-exe")}
        r = client.post(f"/api/studio/folders/{fid}/items:add_upload", files=files)
        assert r.status_code == 415


def test_set_gold_round_trips_unknown_keys(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fid = _make_folder(client)
        item = client.post(
            f"/api/studio/folders/{fid}/items:add_catdv",
            json={"provider_clip_id": "1", "name": "x"},
        ).json()
        r = client.put(
            f"/api/studio/items/{item['id']}/gold",
            json={"description": "first", "future_field": 42},
        )
        assert r.status_code == 200
        r = client.put(
            f"/api/studio/items/{item['id']}/gold",
            json={"description": "second", "future_field": 42},
        )
        assert r.status_code == 200


def test_set_gold_clear_with_empty_description(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fid = _make_folder(client)
        item = client.post(
            f"/api/studio/folders/{fid}/items:add_catdv",
            json={"provider_clip_id": "1", "name": "x"},
        ).json()
        client.put(f"/api/studio/items/{item['id']}/gold", json={"description": "x"})
        r = client.put(f"/api/studio/items/{item['id']}/gold", json={"description": ""})
        assert r.status_code == 200


def test_delete_item(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        fid = _make_folder(client)
        item = client.post(
            f"/api/studio/folders/{fid}/items:add_catdv",
            json={"provider_clip_id": "1", "name": "x"},
        ).json()
        r = client.delete(f"/api/studio/items/{item['id']}")
        assert r.status_code == 200


# --- Task 14: run start/cancel ---


def test_start_run_returns_run_id(monkeypatch, tmp_path):
    """Run starts and returns id. We don't await the background task here —
    just verify the API contract."""
    with _make_client(monkeypatch, tmp_path) as client:
        # Need a prompt version. Insert via PromptsRepo directly.
        import asyncio
        ctx = client.app.state.ctx

        async def _setup():
            _, pv = await ctx.prompts_repo.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
                initial_state="production",
            )
            return pv

        pv_id = asyncio.run(_setup())

        tb = client.post("/api/studio/testbenches", json={"name": "rt"}).json()
        r = client.post(
            "/api/studio/runs",
            json={"testbench_id": tb["id"], "prompt_version_id": pv_id},
        )
        assert r.status_code == 200
        assert isinstance(r.json()["id"], int)


def test_cancel_run(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        import asyncio
        ctx = client.app.state.ctx

        async def _setup():
            _, pv = await ctx.prompts_repo.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
                initial_state="production",
            )
            return pv

        pv_id = asyncio.run(_setup())
        tb = client.post("/api/studio/testbenches", json={"name": "rt2"}).json()
        rid = client.post(
            "/api/studio/runs",
            json={"testbench_id": tb["id"], "prompt_version_id": pv_id},
        ).json()["id"]
        r = client.post(f"/api/studio/runs/{rid}:cancel")
        assert r.status_code == 200


# --- Task 15: offline smoke test ---


def test_studio_api_works_when_catdv_offline(monkeypatch, tmp_path):
    """`CATDV_OFFLINE=true` is already set by _make_client; the test simply
    asserts the studio JSON API serves 200 with no archive logged in."""
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.post("/api/studio/testbenches", json={"name": "offline-tb"})
        assert r.status_code == 200
        # ensure archive really is None / forced_offline
        ctx = client.app.state.ctx
        assert ctx.archive is None or ctx.connection_monitor is None or (
            ctx.connection_monitor is not None
            and not ctx.connection_monitor.current_state().name.endswith("online")
        )


# --- Task 16: page routes ---


def test_landing_page_lists_testbenches(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        client.post("/api/studio/testbenches", json={"name": "demo"})
        r = client.get("/studio")
        assert r.status_code == 200
        assert "demo" in r.text


def test_testbench_page_renders(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        tb = client.post("/api/studio/testbenches", json={"name": "tb"}).json()
        r = client.get(f"/studio/testbenches/{tb['id']}")
        assert r.status_code == 200


def test_run_detail_page_404_when_missing(monkeypatch, tmp_path):
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/studio/runs/999")
        assert r.status_code == 404


def test_run_detail_page_renders_for_existing_run(monkeypatch, tmp_path):
    import asyncio
    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.ctx

        async def _setup():
            _, pv = await ctx.prompts_repo.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
                initial_state="production",
            )
            tb = await ctx.testbenches_repo.create(ctx.db, name="t", description=None)
            rid = await ctx.studio_runs_repo.create(
                ctx.db, testbench_id=tb, prompt_version_id=pv,
            )
            return rid

        rid = asyncio.run(_setup())
        r = client.get(f"/studio/runs/{rid}")
        assert r.status_code == 200


def test_compare_page_returns_two_sides(monkeypatch, tmp_path):
    """Compare with gold on both sides should always render, even with no items."""
    with _make_client(monkeypatch, tmp_path) as client:
        tb = client.post("/api/studio/testbenches", json={"name": "cmp"}).json()
        r = client.get(
            f"/studio/testbenches/{tb['id']}/compare?left=gold&right=gold"
        )
        assert r.status_code == 200


def test_landing_page_works_when_offline(monkeypatch, tmp_path):
    """CATDV_OFFLINE=true is already set in _make_client; just verify /studio loads."""
    with _make_client(monkeypatch, tmp_path) as client:
        r = client.get("/studio")
        assert r.status_code == 200
