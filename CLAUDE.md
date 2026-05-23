# CLAUDE.md — catdv-annotator

Project-scoped guidance for Claude Code sessions working inside this repo. Network/auth context (VPN, credentials, contacts) lives in the parent `sikl/CLAUDE.md`; this file only covers what's relevant when editing or running the code here.

## CatDV session discipline (license seats)

CatDV Enterprise has a **2-seat session limit**, and in practice one seat is almost always taken by the human web client — so **assume 1 seat is available to this app**. A leaked `JSESSIONID` locks the server out until it times out server-side, which can be many minutes.

### Before starting a dev server

Always check for an existing instance first. Don't launch a second one:

```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
/bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
/usr/sbin/lsof -nP -iTCP@192.168.1.41:8080
```

If anything is listening on `8765` or connected to `192.168.1.41:8080`, **reuse it or shut it down first** — don't spawn another.

### Always shut down gracefully

Use `SIGTERM`, **never `SIGKILL`**. Only TERM lets FastAPI's `lifespan` run `ctx.aclose()`, which calls `DELETE /catdv/api/9/session` and frees the seat.

```bash
/bin/kill -TERM <pid>          # ✅ graceful — runs aclose()
/bin/kill -9 <pid>             # ❌ leaks the JSESSIONID — seat held until CatDV times it out
```

After kill, confirm in the server log:

```
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.   ← this line means the seat was released
INFO:     Finished server process [...]
```

If you only see `Finished server process` without the shutdown lines above, the seat may still be held — wait it out or ask the admin to kick the stale session.

### One-shot scripts must log out too

If you `POST /session` directly from a script or `curl`, you've taken a seat. Finish with:

```bash
curl -b /tmp/jar -X DELETE http://192.168.1.41:8080/catdv/api/9/session
```

Otherwise the seat stays held for the JSESSIONID's idle-timeout window.

### When a 502 says "Maximum:2"

`GET /` returning `502 Bad Gateway` with detail `"Web Client session limit reached (Maximum:2)."` means **a seat is stuck**. Don't keep retrying — that won't free anything. Either:

1. Wait it out (server-side timeout eventually drops the stale session).
2. Ask the admin to kick the session in the CatDV admin UI.
3. If you suspect it's your own leaked session: confirm no `uvicorn` / `python backend.app` process is still alive (`ps`, `lsof`) — if one is, `kill -TERM` it properly.

## Why this matters

The CatDV REST API binds the session to `JSESSIONID` and the seat is held *server-side*, not by our process. So even when our process dies, the seat can linger. The combination of (a) checking before starting and (b) graceful shutdown after running is what keeps the single available seat usable for the next dev session.

## Recording decisions at end of session

When a session involves any non-trivial design call — a schema replacement,
an API shape choice, a deliberate deviation from the spec, a "we considered
X and Y, picked Z" moment — append a new ADR file to
`docs/adr/NNNN-slug.md` (one number higher than the last) before the
session ends. Use the MADR-lite format: a `# NNNN. <Title>` heading,
`**Date:**` / `**Status:**` metadata, then `## Context` / `## Alternatives` /
`## Decision` / `## Consequences` sections. See any existing ADR (e.g.
`docs/adr/0001-python-only-stack-no-node-frontend.md`) for the template.
Update the index table in `docs/decisions.md` with the new entry. Group
several related calls under one ADR when they share context (see the
PR 3 / PR 5 / PR 6 / PR 7 ADRs for the pattern).

The bar is "would a future contributor reading the diff ask *why*?" If
yes, document it. If the call was forced by an obvious constraint and the
diff itself makes the reasoning self-evident, skip it. Pure mechanical
work (renames, dependency bumps, test additions) does not need an entry.
