"""SMPTE timecode helpers — non-drop-frame conversion between seconds
and HH:MM:SS:FF, plus frame snapping."""

import re

_SMPTE_RE = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2}):(\d{2})$")


def secs_to_smpte(secs: float, fps: float) -> str:
    """Convert seconds to HH:MM:SS:FF using non-drop-frame counting."""
    total_frames = round(secs * fps)
    frames_per_sec = round(fps)
    ff = total_frames % frames_per_sec
    total_secs = total_frames // frames_per_sec
    ss = total_secs % 60
    mm = (total_secs // 60) % 60
    hh = total_secs // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def smpte_to_secs(smpte: str, fps: float) -> float:
    m = _SMPTE_RE.match(smpte.strip())
    if not m:
        raise ValueError(f"invalid SMPTE timecode: {smpte!r}")
    hh, mm, ss, ff = (int(x) for x in m.groups())
    frames_per_sec = round(fps)
    total_frames = ((hh * 3600 + mm * 60 + ss) * frames_per_sec) + ff
    return total_frames / fps


def snap_to_frame(secs: float, fps: float) -> float:
    """Round secs to the nearest whole-frame boundary at fps."""
    return round(secs * fps) / fps
