import pytest

from backend.app.models.studio import (
    AnnotationOutput,
    StudioRun,
    StudioRunItem,
    Testbench,
    TestbenchFolder,
    TestbenchItem,
)


def test_testbench_basic():
    tb = Testbench(id=1, name="my tb", description=None, archived=False,
                   created_at="2026-01-01", updated_at="2026-01-01")
    assert tb.archived is False


def test_testbench_item_upload_kind_requires_upload_path():
    with pytest.raises(ValueError):
        TestbenchItem(id=1, folder_id=1, source_kind="upload",
                      upload_path=None, upload_orig_name=None,
                      catdv_provider_clip_id=None, display_name="x",
                      gold_json=None, sort_index=0, created_at="2026-01-01")


def test_testbench_item_catdv_kind_requires_provider_clip_id():
    with pytest.raises(ValueError):
        TestbenchItem(id=1, folder_id=1, source_kind="catdv_clip",
                      upload_path=None, upload_orig_name=None,
                      catdv_provider_clip_id=None, display_name="x",
                      gold_json=None, sort_index=0, created_at="2026-01-01")


def test_studio_run_states():
    for s in ("pending", "running", "completed", "failed", "cancelled"):
        StudioRun(id=1, testbench_id=1, prompt_version_id=1, status=s,
                  created_at="2026-01-01", started_at=None, finished_at=None, notes=None)
    with pytest.raises(ValueError):
        StudioRun(id=1, testbench_id=1, prompt_version_id=1, status="bogus",
                  created_at="2026-01-01", started_at=None, finished_at=None, notes=None)


def test_studio_run_item_unacceptable_state_allowed():
    StudioRunItem(id=1, run_id=1, testbench_item_id=1, status="unacceptable",
                  error=None, unacceptable_reason="catdv offline; no cache",
                  structured_json=None, raw_text=None, prompt_used=None,
                  model=None, latency_ms=None, started_at=None, finished_at=None)


def test_annotation_output_dataclass():
    out = AnnotationOutput(
        structured={"k": "v"}, raw_text='{"k":"v"}',
        prompt_used="rendered prompt body", model="gemini-x", latency_ms=1234,
    )
    assert out.structured == {"k": "v"}
    assert out.latency_ms == 1234
