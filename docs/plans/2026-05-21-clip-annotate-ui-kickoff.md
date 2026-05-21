# Subagent kickoff prompt — clip annotate UI

Paste the block below into a fresh Claude Code session in this repo
(`/Users/peterhora/Documents/futurememorylab/sikl/catdv-annotator`).

---

```
Implement the plan at docs/plans/2026-05-21-clip-annotate-ui.md, which is
backed by the spec at docs/specs/2026-05-21-clip-annotate-ui-design.md.

Use the superpowers:subagent-driven-development skill — dispatch a fresh
subagent per task, review between tasks, do not skip the review step. The
plan has 17 tasks; each task ends with a single conventional-commits
commit. Do not squash and do not rewrite history.

Important context this session does NOT carry over from the brainstorming
session that produced the plan:

1. Project-specific shutdown discipline. Read CLAUDE.md and the parent
   sikl/CLAUDE.md before touching anything that talks to CatDV. Every
   manual verification step in the plan ends with `kill -TERM` for a
   reason: CatDV has 2 license seats and SIGKILL leaks one.

2. Python venv. All Python invocations use `.venv/bin/python` and
   `.venv/bin/pytest`, never system `python3`. The plan uses this
   convention throughout — do not "fix" it.

3. The backend pipeline is reused as-is. Do not edit
   backend/app/services/{gcs,gemini,annotator,target_map}.py,
   backend/app/archive/ai_stores/*, scripts/setup-gcp.sh, or
   .env.example. The spec lists them as untouched modules; the plan does
   not include any task that modifies them.

4. View-models in this codebase return plain `dict[str, Any]`, not pydantic
   models. Match that pattern in `backend/app/services/draft_view.py`.

5. Two soft spots flagged by the planning pass — verify before assuming:
   - Task 6 assumes `client`, `ctx`, `seeded_clip_101` fixtures exist or can
     be added in `tests/integration/conftest.py`. Read that file first and
     mimic the FakeArchive pattern from
     `tests/integration/test_annotator_worker.py:112-117` if they don't.
   - Task 12 lifts Alpine state up to the `.detail` wrapper via
     `Object.assign(player(...), {...})`. Verify in the browser that the
     player still works after the lift — Alpine should be fine with it,
     but it's the trickiest moment in the plan.

6. Frequent commits. Each task ends with a single commit. Use the exact
   message from the plan; co-author trailer is already in this repo's
   convention but not required.

7. Session-end convention. When the plan is complete (or you pause for the
   day), append an entry to docs/decisions.md per the format already
   established there — see commit 520705e and the existing entries. Group
   related task-level decisions under one dated header.

Start by reading the spec, then the plan, then begin Task 1.
```

---

## Why a kickoff prompt and not just "run the plan"

The next session will start cold. The plan + spec carry the *what* and
*how*; this kickoff carries the *what not to do* — the four or five
things that would be invisible without conversation context and would
cause real damage (CatDV seat leak, system python, edits to untouched
modules, missing fixtures).

If the session goes long, the subagent-driven loop will preserve
context per-task because each subagent only reads what it needs.
