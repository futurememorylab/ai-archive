---
name: server-start
description: Start the catdv-annotator backend dev server safely. Use this whenever the user asks to start, launch, run, restart, or boot the backend / FastAPI / uvicorn server (typically on port 8765), or mentions firing up the app. Enforces the single-instance + graceful-shutdown discipline that keeps the scarce CatDV license seat available.
---

# Server Start

The catdv-annotator backend talks to a CatDV Enterprise server that has only **2 session seats total**, and one is usually taken by the human web client. A second local dev server (or a leaked `JSESSIONID`) will lock everyone out until CatDV times the session out server-side. This skill encodes the safe start procedure.

## Steps

1. **Check whether the server is already running.**

   ```bash
   /usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
   /bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
   ```

2. **If something is already listening on 8765**, do not launch a second instance. Verify health and report back:

   ```bash
   /usr/bin/curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/healthz
   ```

   Tell the user the existing PID and health status, and stop. Ask before killing it.

3. **If nothing is running**, activate the project venv and start the server. Use the local venv (`.venv/bin/python`), never system Python:

   ```bash
   cd /Users/peterhora/Documents/futurememorylab/sikl/catdv-annotator
   .venv/bin/python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765 --reload
   ```

   Run it via `run_in_background: true` so it doesn't block the conversation, then capture the PID.

4. **Tail logs briefly (~5 s) to confirm clean startup.** Look for `Application startup complete.` and any CatDV login / lifespan errors. If startup fails, surface the error — do not retry in a loop.

5. **Report** the PID, port, and health check result back to the user.

## Hard rules

- **Never run parallel installs, venv copies, or server restarts.** They contend for the same resources and break the CatDV seat discipline. Serialize everything.
- **Never use `kill -9`** to stop this server. Use `/bin/kill -TERM <pid>` so FastAPI's `lifespan` runs `ctx.aclose()` and releases the CatDV seat. See the project `CLAUDE.md` for the full shutdown procedure.
- **One diagnostic pass, not a loop.** If the health check or log tail doesn't give a clear answer in 2–3 commands, stop and read the user's request again or read the code — don't ping/curl in a tight loop.
- **502 with "Maximum:2"** means a CatDV seat is stuck. Don't keep retrying — wait it out or ask the admin. See `CLAUDE.md` → "When a 502 says Maximum:2".

## Why this matters

The CatDV REST API binds sessions to `JSESSIONID` server-side. Our process dying doesn't free the seat; only a clean `DELETE /catdv/api/9/session` (which `aclose()` performs during graceful shutdown) does. Combining "check before starting" with "kill -TERM, never -9" is what keeps the one available seat usable across dev sessions.
