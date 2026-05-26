---
name: server-stop
description: Stop the catdv-annotator backend dev server gracefully so the CatDV license seat is released. Use this whenever the user asks to stop, shut down, kill, halt, or terminate the backend / FastAPI / uvicorn server (typically on port 8765). Enforces SIGTERM-only shutdown and verifies the seat-release log lines — a `kill -9` will leak the CatDV `JSESSIONID` and block the next dev session.
---

# Server Stop

The catdv-annotator backend holds a CatDV Enterprise session seat while it runs. CatDV only has 2 seats total, and the human web client usually holds one — so this process is on the scarce one. The seat is released **only** when FastAPI's `lifespan` runs `ctx.aclose()` on shutdown, which calls `DELETE /catdv/api/9/session`. That only happens on `SIGTERM`, never on `SIGKILL`.

## Steps

1. **Find the running PID.**

   ```bash
   /usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
   /bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
   ```

   If nothing is running, report that and stop — nothing to do.

2. **Send `SIGTERM` to the PID.**

   ```bash
   /bin/kill -TERM <pid>
   ```

   Do **not** use `kill -9` / `SIGKILL`. That bypasses `lifespan` and leaves the CatDV seat held server-side for the JSESSIONID's idle-timeout window (minutes).

3. **Wait a few seconds, then check the server log for the seat-release evidence.** Look for all four lines, in order:

   ```
   INFO:     Shutting down
   INFO:     Waiting for application shutdown.
   INFO:     Application shutdown complete.   ← seat was released
   INFO:     Finished server process [...]
   ```

   The critical line is `Application shutdown complete.` — that's the proof `aclose()` ran. If you only see `Finished server process` without the preceding shutdown lines, the seat may still be held.

4. **Verify the port is free.**

   ```bash
   /usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN
   ```

   Empty output = clean shutdown.

5. **Report** to the user: PID terminated, whether the four shutdown lines appeared, and that the port is free.

## Hard rules

- **Never `kill -9`** unless the user explicitly insists after being warned that it leaks a CatDV seat. Even then, surface the consequence first.
- **One pass, not a retry loop.** If `SIGTERM` doesn't bring the process down within ~10 s, stop and report — don't escalate to `-9` automatically and don't keep polling in a tight loop. Read the log to find out *why* it's not shutting down (often a hung outbound CatDV request).
- **Don't restart in the same command.** If the user wants a restart, stop here, verify the shutdown lines, then invoke the `server-start` skill as a separate step.

## If something goes wrong

- **No `Application shutdown complete.` line**: the seat may still be held. Either wait for CatDV's server-side timeout, or ask Honza (admin) to kick the stale session in the CatDV admin UI. Do not keep restarting — that won't free anything and may compound the problem.
- **Process won't die after `SIGTERM`**: usually a stuck network call to `192.168.1.41:8080`. Check VPN reachability (`ping -c1 192.168.1.41`) and the server log for the last activity before deciding whether to wait longer or escalate.

## Why this matters

The CatDV REST API binds sessions to `JSESSIONID` server-side. Our process exiting does not free the seat; only an explicit `DELETE /catdv/api/9/session` does, and that call lives in the FastAPI `lifespan` shutdown path. `SIGTERM` gives that path a chance to run; `SIGKILL` does not. Treat graceful shutdown as the contract that keeps the single available seat usable across dev sessions.
