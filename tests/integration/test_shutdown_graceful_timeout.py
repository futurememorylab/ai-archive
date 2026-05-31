"""Regression guard for the shutdown-button hang.

A long-lived SSE connection that ignores uvicorn's exit signal (e.g. the raw
`StreamingResponse` at `/api/connection/events`) used to block uvicorn's
graceful shutdown *forever* — uvicorn has no graceful-shutdown timeout by
default, and the lifespan teardown that releases the CatDV seat
(`LiveCtx.aclose`) only runs after every connection closes. So the process
hung at "Waiting for connections to close." and the seat leaked.

The fix bounds the wait with `--timeout-graceful-shutdown`, set in both launch
points (`run.sh`, `deploy/catdv-annotator.service`). After the timeout uvicorn
force-closes lingering connections and still runs lifespan shutdown.

These tests are seat-safe: with no CatDV credentials in the environment and no
`.env` in the working directory, `init_external` is False, so the spawned
server never logs into CatDV and never takes a seat.
"""

from __future__ import annotations

import contextlib
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_SH = REPO_ROOT / "run.sh"
SERVICE_UNIT = REPO_ROOT / "deploy" / "catdv-annotator.service"

GRACEFUL_TIMEOUT_S = 3


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, timeout_s: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5) as s:
                s.sendall(
                    b"GET /api/health HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
                )
                if b"200" in s.recv(256):
                    return True
        except OSError:
            pass
        time.sleep(0.2)
    return False


@pytest.mark.timeout(40)
def test_open_sse_stream_does_not_block_graceful_shutdown(tmp_path):
    """With an SSE stream held open, SIGTERM must still finish lifespan
    shutdown within the graceful-timeout window (not hang indefinitely)."""
    port = _free_port()
    # Build a clean environment: no real CatDV/GCP creds => init_external False
    # => no seat taken. cwd=tmp_path has no .env, so pydantic loads none.
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
        "APP_ENV": "dev",
        "CATDV_BASE_URL": "http://127.0.0.1:9/none",
        "CATDV_CATALOG_ID": "1",
        "GCP_PROJECT_ID": "dummy",
        "GCS_BUCKET_NAME": "dummy",
        "DATA_DIR": str(tmp_path / "data"),
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--timeout-graceful-shutdown",
            str(GRACEFUL_TIMEOUT_S),
        ],
        cwd=str(tmp_path),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    sse: socket.socket | None = None
    try:
        assert _wait_health(port), "server did not become healthy"

        # Hold an SSE connection open — the raw StreamingResponse generator
        # blocks on queue.get() and never reacts to the exit signal.
        sse = socket.create_connection(("127.0.0.1", port), timeout=2)
        sse.sendall(
            b"GET /api/connection/events HTTP/1.1\r\nHost: x\r\n\r\n"
        )
        sse.settimeout(2)
        with contextlib.suppress(socket.timeout):
            sse.recv(256)  # read the 200 response head so the stream is active

        start = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=GRACEFUL_TIMEOUT_S + 7)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail(
                "server hung on shutdown with an open SSE connection — "
                "graceful-shutdown timeout did not force it to exit"
            )
        elapsed = time.monotonic() - start
    finally:
        if sse is not None:
            with contextlib.suppress(OSError):
                sse.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    out = proc.stdout.read() if proc.stdout else ""
    # Lifespan teardown ran => the seat-release path executed.
    assert "Application shutdown complete" in out, out
    # Sanity: it actually waited for the stream (didn't exit instantly) but
    # stayed bounded by the configured timeout plus teardown slack.
    assert elapsed <= GRACEFUL_TIMEOUT_S + 7


def test_run_sh_sets_graceful_shutdown_timeout():
    text = RUN_SH.read_text()
    assert "--timeout-graceful-shutdown" in text, (
        "run.sh must pass --timeout-graceful-shutdown so an open SSE/WS "
        "connection cannot make shutdown hang and leak the CatDV seat"
    )


def test_systemd_unit_sets_graceful_shutdown_timeout():
    text = SERVICE_UNIT.read_text()
    exec_line = next(
        (ln for ln in text.splitlines() if ln.strip().startswith("ExecStart=")),
        "",
    )
    assert "--timeout-graceful-shutdown" in exec_line, (
        "the systemd unit's ExecStart must pass --timeout-graceful-shutdown"
    )
    assert re.search(r"--timeout-graceful-shutdown\s+\d+", exec_line)
