"""Python mirror of the JS clampSize helper. Tests pin the clamping
shape; the JS version (backend/app/static/studioResize.js) is a
character-for-character port and shares these fixtures.

clampSize(start, delta, lo, hi) -> the new size (start + delta) clamped
to the inclusive range [lo, hi]. Used by the studio split-pane resizer
to keep a pane from collapsing to 0 or pushing its neighbour offscreen.
"""


def clamp_size(start: float, delta: float, lo: float, hi: float) -> float:
    """Mirror of studioResize.js::clampSize."""
    size = start + delta
    if size < lo:
        return lo
    if size > hi:
        return hi
    return size


def test_within_range_returns_start_plus_delta():
    assert clamp_size(320, 40, 160, 800) == 360


def test_negative_delta_within_range():
    assert clamp_size(320, -40, 160, 800) == 280


def test_clamps_to_lower_bound():
    assert clamp_size(200, -300, 160, 800) == 160


def test_clamps_to_upper_bound():
    assert clamp_size(700, 300, 160, 800) == 800


def test_exactly_at_bounds_is_kept():
    assert clamp_size(160, 0, 160, 800) == 160
    assert clamp_size(800, 0, 160, 800) == 800


def test_percentage_split_clamps_low():
    # cmp split is a percentage, clamped to 20..80
    assert clamp_size(50, -40, 20, 80) == 20


def test_percentage_split_clamps_high():
    assert clamp_size(50, 40, 20, 80) == 80


def test_percentage_split_within_range():
    assert clamp_size(50, 10, 20, 80) == 60
