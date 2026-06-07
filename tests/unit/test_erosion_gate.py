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
