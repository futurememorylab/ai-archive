# gh-handoff dry-fire test plan (synthetic, not a real feature)

This is a throwaway plan used only to test the `gh-handoff` skill's
fire -> monitor -> PR-checklist mechanics end to end. Not real work.

## Task 1: add a scratch marker file

Create `docs/scratch/gh-handoff-dryfire-marker.md` containing exactly:

    gh-handoff dry-fire test marker — safe to delete, no functional code touched.

Commit it. No tests are needed for this task (it's a static doc file, not
application code) — skip straight to opening the draft PR per the routine's
standard flow.
