"""Tests for the archive registry's FS branch + the FS-related Settings validator."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.app.archive.providers.fs.adapter import FilesystemArchiveProvider
from backend.app.archive.registry import build_archive_provider


def test_registry_builds_fs_provider(tmp_path: Path):
    settings = SimpleNamespace(
        archive_provider="fs",
        fs_root=tmp_path,
        fs_media_exts=".mov,.mp4",
    )
    p = build_archive_provider(settings)
    assert isinstance(p, FilesystemArchiveProvider)
    assert p.id == "fs"


def test_registry_rejects_fs_without_fs_root():
    settings = SimpleNamespace(archive_provider="fs", fs_root=None)
    with pytest.raises(ValueError):
        build_archive_provider(settings)


def test_registry_rejects_fs_with_empty_fs_root():
    settings = SimpleNamespace(archive_provider="fs", fs_root="")
    with pytest.raises(ValueError):
        build_archive_provider(settings)


def test_registry_unknown_provider_raises():
    settings = SimpleNamespace(archive_provider="resourcespace")
    with pytest.raises(ValueError):
        build_archive_provider(settings)


def test_registry_accepts_list_exts(tmp_path: Path):
    settings = SimpleNamespace(
        archive_provider="fs",
        fs_root=tmp_path,
        fs_media_exts=[".mov", ".mp4"],
    )
    p = build_archive_provider(settings)
    assert isinstance(p, FilesystemArchiveProvider)


def test_settings_validator_requires_fs_root_when_fs(monkeypatch, tmp_path: Path):
    # Stand up an isolated env so .env on the repo doesn't leak in.
    env_keys = [
        "ARCHIVE_PROVIDER",
        "CATDV_BASE_URL",
        "CATDV_CATALOG_ID",
        "GCP_PROJECT_ID",
        "GCS_BUCKET_NAME",
        "FS_ROOT",
    ]
    for k in env_keys:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ARCHIVE_PROVIDER", "fs")
    monkeypatch.setenv("CATDV_BASE_URL", "http://x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    # Disable .env loading by chdir to tmp_path so pydantic-settings can't find one.
    monkeypatch.chdir(tmp_path)

    # Import fresh so the new env applies.
    from backend.app.settings import Settings
    with pytest.raises(ValidationError):
        Settings()


def test_settings_validator_accepts_fs_with_fs_root(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARCHIVE_PROVIDER", "fs")
    monkeypatch.setenv("FS_ROOT", str(tmp_path))
    monkeypatch.setenv("CATDV_BASE_URL", "http://x")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.chdir(tmp_path)

    from backend.app.settings import Settings
    s = Settings()
    assert s.archive_provider == "fs"
    assert s.fs_root == tmp_path
