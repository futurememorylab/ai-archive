"""Tests for tools/decision_trigger_gate.py (decision-sensitive diffs need an ADR or a reason)."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from tools import decision_trigger_gate as gate

TRIGGERS = """
[[trigger]]
name = "new migration"
why = "schema"
kind = "added"
paths = ["backend/migrations/*"]

[[trigger]]
name = "contracts"
why = "layers"
kind = "modified"
paths = [".importlinter"]

[[trigger]]
name = "models"
why = "catalog"
kind = "lines"
paths = ["registry.py"]
pattern = 'gemini-'

[[trigger]]
name = "session"
why = "seat"
kind = "symbols"
paths = ["client.py"]
symbols = ["Client.login"]

[[trigger]]
name = "deps"
why = "deps"
kind = "toml_keys"
paths = ["pyproject.toml"]
keys = ["project.dependencies"]

[[trigger]]
name = "routes"
why = "contract"
kind = "removed_routes"
paths = ["routes/*.py"]
"""

BASE_FILES = {
    "backend/migrations/0001_init.sql": "create table a(x);\n",
    ".importlinter": "[importlinter]\n",
    "registry.py": 'MODELS = ["gemini-1"]\nOTHER = 1\n',
    "client.py": "class Client:\n    def login(self):\n        return 1\n\n"
    "    def other(self):\n        return 2\n",
    "pyproject.toml": '[project]\nname = "x"\nversion = "1"\ndependencies = ["a"]\n',
    "routes/a.py": 'router = APIRouter(prefix="/api/a")\n\n@router.get("/x")\ndef x(): ...\n',
    "README.md": "hi\n",
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A git repo with `main` at BASE_FILES and a checked-out `feature` branch."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    for rel, text in BASE_FILES.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    _git(tmp_path, "checkout", "-qb", "feature")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "triggers.toml").write_text(TRIGGERS)
    return tmp_path


def _commit(repo: Path, files: dict[str, str | None]) -> None:
    for rel, text in files.items():
        path = repo / rel
        if text is None:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")


def _fired(repo: Path) -> dict[str, list[str]]:
    r = gate.Repo("main")
    return gate.fired(gate.load_triggers(repo / "triggers.toml"), r, r.changes())


def _names(repo: Path) -> set[str]:
    return {k.split(" (")[0] for k in _fired(repo)}


def _run(repo: Path, body: str = "") -> int:
    (repo / "body.md").write_text(body)
    return gate.main(
        ["--base-ref", "main", "--triggers", str(repo / "triggers.toml"), "--pr-body-file",
         str(repo / "body.md")]
    )  # fmt: skip


def test_unrelated_change_fires_nothing(repo):
    _commit(repo, {"README.md": "changed\n"})
    assert _fired(repo) == {}
    assert _run(repo) == 0


def test_new_migration_fires_but_editing_one_does_not(repo):
    _commit(repo, {"backend/migrations/0001_init.sql": "create table a(y);\n"})
    assert _names(repo) == set()
    _commit(repo, {"backend/migrations/0002_more.sql": "alter table a;\n"})
    assert _names(repo) == {"new migration"}


def test_modified_kind_fires_on_any_change(repo):
    _commit(repo, {".importlinter": None})
    assert _names(repo) == {"contracts"}


def test_lines_kind_needs_a_matching_changed_line(repo):
    _commit(repo, {"registry.py": 'MODELS = ["gemini-1"]\nOTHER = 2\n'})
    assert _names(repo) == set()
    _commit(repo, {"registry.py": 'MODELS = ["gemini-1", "gemini-2"]\nOTHER = 2\n'})
    assert _names(repo) == {"models"}


def test_lines_kind_ignores_a_pure_rename(repo):
    _commit(repo, {"registry.py": None, "registry2.py": BASE_FILES["registry.py"]})
    r = gate.Repo("main")
    trig = {"paths": ["registry*.py"], "pattern": "gemini-"}
    assert gate.fire_lines(trig, r, r.changes()) == []


def test_symbols_kind_only_fires_for_named_functions(repo):
    _commit(repo, {"client.py": BASE_FILES["client.py"].replace("return 2", "return 3")})
    assert _names(repo) == set()
    _commit(repo, {"client.py": BASE_FILES["client.py"].replace("return 1", "return 0")})
    assert _fired(repo) == {"session (seat)": ["client.py::Client.login"]}


def test_toml_keys_kind_ignores_other_keys(repo):
    _commit(repo, {"pyproject.toml": BASE_FILES["pyproject.toml"].replace('"1"', '"2"')})
    assert _names(repo) == set()
    _commit(repo, {"pyproject.toml": BASE_FILES["pyproject.toml"].replace('["a"]', '["a", "b"]')})
    assert _names(repo) == {"deps"}


def test_removed_route_fires_but_a_moved_route_does_not(repo):
    moved = 'r2 = APIRouter(prefix="/api")\n\n@r2.get("/a/x")\ndef x(): ...\n'
    _commit(repo, {"routes/a.py": None, "routes/b.py": moved})
    assert _names(repo) == set()
    _commit(repo, {"routes/b.py": "r2 = APIRouter()\n"})
    assert _fired(repo) == {"routes (contract)": ["GET /api/a/x"]}


def test_fired_trigger_fails_without_adr_or_reason(repo):
    _commit(repo, {".importlinter": "[importlinter]\nx = 1\n"})
    assert _run(repo) == 1


def test_adding_an_adr_satisfies_the_gate(repo):
    _commit(repo, {".importlinter": "changed\n", "docs/adr/0002-new.md": "# ADR\n"})
    assert _run(repo) == 0


def test_deleting_an_adr_does_not_satisfy_the_gate(repo):
    _commit(repo, {"docs/adr/0001-old.md": "# ADR\n"})
    _git(repo, "branch", "-f", "main", "HEAD")
    _commit(repo, {".importlinter": "changed\n", "docs/adr/0001-old.md": None})
    assert _run(repo) == 1


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("## Decisions\n\nNo-ADR: renames a test helper only\n", "renames a test helper only"),
        ("- **No-ADR:** bug fix inside the rule\n", "bug fix inside the rule"),
        ("<!-- No-ADR: <reason> -->\n", None),
        ("No-ADR: <reason>\n", None),
        ("No-ADR:\n", None),
        ("No-ADR: <why this is not a decision, in one line>\n", None),
        ("", None),
    ],
)
def test_no_adr_reason_parsing(body, reason):
    assert gate.no_adr_reason(body) == reason


def test_no_adr_line_satisfies_the_gate(repo):
    _commit(repo, {".importlinter": "changed\n"})
    assert _run(repo, "No-ADR: only reorders the contracts, same layers\n") == 0


def test_pr_body_comes_from_the_github_event(tmp_path, monkeypatch):
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"body": "No-ADR: x"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    assert gate.pr_body(None) == "No-ADR: x"
    event.write_text(json.dumps({"pull_request": {"body": None}}))
    assert gate.pr_body(None) == ""


def test_no_base_ref_skips(repo):
    assert gate.main(["--triggers", str(repo / "triggers.toml")]) == 0


def test_unknown_kind_is_rejected(tmp_path):
    bad = tmp_path / "t.toml"
    bad.write_text('[[trigger]]\nname = "x"\nwhy = "y"\nkind = "nope"\npaths = []\n')
    with pytest.raises(ValueError, match="unknown kind"):
        gate.load_triggers(bad)


def test_shipped_trigger_list_is_valid_and_points_at_real_files():
    """Every trigger loads, and each non-glob path exists, so a rename can't silently disarm it."""
    root = Path(__file__).resolve().parents[2]
    for t in gate.load_triggers(gate.DEFAULT_TRIGGERS):
        assert t["name"] and t["why"]
        for p in t["paths"]:
            if "*" not in p:
                assert (root / p).exists(), f"trigger {t['name']!r}: {p} does not exist"
        if t["kind"] == "symbols":
            (path,) = t["paths"]
            have = gate.symbol_sources((root / path).read_text())
            missing = [s for s in t["symbols"] if s not in have]
            assert not missing, f"trigger {t['name']!r}: {missing} not in {path}"


def test_pr_body_prefers_the_live_description_when_a_token_is_set(tmp_path, monkeypatch):
    """A re-run replays the old event; the gate reads the current body from the API."""
    event = tmp_path / "event.json"
    url = "https://api.github.com/repos/o/r/pulls/7"
    event.write_text(json.dumps({"pull_request": {"url": url, "body": "old"}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GH_TOKEN", "t")
    seen = {}

    class Resp:
        def __enter__(self):
            return io.BytesIO(b'{"body": "No-ADR: edited later"}')

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen["url"], seen["auth"] = req.full_url, req.get_header("Authorization")
        return Resp()

    monkeypatch.setattr(gate.urllib.request, "urlopen", fake_urlopen)
    assert gate.pr_body(None) == "No-ADR: edited later"
    assert seen == {"url": url, "auth": "Bearer t"}


def test_pr_body_falls_back_to_the_event_when_the_api_fails_or_url_is_foreign(
    tmp_path, monkeypatch
):
    event = tmp_path / "event.json"
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GH_TOKEN", "t")

    def boom(req, timeout):
        raise OSError("down")

    monkeypatch.setattr(gate.urllib.request, "urlopen", boom)
    event.write_text(json.dumps({"pull_request": {"url": "https://api.github.com/x", "body": "a"}}))
    assert gate.pr_body(None) == "a"
    event.write_text(json.dumps({"pull_request": {"url": "https://evil.example/x", "body": "b"}}))
    assert gate.pr_body(None) == "b"


def test_shipped_list_flags_ai_input_store_changes():
    """#165 (signed-URL uploads writing ai_store_files) would have needed an ADR."""
    (trig,) = [
        t for t in gate.load_triggers(gate.DEFAULT_TRIGGERS)
        if t["name"] == "AI input store / cloud storage"
    ]  # fmt: skip
    for path in (
        "backend/app/services/direct_upload.py",
        "backend/app/services/gcs.py",
        "backend/app/services/upload_cleanup.py",
    ):
        change = gate.Change("M", path, path)
        assert gate.fire_modified(trig, None, [change]) == [path]  # type: ignore[arg-type]
    other = gate.Change("M", "backend/app/services/gemini.py", "backend/app/services/gemini.py")
    assert gate.fire_modified(trig, None, [other]) == []  # type: ignore[arg-type]
