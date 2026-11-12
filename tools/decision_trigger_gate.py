#!/usr/bin/env python3
"""Decision-trigger gate: a decision-sensitive change needs an ADR or a reason.

Several decisions shipped without an ADR (a CatDV session id persisted in
`app_meta`, a deploy `--max=1` change, signed-URL uploads, multi-catalog).
Each touched code that the admission bar in `docs/decisions.md` covers.
This gate makes the question unskippable whichever tool wrote the PR:

    a PR whose diff (vs the merge-base with --base-ref) trips a trigger in
    `tools/decision_triggers.toml` must add or change a file under
    `docs/adr/`, or carry a line `No-ADR: <reason>` in its description.

The PR description is read from the GitHub event (`GITHUB_EVENT_PATH`) or
`--pr-body-file`; HTML comments are ignored, so the template's placeholder
doesn't count. With no `--base-ref` (a push to main) there is nothing to
compare and the gate passes. The trigger list is data; see the TOML file
for the kinds it supports.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TRIGGERS = Path(__file__).with_name("decision_triggers.toml")
ADR_PREFIX = "docs/adr/"
_NO_ADR = re.compile(r"(?im)^[\s>*_-]*No-ADR\**\s*:\s*\**\s*(.+?)\s*$")
_PLACEHOLDERS = {"", "<reason>", "reason", "n/a", "-", "none"}
_ROUTER = re.compile(r"(?m)^(\w+)\s*=\s*APIRouter\(([^)]*)\)")
_PREFIX = re.compile(r"""prefix\s*=\s*["']([^"']*)["']""")
_ROUTE = re.compile(
    r"""@(\w+)\.(get|post|put|patch|delete|head|options|websocket|api_route)\(\s*["']([^"']*)["']"""
)


@dataclass(frozen=True)
class Change:
    """One file in the diff: git status letter, path, and the pre-rename path."""

    status: str
    path: str
    old_path: str


class Repo:
    """Read-only git access between the merge-base and HEAD."""

    def __init__(self, base_ref: str, head: str = "HEAD", cwd: Path | None = None) -> None:
        self.cwd = cwd
        self.head = head
        self.base = self._git("merge-base", base_ref, head).strip()

    def _git(self, *args: str, check: bool = True) -> str:
        res = subprocess.run(["git", *args], cwd=self.cwd, capture_output=True, text=True)
        if check and res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout if res.returncode == 0 else ""

    def changes(self) -> list[Change]:
        """Every changed file, with renames detected."""
        out = []
        for line in self._git("diff", "--name-status", "-M", self.base, self.head).splitlines():
            parts = line.split("\t")
            status = parts[0][0]
            path = parts[-1]
            out.append(Change(status, path, parts[1] if status == "R" else path))
        return out

    def base_text(self, path: str) -> str:
        """File content at the merge-base ('' if absent)."""
        return self._git("show", f"{self.base}:{path}", check=False)

    def head_text(self, path: str) -> str:
        """File content at the head revision ('' if absent)."""
        return self._git("show", f"{self.head}:{path}", check=False)

    def changed_lines(self, path: str) -> list[str]:
        """Added and removed lines of one file, without the +/- marker."""
        diff = self._git("diff", "-U0", self.base, self.head, "--", path)
        return [
            line[1:]
            for line in diff.splitlines()
            if line[:1] in "+-" and not line.startswith(("+++", "---"))
        ]

    def files(self, rev: str, globs: list[str]) -> list[str]:
        """Tracked files at `rev` ('base' or 'head') matching any glob."""
        commit = self.base if rev == "base" else self.head
        names = self._git("ls-tree", "-r", "--name-only", commit).splitlines()
        return [n for n in names if matches(n, globs)]


def matches(path: str, globs: list[str]) -> bool:
    """fnmatch-style match (`*` crosses `/`)."""
    return any(fnmatch.fnmatchcase(path, g) for g in globs)


def _touched(trigger: dict, changes: list[Change]) -> list[Change]:
    globs = trigger["paths"]
    return [c for c in changes if matches(c.path, globs) or matches(c.old_path, globs)]


def fire_added(trigger: dict, repo: Repo, changes: list[Change]) -> list[str]:
    """New files (not renames) matching the globs."""
    return [c.path for c in _touched(trigger, changes) if c.status == "A"]


def fire_modified(trigger: dict, repo: Repo, changes: list[Change]) -> list[str]:
    """Any change to a matching file."""
    return [c.path for c in _touched(trigger, changes)]


def fire_lines(trigger: dict, repo: Repo, changes: list[Change]) -> list[str]:
    """Changed lines matching `pattern`."""
    rx = re.compile(trigger["pattern"])
    out = []
    for c in _touched(trigger, changes):
        hits = [ln.strip() for ln in repo.changed_lines(c.path) if rx.search(ln)]
        out += [f"{c.path}: {h}" for h in hits]
    return out


def symbol_sources(text: str) -> dict[str, str]:
    """Dotted name -> source of every class and function in a Python file."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {}
    out: dict[str, str] = {}

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                name = f"{prefix}{child.name}"
                out[name] = ast.get_source_segment(text, child) or ""
                walk(child, f"{name}.")

    walk(tree, "")
    return out


def fire_symbols(trigger: dict, repo: Repo, changes: list[Change]) -> list[str]:
    """Named functions/classes whose source changed, appeared or vanished."""
    out = []
    for c in _touched(trigger, changes):
        before = symbol_sources(repo.base_text(c.old_path))
        after = symbol_sources(repo.head_text(c.path))
        out += [f"{c.path}::{s}" for s in trigger["symbols"] if before.get(s) != after.get(s)]
    return out


def _dig(data: dict, dotted: str) -> object:
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)  # type: ignore[assignment]
    return data


def _toml(text: str) -> dict:
    try:
        return tomllib.loads(text) if text else {}
    except tomllib.TOMLDecodeError:
        return {}


def fire_toml_keys(trigger: dict, repo: Repo, changes: list[Change]) -> list[str]:
    """TOML keys whose value differs between base and HEAD."""
    out = []
    for c in _touched(trigger, changes):
        before, after = _toml(repo.base_text(c.old_path)), _toml(repo.head_text(c.path))
        out += [f"{c.path}: {k}" for k in trigger["keys"] if _dig(before, k) != _dig(after, k)]
    return out


def routes_in(text: str) -> set[str]:
    """`METHOD /prefix/path` for each route decorator in a routes module."""
    prefixes = {}
    for var, args in _ROUTER.findall(text):
        m = _PREFIX.search(args)
        prefixes[var] = m.group(1) if m else ""
    return {
        f"{method.upper()} {prefixes.get(var, '')}{path}"
        for var, method, path in _ROUTE.findall(text)
    }


def fire_removed_routes(trigger: dict, repo: Repo, changes: list[Change]) -> list[str]:
    """Routes present at the base and gone from every matching file at HEAD."""
    if not _touched(trigger, changes):
        return []
    globs = trigger["paths"]
    before = set().union(*(routes_in(repo.base_text(p)) for p in repo.files("base", globs)))
    after = set().union(*(routes_in(repo.head_text(p)) for p in repo.files("head", globs)))
    return sorted(before - after)


KINDS: dict[str, Callable[[dict, Repo, list[Change]], list[str]]] = {
    "added": fire_added,
    "modified": fire_modified,
    "lines": fire_lines,
    "symbols": fire_symbols,
    "toml_keys": fire_toml_keys,
    "removed_routes": fire_removed_routes,
}


def load_triggers(path: Path) -> list[dict]:
    """The [[trigger]] tables, validated against KINDS."""
    triggers = tomllib.loads(path.read_text(encoding="utf-8"))["trigger"]
    for t in triggers:
        if t.get("kind") not in KINDS:
            raise ValueError(f"trigger {t.get('name')!r}: unknown kind {t.get('kind')!r}")
    return triggers


def fired(triggers: list[dict], repo: Repo, changes: list[Change]) -> dict[str, list[str]]:
    """Trigger name -> what tripped it, for each trigger that fired."""
    out: dict[str, list[str]] = {}
    for t in triggers:
        hits = KINDS[t["kind"]](t, repo, changes)
        if hits:
            out[f"{t['name']} ({t['why']})"] = hits
    return out


def touches_adr(changes: list[Change]) -> bool:
    """True when the PR adds, edits or renames an ADR file."""
    return any(c.status != "D" and c.path.startswith(ADR_PREFIX) for c in changes)


def no_adr_reason(body: str) -> str | None:
    """The `No-ADR:` reason in a PR description, ignoring HTML comments and placeholders."""
    visible = re.sub(r"(?s)<!--.*?-->", "", body or "")
    for reason in _NO_ADR.findall(visible):
        if reason.strip().lower() not in _PLACEHOLDERS:
            return reason.strip()
    return None


def pr_body(body_file: str | None) -> str:
    """The PR description: --pr-body-file, else the GitHub event payload, else ''."""
    if body_file:
        return Path(body_file).read_text(encoding="utf-8")
    event = os.environ.get("GITHUB_EVENT_PATH")
    if not (event and Path(event).exists()):
        return ""
    pr = json.loads(Path(event).read_text(encoding="utf-8")).get("pull_request") or {}
    return _live_body(pr.get("url")) or pr.get("body") or ""


def _live_body(api_url: str | None) -> str | None:
    """The PR description as it is now, so a re-run sees an edited description.

    A re-run job replays the original event payload; with GH_TOKEN set this
    reads the current body from the PR's own API URL (from that payload).
    None when there's no token or the request fails, so the payload is used.
    """
    token = os.environ.get("GH_TOKEN")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if not (token and api_url and api_url.startswith(api + "/")):
        return None
    req = urllib.request.Request(
        api_url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp).get("body") or ""
    except (OSError, ValueError):
        return None


def verdict(hits: dict[str, list[str]], changes: list[Change], body: str) -> tuple[bool, str]:
    """(passed, message) for the fired triggers and the PR's ADR evidence."""
    if not hits:
        return True, "OK: no decision-sensitive paths touched"
    listing = "\n".join(f"  - {name}\n      " + "\n      ".join(h) for name, h in hits.items())
    if touches_adr(changes):
        return True, f"OK: decision triggers fired and the PR adds or changes an ADR\n{listing}"
    reason = no_adr_reason(body)
    if reason:
        return True, f"OK: decision triggers fired; No-ADR: {reason}\n{listing}"
    return False, (
        "FAIL: this PR touches decision-sensitive code but has no ADR.\n"
        f"{listing}\n"
        "Either add an ADR under docs/adr/ (see docs/decisions.md for the bar), or add a\n"
        "line to the PR description:  No-ADR: <why this is not a decision>"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns process exit code."""
    ap = argparse.ArgumentParser(description="Decision-sensitive change needs an ADR or a reason")
    ap.add_argument("--base-ref", default="", help="e.g. origin/main; empty skips the gate")
    ap.add_argument("--triggers", default=str(DEFAULT_TRIGGERS))
    ap.add_argument("--pr-body-file", help="Read the PR description from a file")
    args = ap.parse_args(argv)

    triggers = load_triggers(Path(args.triggers))
    if not args.base_ref:
        print("SKIP: no --base-ref (not a pull request); nothing to compare")
        return 0
    repo = Repo(args.base_ref)
    changes = repo.changes()
    ok, message = verdict(fired(triggers, repo, changes), changes, pr_body(args.pr_body_file))
    print(message, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
