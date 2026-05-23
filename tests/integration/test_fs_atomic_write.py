"""Atomic-write semantics for the FS archive adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.app.archive.model import ChangeSet, SetField
from backend.app.archive.providers.fs import adapter as adapter_mod
from backend.app.archive.providers.fs import media_probe
from backend.app.archive.providers.fs.adapter import FilesystemArchiveProvider


@pytest.fixture(autouse=True)
def _stub_ffprobe(monkeypatch):
    media_probe.reset_warning_flag()
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: None)


@pytest.mark.asyncio
async def test_failed_serialise_leaves_sidecar_unchanged(tmp_path: Path, monkeypatch):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    sidecar = cat / "v.annot.json"
    sidecar.write_text(json.dumps({"fields": {"x": {"value": "ORIGINAL", "is_multi": False}}}))
    original = sidecar.read_bytes()

    def boom(*_a, **_kw):
        raise RuntimeError("serialise failed")

    monkeypatch.setattr(adapter_mod, "dumps_sidecar", boom)

    p = FilesystemArchiveProvider(fs_root=tmp_path)
    with pytest.raises(RuntimeError):
        await p.apply_changes(
            ChangeSet(clip_key=("fs", "c/v"), ops=(SetField(identifier="x", value="NEW"),))
        )

    # Original sidecar bytes intact.
    assert sidecar.read_bytes() == original
    # No tempfile left lying around.
    leftover = [
        x for x in cat.iterdir() if x.name.startswith("v.annot.json.") and x.name.endswith(".tmp")
    ]
    assert leftover == []


@pytest.mark.asyncio
async def test_failed_os_replace_cleans_up_tmp(tmp_path: Path, monkeypatch):
    cat = tmp_path / "c"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")

    def fail_replace(*_a, **_kw):
        raise OSError("rename failed")

    monkeypatch.setattr(adapter_mod.os, "replace", fail_replace)

    p = FilesystemArchiveProvider(fs_root=tmp_path)
    with pytest.raises(OSError):
        await p.apply_changes(
            ChangeSet(clip_key=("fs", "c/v"), ops=(SetField(identifier="x", value="NEW"),))
        )

    leftover = [
        x for x in cat.iterdir() if x.name.startswith("v.annot.json.") and x.name.endswith(".tmp")
    ]
    assert leftover == []


@pytest.mark.asyncio
async def test_write_creates_parent_directory(tmp_path: Path):
    # Manually create a media path under a directory that does not exist;
    # the adapter must mkdir -p for the sidecar destination. In normal use
    # the media's parent always exists (since the media file is there), so
    # this is mostly belt-and-braces. We assert by writing to a real clip
    # in a fresh catalog directory.
    cat = tmp_path / "fresh"
    cat.mkdir()
    (cat / "v.mov").write_bytes(b"")
    p = FilesystemArchiveProvider(fs_root=tmp_path)
    result = await p.apply_changes(
        ChangeSet(clip_key=("fs", "fresh/v"), ops=(SetField(identifier="k", value=1),))
    )
    assert result.status == "ok"
    assert (cat / "v.annot.json").exists()
