"""Resolve (duration_secs, fps) for a media file via `ffprobe` if present.

If `ffprobe` is not on `PATH`, log a single warning per process and return
`(0.0, 25.0)`. The default fps matches the rest of the codebase. Failures
during probing (subprocess error, malformed JSON, missing fields) also
fall back to the defaults — the user can still annotate; only timeline
duration and fps display will be wrong, and a downstream operator can
install `ffprobe` to fix it.

Kept deliberately tiny and dependency-free (no `ffmpeg-python`, no async).
The adapter calls `probe()` from threadpooled code paths or directly in
the read path — `ffprobe` itself is bounded by the per-call timeout.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_DURATION_SECS = 0.0
DEFAULT_FPS = 25.0
PROBE_TIMEOUT_SECS = 5.0

_warned_missing = False


def _warn_missing_once() -> None:
    global _warned_missing
    if not _warned_missing:
        log.warning(
            "ffprobe not found on PATH; FS adapter will report duration=0 fps=25 "
            "for every clip. Install ffmpeg to enable accurate probing."
        )
        _warned_missing = True


def _parse_fps(rate: str) -> float:
    """Parse '30000/1001' or '25/1' or '24' → float fps. Defaults on error."""
    try:
        if "/" in rate:
            num_s, _, den_s = rate.partition("/")
            num = float(num_s)
            den = float(den_s)
            if den <= 0:
                return DEFAULT_FPS
            return num / den
        return float(rate)
    except (TypeError, ValueError):
        return DEFAULT_FPS


def probe(path: Path) -> tuple[float, float]:
    """Return `(duration_secs, fps)` for the given media file."""
    if shutil.which("ffprobe") is None:
        _warn_missing_once()
        return DEFAULT_DURATION_SECS, DEFAULT_FPS

    try:
        cp = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate,duration:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_SECS,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("ffprobe failed for %s: %s", path, exc)
        return DEFAULT_DURATION_SECS, DEFAULT_FPS

    if cp.returncode != 0:
        return DEFAULT_DURATION_SECS, DEFAULT_FPS

    try:
        payload = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return DEFAULT_DURATION_SECS, DEFAULT_FPS

    duration = DEFAULT_DURATION_SECS
    fps = DEFAULT_FPS

    streams = payload.get("streams") or []
    if isinstance(streams, list) and streams:
        s0 = streams[0]
        if isinstance(s0, dict):
            r = s0.get("r_frame_rate")
            if isinstance(r, str) and r:
                fps = _parse_fps(r) or DEFAULT_FPS
            d = s0.get("duration")
            if isinstance(d, (str, int, float)):
                try:
                    duration = float(d)
                except (TypeError, ValueError):
                    pass

    fmt = payload.get("format")
    if isinstance(fmt, dict):
        d = fmt.get("duration")
        if isinstance(d, (str, int, float)):
            try:
                duration = float(d)
            except (TypeError, ValueError):
                pass

    return duration, fps


def reset_warning_flag() -> None:
    """Test hook — clear the once-per-process ffprobe-missing warning."""
    global _warned_missing
    _warned_missing = False
