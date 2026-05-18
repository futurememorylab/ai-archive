import pytest

from backend.app.timecode import secs_to_smpte, smpte_to_secs, snap_to_frame


@pytest.mark.parametrize(
    "secs,fps,expected",
    [
        (0.0, 25, "00:00:00:00"),
        (1.0, 25, "00:00:01:00"),
        (1.04, 25, "00:00:01:01"),
        (60.0, 25, "00:01:00:00"),
        (3600.0, 25, "01:00:00:00"),
        (10.0, 24, "00:00:10:00"),
        (10.0, 30, "00:00:10:00"),
    ],
)
def test_secs_to_smpte_basic(secs, fps, expected):
    assert secs_to_smpte(secs, fps) == expected


@pytest.mark.parametrize(
    "smpte,fps,expected",
    [
        ("00:00:00:00", 25, 0.0),
        ("00:00:01:00", 25, 1.0),
        ("00:00:01:12", 25, 1.48),
        ("00:01:00:00", 25, 60.0),
    ],
)
def test_smpte_to_secs_basic(smpte, fps, expected):
    assert smpte_to_secs(smpte, fps) == pytest.approx(expected, abs=1e-9)


def test_round_trip():
    for frames in range(0, 5000, 7):
        secs = frames / 25.0
        assert smpte_to_secs(secs_to_smpte(secs, 25), 25) == pytest.approx(secs, abs=1e-9)


def test_snap_to_frame_rounds_down_within_half_frame():
    assert snap_to_frame(1.039, 25) == pytest.approx(1.04, abs=1e-9)
    assert snap_to_frame(1.0, 25) == pytest.approx(1.0, abs=1e-9)


def test_smpte_to_secs_rejects_garbage():
    with pytest.raises(ValueError):
        smpte_to_secs("not-a-timecode", 25)
