"""Unit tests for the structural-erosion gate (tools/erosion_gate.py).

See docs/specs/2026-06-07-erosion-detection-ci-design.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.erosion_gate import (
    CallableInfo,
    callables_in_source,
    erosion_stats,
    evaluate,
    mass,
    touched_regressions,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "tools" / "erosion_gate.py"


def _ci(complexity: int, sloc: int, name: str = "f") -> CallableInfo:
    return CallableInfo(
        path="<t>",
        name=name,
        lineno=1,
        classname=None,
        complexity=complexity,
        sloc=sloc,
    )


def test_mass_formula():
    assert mass(4, 9) == 12.0  # 4 * sqrt(9)


def test_erosion_ratio_on_known_callables():
    # trivial mass = 2*sqrt(4) = 4 ; complex mass = 12*sqrt(9) = 36
    # total = 40, eroded (CC>10) = 36 -> 0.9
    erosion, max_cc = erosion_stats([_ci(2, 4), _ci(12, 9)])
    assert erosion == 0.9
    assert max_cc == 12


def test_methods_and_closures_counted_once():
    # radon.cc_visit lists each method twice (top-level + Class.methods);
    # the gate must dedupe so the ratio is not skewed.
    src = (
        "class Foo:\n"
        "    def a(self):\n"
        "        if 1:\n"
        "            pass\n"
        "    def b(self):\n"
        "        for x in []:\n"
        "            pass\n"
        "def top():\n"
        "    def inner():\n"
        "        pass\n"
        "    return inner\n"
    )
    cs = callables_in_source(src, "m.py")
    keys = [(c.path, c.name, c.lineno) for c in cs]
    assert len(keys) == len(set(keys))  # no duplicate (path,name,lineno)
    assert sorted(c.name for c in cs) == ["a", "b", "inner", "top"]


def test_ratchet_passes_within_tolerance():
    assert evaluate(0.412, 25, baseline=0.410, tolerance=0.005, max_cc_cap=30) == []


def test_ratchet_fails_above_tolerance():
    failures = evaluate(0.420, 25, baseline=0.410, tolerance=0.005, max_cc_cap=30)
    assert len(failures) == 1
    assert "exceeds baseline" in failures[0]


def test_max_cc_gate_fails_over_cap():
    failures = evaluate(0.400, 31, baseline=0.410, tolerance=0.005, max_cc_cap=30)
    assert len(failures) == 1
    assert "CC 31 > cap 30" in failures[0]


def test_write_baseline_roundtrip(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text("def f():\n    return 1\n")
    out = tmp_path / "baseline.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--path",
            str(pkg),
            "--baseline",
            str(out),
            "--write-baseline",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(out.read_text())
    assert set(data) == {"erosion", "max_cc"}


def test_exit_1_when_max_cc_exceeded(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    body = (
        "def f(x):\n"
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
        + "    return 0\n"
    )
    (pkg / "m.py").write_text(body)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(pkg), "--max-cc", "5"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cap 5" in proc.stderr


def test_exit_2_on_missing_path(tmp_path: Path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(tmp_path / "nope")],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 2


# --- Tightening ratchets (ADR 0129, quality-gates hardening spec section C) ---


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _branchy(name: str, branches: int) -> str:
    return (
        f"def {name}(x):\n"
        + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(branches))
        + "    return 0\n"
    )


def test_stale_baseline_fails_with_rebaseline_hint():
    failures = evaluate(
        0.420, 25, baseline=0.430, tolerance=0.005, max_cc_cap=30, stale_slack=0.003
    )
    assert len(failures) == 1
    assert "stale" in failures[0]
    assert "--write-baseline" in failures[0]


def test_small_improvement_within_stale_slack_passes():
    assert (
        evaluate(0.428, 25, baseline=0.430, tolerance=0.005, max_cc_cap=30, stale_slack=0.003) == []
    )


def test_max_cc_above_baseline_fails():
    failures = evaluate(
        0.430, 27, baseline=0.430, tolerance=0.005, max_cc_cap=30, baseline_max_cc=26
    )
    assert failures == ["max function CC 27 exceeds baseline max_cc 26"]


def test_max_cc_below_baseline_is_stale():
    failures = evaluate(
        0.430, 25, baseline=0.430, tolerance=0.005, max_cc_cap=30, baseline_max_cc=26
    )
    assert len(failures) == 1
    assert "stale" in failures[0]


def test_touched_regressions_names_function_and_both_ccs():
    before = callables_in_source(_branchy("f", 3) + _branchy("g", 2), "m.py")
    after = callables_in_source(_branchy("f", 5) + _branchy("g", 1), "m.py")
    msgs = touched_regressions(before, after)
    assert len(msgs) == 1
    assert "m.py::f CC rose 4 -> 6" in msgs[0]


def test_touched_regressions_ignore_new_functions():
    before = callables_in_source(_branchy("f", 3), "m.py")
    after = callables_in_source(_branchy("f", 3) + _branchy("h", 20), "m.py")
    assert touched_regressions(before, after) == []


def test_touched_function_rule_against_merge_base(tmp_path: Path):
    repo = tmp_path / "repo"
    pkg = repo / "pkg"
    pkg.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (pkg / "m.py").write_text(_branchy("f", 3))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "feature")
    (pkg / "m.py").write_text(_branchy("f", 6))
    _git(repo, "commit", "-qam", "more branches")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", "pkg", "--base-ref", "main"],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "pkg/m.py::f CC rose 4 -> 7" in proc.stderr


def test_unknown_base_ref_fails_loudly(tmp_path: Path):
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / "pkg" / "m.py").write_text("def f():\n    return 1\n")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--path", "pkg", "--base-ref", "nope"],
        capture_output=True,
        text=True,
        cwd=str(repo),
    )
    assert proc.returncode == 2


def test_write_baseline_refuses_to_raise(tmp_path: Path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text(_branchy("f", 12))
    out = tmp_path / "baseline.json"
    out.write_text(json.dumps({"erosion": 0.1, "max_cc": 5}))
    cmd = [sys.executable, str(SCRIPT), "--path", str(pkg), "--baseline", str(out)]

    proc = subprocess.run([*cmd, "--write-baseline"], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "refusing to raise" in proc.stderr
    assert json.loads(out.read_text()) == {"erosion": 0.1, "max_cc": 5}

    proc = subprocess.run(
        [*cmd, "--write-baseline", "--allow-raise"], capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(out.read_text())["max_cc"] == 13
