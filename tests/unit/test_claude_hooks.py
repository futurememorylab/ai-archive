"""Tests for the agent-side hooks in .claude/hooks/ (ADR 0129, spec section E).

The hooks are stdlib scripts Claude Code runs with JSON on stdin; exit code 2
blocks the tool call (protect_gate_files) or the session stop (stop_gate).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOKS = REPO_ROOT / ".claude" / "hooks"
PROTECT = HOOKS / "protect_gate_files.py"
STOP = HOOKS / "stop_gate.py"


def _hook(script: Path, event: dict, project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        env={"CLAUDE_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin"},
    )


def _write(rel: str, content: str = "x") -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": rel, "content": content}}


def test_every_codeowners_path_is_blocked():
    for rel in (
        ".erosion-baseline.json",
        ".github/workflows/ci.yml",
        ".github/CODEOWNERS",
        ".claude/hooks/stop_gate.py",
        "tests/unit/test_no_x_data_stack.py",
        "tests/unit/test_write_lock_guard.py",
        "AGENTS.md",
        "pyproject.toml",
    ):
        proc = _hook(PROTECT, _write(str(REPO_ROOT / rel)), REPO_ROOT)
        assert proc.returncode == 2, rel
        assert rel in proc.stderr
        assert "ask Peter" in proc.stderr


def test_ordinary_files_are_allowed():
    for rel in ("backend/app/main.py", "docs/ARCHITECTURE.md", "tests/unit/test_erosion_gate.py"):
        assert _hook(PROTECT, _write(str(REPO_ROOT / rel)), REPO_ROOT).returncode == 0, rel


def _project_with_baseline(tmp_path: Path) -> Path:
    (tmp_path / ".github").mkdir()
    shutil.copy(REPO_ROOT / ".github" / "CODEOWNERS", tmp_path / ".github" / "CODEOWNERS")
    (tmp_path / ".erosion-baseline.json").write_text(
        json.dumps({"erosion": 0.42, "max_cc": 29}, indent=2) + "\n"
    )
    return tmp_path


def _edit_baseline(project: Path, old: str, new: str) -> subprocess.CompletedProcess[str]:
    event = {
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(project / ".erosion-baseline.json"),
            "old_string": old,
            "new_string": new,
        },
    }
    return _hook(PROTECT, event, project)


def test_lowering_the_erosion_baseline_is_allowed(tmp_path: Path):
    project = _project_with_baseline(tmp_path)
    assert _edit_baseline(project, '"max_cc": 29', '"max_cc": 26').returncode == 0
    assert _edit_baseline(project, '"erosion": 0.42', '"erosion": 0.41').returncode == 0


def test_raising_the_erosion_baseline_is_blocked(tmp_path: Path):
    project = _project_with_baseline(tmp_path)
    assert _edit_baseline(project, '"max_cc": 29', '"max_cc": 31').returncode == 2
    assert _edit_baseline(project, '"erosion": 0.42', '"erosion": 0.5').returncode == 2


def test_rewriting_the_baseline_with_other_keys_is_blocked(tmp_path: Path):
    project = _project_with_baseline(tmp_path)
    event = _write(str(project / ".erosion-baseline.json"), '{"erosion": 0.1}')
    assert _hook(PROTECT, event, project).returncode == 2


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    return repo


def test_stop_gate_does_nothing_without_python_changes(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "README").write_text("changed\n")
    proc = _hook(STOP, {"stop_hook_active": False}, repo)
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""


def test_stop_gate_respects_reentry_flag(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "pkg" / "bad.py").write_text("import os\n")
    assert _hook(STOP, {"stop_hook_active": True}, repo).returncode == 0


def test_stop_gate_blocks_on_failing_gate(tmp_path: Path):
    repo = _repo(tmp_path)
    (repo / "pkg" / "bad.py").write_text("import os\n")  # F401: unused import
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    ruff = shutil.which("ruff") or str(Path(sys.executable).parent / "ruff")
    (venv_bin / "ruff").symlink_to(ruff)
    (venv_bin / "python").symlink_to(sys.executable)
    proc = _hook(STOP, {"stop_hook_active": False}, repo)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "ruff check" in proc.stderr
    assert "F401" in proc.stderr


def _project_with_frontend_baseline(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / ".github").mkdir(parents=True)
    (project / ".github" / "CODEOWNERS").write_text("/.frontend-size-baseline.json @peterhora\n")
    baseline = {"files": {"a.js": 575}, "functions": {"a.js::f": 94, "b.js::g": 83}}
    (project / ".frontend-size-baseline.json").write_text(json.dumps(baseline))
    return project


def _write_frontend_baseline(project: Path, data: dict) -> subprocess.CompletedProcess[str]:
    event = _write(str(project / ".frontend-size-baseline.json"), json.dumps(data))
    return _hook(PROTECT, event, project)


def test_lowering_the_frontend_baseline_or_dropping_entries_is_allowed(tmp_path: Path):
    project = _project_with_frontend_baseline(tmp_path)
    lowered = {"files": {"a.js": 560}, "functions": {"a.js::f": 90}}
    assert _write_frontend_baseline(project, lowered).returncode == 0


def test_raising_or_adding_to_the_frontend_baseline_is_blocked(tmp_path: Path):
    project = _project_with_frontend_baseline(tmp_path)
    raised = {"files": {"a.js": 600}, "functions": {"a.js::f": 94, "b.js::g": 83}}
    added = {"files": {"a.js": 575}, "functions": {"a.js::f": 94, "c.js::h": 81}}
    assert _write_frontend_baseline(project, raised).returncode == 2
    assert _write_frontend_baseline(project, added).returncode == 2
