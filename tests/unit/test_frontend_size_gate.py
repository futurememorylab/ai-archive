"""Tests for tools/frontend_size_gate.py (the static/*.js size ratchet, ADR 0129)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "frontend_size_gate", REPO_ROOT / "tools" / "frontend_size_gate.py"
)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules["frontend_size_gate"] = gate
_spec.loader.exec_module(gate)


def _body(n: int) -> str:
    return "".join(f"  x{i} = {i};\n" for i in range(n))


def _fn(name: str, body_lines: int) -> str:
    return f"function {name}() {{\n{_body(body_lines)}}}\n"


def test_function_length_counts_header_and_closing_brace():
    (f,) = gate.functions_in_source(_fn("small", 3))
    assert (f.name, f.line, f.own_lines) == ("small", 1, 5)


def test_nested_functions_do_not_count_toward_their_parent():
    src = "function outer() {\n  const a = 1;\n  inner() {\n" + _body(50) + "  },\n}\n"
    by_name = {f.name: f.own_lines for f in gate.functions_in_source(src)}
    assert by_name["inner"] == 52
    assert by_name["outer"] == 3  # its header, `const a` and its closing brace


def test_braces_in_strings_and_comments_are_ignored():
    src = 'function f() {\n  const s = "}}}";\n  // } {\n  /* { */\n  return `${s}`;\n}\n'
    (f,) = gate.functions_in_source(src)
    assert f.own_lines == 6


def test_arrow_and_method_forms_are_found():
    src = (
        "const a = (x) => {\n  return x;\n};\n"
        "const obj = {\n  async load(id) {\n    return id;\n  },\n};\n"
    )
    names = {f.name for f in gate.functions_in_source(src)}
    assert {"a", "load"} <= names


def test_anonymous_callback_is_named_after_its_call():
    src = 'document.addEventListener("alpine:init", () => {\n  go();\n});\n'
    (f,) = gate.functions_in_source(src)
    assert f.name == "addEventListener(alpine:init)"


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "static"
    root.mkdir()
    for name, src in files.items():
        (root / name).write_text(src)
    return root


def _run(root: Path, baseline: Path, *extra: str) -> int:
    return gate.main(["--path", str(root), "--baseline", str(baseline), *extra])


def test_new_long_function_fails_and_short_one_passes(tmp_path: Path):
    baseline = tmp_path / "b.json"
    assert _run(_tree(tmp_path, {"a.js": _fn("ok", 70)}), baseline) == 0
    root2 = tmp_path / "two"
    root2.mkdir()
    assert _run(_tree(root2, {"a.js": _fn("long", 90)}), baseline) == 1


def test_file_over_limit_fails_without_baseline(tmp_path: Path):
    src = "".join(_fn(f"f{i}", 10) for i in range(50))  # 600 lines
    assert _run(_tree(tmp_path, {"big.js": src}), tmp_path / "b.json") == 1


def test_baselined_function_may_not_grow_but_may_shrink_a_little(tmp_path: Path):
    root = _tree(tmp_path, {"a.js": _fn("long", 90)})
    baseline = tmp_path / "b.json"
    assert _run(root, baseline, "--write-baseline") == 0  # first baseline records what exists
    assert json.loads(baseline.read_text())["functions"] == {f"{root.as_posix()}/a.js::long": 92}
    assert _run(root, baseline) == 0
    (root / "a.js").write_text(_fn("long", 91))
    assert _run(root, baseline) == 1
    (root / "a.js").write_text(_fn("long", 85))
    assert _run(root, baseline) == 0  # within the stale slack


def test_big_shrink_makes_the_baseline_stale(tmp_path: Path):
    root = _tree(tmp_path, {"a.js": _fn("long", 90)})
    baseline = tmp_path / "b.json"
    assert _run(root, baseline, "--write-baseline") == 0
    (root / "a.js").write_text(_fn("long", 40))
    assert _run(root, baseline) == 1
    assert _run(root, baseline, "--write-baseline") == 0
    assert json.loads(baseline.read_text())["functions"] == {}
    assert _run(root, baseline) == 0


def test_write_baseline_refuses_to_raise_unless_allowed(tmp_path: Path):
    root = _tree(tmp_path, {"a.js": _fn("long", 90)})
    baseline = tmp_path / "b.json"
    assert _run(root, baseline, "--write-baseline") == 0
    (root / "a.js").write_text(_fn("long", 100))
    assert _run(root, baseline, "--write-baseline") == 1
    assert _run(root, baseline, "--write-baseline", "--allow-raise") == 0
    assert _run(root, baseline) == 0


def test_repo_static_js_passes_its_baseline(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)  # baseline keys are repo-relative
    current = gate.measure(Path(gate.DEFAULT_ROOT))
    baseline = json.loads((REPO_ROOT / ".frontend-size-baseline.json").read_text())
    assert gate.evaluate(current, baseline) == []
