"""Studio domain models — round-trip + invariants."""

from backend.app.models.studio import StudioFolder, StudioFolderClip, StudioRun


def test_studio_folder_minimal():
    f = StudioFolder(id=1, name="edge_cases", created_at="2026-05-26T10:00:00+00:00")
    assert f.name == "edge_cases"


def test_studio_folder_clip_minimal():
    fc = StudioFolderClip(folder_id=1, clip_id=12041, added_at="2026-05-26T10:00:00+00:00")
    assert fc.clip_id == 12041


def test_studio_run_ok_with_output():
    r = StudioRun(
        id=1, prompt_version_id=10, clip_id=12041, job_id=99,
        status="ok",
        output_json={"scenes": [{"name": "garden", "in_secs": 0, "out_secs": 12.4}]},
        duration_s=7.4, tokens_in=14820, tokens_out=612, cost_usd=0.0218,
        model="gemini-2.5-pro",
        started_at="2026-05-26T10:00:00+00:00",
        finished_at="2026-05-26T10:00:07+00:00",
    )
    assert r.status == "ok"
    assert r.output_json["scenes"][0]["name"] == "garden"


def test_studio_run_pending_no_output():
    r = StudioRun(
        id=1, prompt_version_id=10, clip_id=12041, status="pending",
        started_at="2026-05-26T10:00:00+00:00",
    )
    assert r.output_json is None
    assert r.duration_s is None
