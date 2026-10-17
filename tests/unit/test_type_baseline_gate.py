"""Unit tests for the basedpyright baseline ratchet (tools/type_baseline_gate.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.type_baseline_gate import count_entries

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "type_baseline_gate.py"


def _baseline(n: int) -> str:
    return json.dumps({"files": {"./a.py": [{"code": "reportX"}] * n, "./b.py": []}})


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path, base_entries: int, head_entries: int) -> Path:
    repo = tmp_path / "repo"
    (repo / ".basedpyright").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    target = repo / ".basedpyright" / "baseline.json"
    target.write_text(_baseline(base_entries))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "feature")
    target.write_text(_baseline(head_entries))
    _git(repo, "commit", "-qam", "change")
    return repo


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--base-ref", "main"],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )


def test_count_entries():
    assert count_entries(_baseline(3)) == 3


def test_grown_baseline_fails(tmp_path: Path):
    proc = _run(_repo(tmp_path, 2, 3))
    assert proc.returncode == 1
    assert "grew from 2 to 3" in proc.stderr


def test_shrunk_or_equal_baseline_passes(tmp_path: Path):
    proc = _run(_repo(tmp_path, 3, 2))
    assert proc.returncode == 0, proc.stderr
    assert "3 at merge-base, 2 now" in proc.stdout
