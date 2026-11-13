# Backend: what counts as a serious problem

The Claude PR reviewer (`.github/workflows/claude-code-review.yml`) checks
each change against the CLAUDE.md files on its path, so this file is the
review guidance for `backend/`. The general rules are in the root
`AGENTS.md`; this page names the failures that matter most here. Flag a
change that makes one of them possible, quoting the file and line.

- **Wrong or lost data in the archive.** Anything that writes to CatDV or
  the filesystem sidecar must write exactly the reviewed fields to the
  right clip, and a failed or partial write must stay visible and
  retryable, never reported as done.
- **Human work destroyed.** Deleting or overwriting annotations, drafts,
  markers or a person's edits, outside an explicit user action that says
  so.
- **A CatDV seat left locked.** Every code path that logs in to CatDV must
  log out (`CatdvClient.logout`), including on errors and shutdown.
- **Absence without evidence.** Treating a clip as gone for any reason
  other than `archive/errors.py::is_provider_not_found(exc)`.
- **Media fetched around the cache.** Clip media read straight from CatDV
  or GCS instead of through the cache layers
  (`services/clip_media.py::ai_input_for_clip` is the pattern).
- **Unplanned Gemini spend.** A retry, loop or re-run that can send the
  same clip or prompt to Gemini more than once without the user asking.
- **Secrets or private data leaked** into logs, error text, templates or
  the browser.
- **Raw errors shown to users.** User-facing error text must go through
  `services/errors.py::humanise`.

## Decisions and the canon

Also flag, as a serious problem:

- **A decision without an ADR.** The diff changes behaviour that
  `docs/decisions.md` says needs an ADR (a new invariant or a change to
  one, a schema or public-contract change, a cloud-topology choice, a new
  dependency, CatDV session handling, access control, the archive write
  rules) and the PR neither adds an ADR nor gives a `No-ADR:` reason that
  holds up. CI only checks that the line exists; judge whether the reason
  is true. The trigger list is `tools/decision_triggers.toml`.
- **A contradicted invariant.** The diff breaks a rule in
  `docs/architecture-invariants.md` without an ADR that supersedes it.
  Name the invariant number and quote the line.
- **A half-marked supersession.** A new ADR replaces an older one but the
  older one isn't marked superseded and pointing forward
  (`tools/adr_lint.py` catches the mechanical part).

Don't flag style, naming or missing tests here; ruff, the erosion gate and
CI cover those.
