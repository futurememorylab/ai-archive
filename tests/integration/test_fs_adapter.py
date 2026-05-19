"""Tests for backend.app.archive.providers.fs.adapter (FilesystemArchiveProvider)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.archive.errors import FatalProviderError
from backend.app.archive.model import (
    AddMarkers,
    AppendNote,
    ChangeSet,
    ClipQuery,
    Marker,
    ReplaceNote,
    SetField,
    Timecode,
)
from backend.app.archive.providers.fs import media_probe
from backend.app.archive.providers.fs.adapter import FilesystemArchiveProvider


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "fs_archive"


@pytest.fixture(autouse=True)
def _stub_ffprobe(monkeypatch):
    """Default: behave as if ffprobe is missing so tests are deterministic."""
    media_probe.reset_warning_flag()
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: None)


def _make_tmp_archive(tmp_path: Path) -> Path:
    cat = tmp_path / "cat_a"
    cat.mkdir(parents=True)
    (cat / "c1.mov").write_bytes(b"")
    (cat / "c2.mov").write_bytes(b"")
    # nested
    nested = cat / "nested"
    nested.mkdir()
    (nested / "c3.mp4").write_bytes(b"")
    other = tmp_path / "cat_b"
    other.mkdir()
    (other / "x.mov").write_bytes(b"")
    # hidden + archive metadata dir
    (tmp_path / ".hidden").mkdir()
    archive_meta = tmp_path / ".archive"
    archive_meta.mkdir()
    return tmp_path


@pytest.mark.asyncio
async def test_list_catalogs_excludes_hidden_and_dotarchive(tmp_path: Path):
    root = _make_tmp_archive(tmp_path)
    p = FilesystemArchiveProvider(fs_root=root)
    catalogs = await p.list_catalogs()
    assert [c["id"] for c in catalogs] == ["cat_a", "cat_b"]


@pytest.mark.asyncio
async def test_list_clips_walks_recursively_and_filters(tmp_path: Path):
    root = _make_tmp_archive(tmp_path)
    p = FilesystemArchiveProvider(fs_root=root)
    page = await p.list_clips("cat_a", ClipQuery(limit=10))
    names = sorted(c.name for c in page.items)
    assert names == ["c1", "c2", "c3"]
    assert page.total == 3

    filtered = await p.list_clips("cat_a", ClipQuery(text="c2", limit=10))
    assert {c.name for c in filtered.items} == {"c2"}


@pytest.mark.asyncio
async def test_list_clips_honours_offset_limit(tmp_path: Path):
    root = _make_tmp_archive(tmp_path)
    p = FilesystemArchiveProvider(fs_root=root)
    page = await p.list_clips("cat_a", ClipQuery(offset=1, limit=1))
    assert len(page.items) == 1
    assert page.total == 3


@pytest.mark.asyncio
async def test_get_clip_returns_canonical_clip_from_sidecar():
    p = FilesystemArchiveProvider(fs_root=FIXTURE_ROOT)
    clip = await p.get_clip("archive_30s/clip001")
    assert clip.key == ("fs", "archive_30s/clip001")
    assert clip.name == "clip001"
    assert clip.fps == 25.0
    assert "pragafilm.dekáda.natočení" in clip.fields
    assert clip.fields["pragafilm.dekáda.natočení"].value == "30.léta"
    assert len(clip.markers) == 1
    assert clip.markers[0].name == "intro"
    assert clip.notes["notes"] == "Test clip"
    # provider_data captures ffprobe presence diagnostic
    assert "ffprobe_present" in clip.provider_data


@pytest.mark.asyncio
async def test_get_clip_missing_media_raises(tmp_path: Path):
    (tmp_path / "cat").mkdir()
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    with pytest.raises(FatalProviderError):
        await p.get_clip("cat/missing")


@pytest.mark.asyncio
async def test_get_clip_for_clip_without_sidecar(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "lonely.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    clip = await p.get_clip("c/lonely")
    assert clip.name == "lonely"
    assert clip.markers == ()
    assert clip.fields == {}
    assert clip.notes == {}


@pytest.mark.asyncio
async def test_capabilities_are_fs_shaped(tmp_path: Path):
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    caps = p.capabilities
    assert caps.supports_markers is True
    assert caps.supports_etag is True
    assert caps.media_is_local is True
    assert caps.write_atomicity == "per-clip"
    assert "notes" in caps.supports_notes


@pytest.mark.asyncio
async def test_health_ok(tmp_path: Path):
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    h = await p.health()
    assert h.ok is True
    assert h.latency_ms is not None


@pytest.mark.asyncio
async def test_health_fails_when_root_missing(tmp_path: Path):
    p = FilesystemArchiveProvider(fs_root=tmp_path / "nope")
    h = await p.health()
    assert h.ok is False


@pytest.mark.asyncio
async def test_health_fails_when_fields_file_unreadable(tmp_path: Path, monkeypatch):
    (tmp_path / ".archive").mkdir()
    f = tmp_path / ".archive" / "fields.json"
    f.write_text("[]")
    p = FilesystemArchiveProvider(fs_root=tmp_path)

    def bad_read(*_a, **_kw):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "read_text", bad_read)
    h = await p.health()
    assert h.ok is False


@pytest.mark.asyncio
async def test_list_field_definitions_reads_archive_metadata(tmp_path: Path):
    p = FilesystemArchiveProvider(fs_root=FIXTURE_ROOT)
    defs = await p.list_field_definitions()
    assert any(d.identifier == "pragafilm.dekáda.natočení" for d in defs)


@pytest.mark.asyncio
async def test_list_field_definitions_empty_when_missing(tmp_path: Path):
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    assert await p.list_field_definitions() == []


# --- write API --------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_changes_set_field_round_trips_to_sidecar(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    cs = ChangeSet(
        clip_key=("fs", "c/v"),
        ops=(SetField(identifier="x", value="42"),),
    )
    result = await p.apply_changes(cs)
    assert result.status == "ok"
    assert result.new_etag is not None
    sidecar = json.loads((cat / "v.annot.json").read_text())
    assert sidecar["fields"]["x"]["value"] == "42"


@pytest.mark.asyncio
async def test_apply_changes_add_markers_is_additive(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    (cat / "v.annot.json").write_text(json.dumps({
        "markers": [{"name": "first", "in": {"secs": 0.0, "frm": 0, "fps": 25.0}, "out": None}],
        "fields": {},
        "notes": {},
        "provider_data": {},
    }))
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    cs = ChangeSet(
        clip_key=("fs", "c/v"),
        ops=(
            AddMarkers(markers=(Marker(name="m2", in_=Timecode(secs=1.0, fps=25.0), out=None),)),
        ),
    )
    result = await p.apply_changes(cs)
    assert result.status == "ok"
    clip = await p.get_clip("c/v")
    names = sorted(m.name for m in clip.markers)
    assert names == ["first", "m2"]


@pytest.mark.asyncio
async def test_apply_changes_append_note_concatenates(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    for text in ("alpha", "beta"):
        await p.apply_changes(
            ChangeSet(
                clip_key=("fs", "c/v"),
                ops=(AppendNote(target="notes", text=text),),
            )
        )
    clip = await p.get_clip("c/v")
    assert clip.notes["notes"] == "alpha\nbeta"


@pytest.mark.asyncio
async def test_apply_changes_replace_note_overwrites(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    await p.apply_changes(
        ChangeSet(
            clip_key=("fs", "c/v"),
            ops=(AppendNote(target="notes", text="alpha"),),
        )
    )
    await p.apply_changes(
        ChangeSet(
            clip_key=("fs", "c/v"),
            ops=(ReplaceNote(target="notes", text="OMEGA"),),
        )
    )
    clip = await p.get_clip("c/v")
    assert clip.notes["notes"] == "OMEGA"


@pytest.mark.asyncio
async def test_apply_changes_stale_etag_returns_conflict(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    # First write to populate sidecar + get a real etag.
    r1 = await p.apply_changes(
        ChangeSet(
            clip_key=("fs", "c/v"),
            ops=(SetField(identifier="x", value=1),),
        )
    )
    real_etag = r1.new_etag

    # Stale etag triggers conflict.
    r2 = await p.apply_changes(
        ChangeSet(
            clip_key=("fs", "c/v"),
            ops=(SetField(identifier="x", value=2),),
            expected_etag="deadbeef",
        )
    )
    assert r2.status == "conflict"
    assert r2.conflict_detail is not None
    assert r2.conflict_detail.actual_etag == real_etag

    # Sidecar untouched.
    sidecar = json.loads((cat / "v.annot.json").read_text())
    assert sidecar["fields"]["x"]["value"] == 1


@pytest.mark.asyncio
async def test_apply_changes_rejects_foreign_provider(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    cs = ChangeSet(
        clip_key=("catdv", "c/v"),
        ops=(SetField(identifier="x", value=1),),
    )
    with pytest.raises(FatalProviderError):
        await p.apply_changes(cs)


@pytest.mark.asyncio
async def test_apply_changes_etag_changes_after_write(tmp_path: Path):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    r1 = await p.apply_changes(
        ChangeSet(clip_key=("fs", "c/v"), ops=(SetField(identifier="a", value=1),))
    )
    r2 = await p.apply_changes(
        ChangeSet(
            clip_key=("fs", "c/v"),
            ops=(SetField(identifier="a", value=2),),
            expected_etag=r1.new_etag,
        )
    )
    assert r2.status == "ok"
    assert r2.new_etag != r1.new_etag
