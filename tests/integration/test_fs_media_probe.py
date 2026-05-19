"""Tests for backend.app.archive.providers.fs.media_probe."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from backend.app.archive.providers.fs import media_probe


def test_probe_returns_defaults_when_ffprobe_missing(tmp_path: Path, monkeypatch, caplog):
    media_probe.reset_warning_flag()
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: None)
    media = tmp_path / "x.mov"
    media.write_bytes(b"")
    with caplog.at_level("WARNING"):
        duration, fps = media_probe.probe(media)
    assert duration == 0.0
    assert fps == 25.0
    assert any("ffprobe not found" in r.message for r in caplog.records)


def test_probe_returns_defaults_when_ffprobe_missing_warns_only_once(monkeypatch, caplog):
    media_probe.reset_warning_flag()
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: None)
    with caplog.at_level("WARNING"):
        media_probe.probe(Path("/tmp/a.mov"))
        media_probe.probe(Path("/tmp/b.mov"))
    warnings = [r for r in caplog.records if "ffprobe not found" in r.message]
    assert len(warnings) == 1


def test_probe_parses_ffprobe_json(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")
    payload = {
        "streams": [{"r_frame_rate": "30000/1001", "duration": "12.345"}],
        "format": {"duration": "12.345"},
    }

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(payload), stderr=""
        )

    monkeypatch.setattr(media_probe.subprocess, "run", fake_run)
    duration, fps = media_probe.probe(tmp_path / "x.mov")
    assert abs(duration - 12.345) < 1e-6
    assert abs(fps - (30000.0 / 1001.0)) < 1e-3


def test_probe_returns_defaults_on_subprocess_error(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    def fake_run(*_args, **_kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(media_probe.subprocess, "run", fake_run)
    assert media_probe.probe(tmp_path / "x.mov") == (0.0, 25.0)


def test_probe_returns_defaults_on_malformed_json(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not json", stderr=""
        )

    monkeypatch.setattr(media_probe.subprocess, "run", fake_run)
    assert media_probe.probe(tmp_path / "x.mov") == (0.0, 25.0)


def test_probe_returns_defaults_on_nonzero_exit(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(media_probe.shutil, "which", lambda _name: "/usr/bin/ffprobe")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="err"
        )

    monkeypatch.setattr(media_probe.subprocess, "run", fake_run)
    assert media_probe.probe(tmp_path / "x.mov") == (0.0, 25.0)
