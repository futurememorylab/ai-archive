"""Pure-Python mirror of `runButtonLabel()` from studio.js — the
authoritative source for what the Run button says in each state.

The JS implementation in studio.js MUST produce the same string for
the same inputs. Both implementations are short by design.
"""

import pytest

from tests._helpers.studio_state import run_button_label


def test_idle_with_version():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0, cancelled_flash_until_ms=0, now_ms=1000.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "▶ Run on this clip · v3"


def test_idle_with_no_version_uses_question_mark():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0, cancelled_flash_until_ms=0, now_ms=1000.0,
        active_version_num=None, elapsed_label="0:00",
    ) == "▶ Run on this clip · v?"


def test_running_renders_elapsed():
    assert run_button_label(
        running=True, cancelling=False,
        done_flash_until_ms=0, cancelled_flash_until_ms=0, now_ms=5000.0,
        active_version_num=3, elapsed_label="0:05",
    ) == "⟳ Running… 0:05"


def test_running_renders_with_minute_elapsed():
    assert run_button_label(
        running=True, cancelling=False,
        done_flash_until_ms=0, cancelled_flash_until_ms=0, now_ms=90000.0,
        active_version_num=3, elapsed_label="1:30",
    ) == "⟳ Running… 1:30"


def test_cancelling_overrides_running():
    assert run_button_label(
        running=True, cancelling=True,
        done_flash_until_ms=0, cancelled_flash_until_ms=0, now_ms=5000.0,
        active_version_num=3, elapsed_label="0:05",
    ) == "⟳ Cancelling…"


def test_done_flash_when_active_overrides_everything():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=12000.0, cancelled_flash_until_ms=0, now_ms=11500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "✓ Done"


def test_done_flash_expired_returns_to_idle():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=10000.0, cancelled_flash_until_ms=0, now_ms=11500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "▶ Run on this clip · v3"


def test_done_flash_takes_precedence_over_running_mid_transition():
    """Brief moment where running has flipped false and the flash is
    set — label should already read Done."""
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=12000.0, cancelled_flash_until_ms=0, now_ms=11500.0,
        active_version_num=3, elapsed_label="0:42",
    ) == "✓ Done"


@pytest.mark.parametrize("v,expected", [
    (1, "▶ Run on this clip · v1"),
    (10, "▶ Run on this clip · v10"),
    (99, "▶ Run on this clip · v99"),
])
def test_version_number_renders_verbatim(v, expected):
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0, cancelled_flash_until_ms=0, now_ms=0,
        active_version_num=v, elapsed_label="0:00",
    ) == expected


def test_cancelled_flash_renders():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0,
        cancelled_flash_until_ms=2000.0,
        now_ms=1500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "⊘ Cancelled"


def test_cancelled_flash_expires():
    # Past the flash window — fall through to idle label.
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0,
        cancelled_flash_until_ms=1000.0,
        now_ms=2000.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "▶ Run on this clip · v3"


def test_done_flash_wins_over_cancelled_flash():
    # Both set (impossible in production but defensive): Done wins because
    # it appears first in the label function. The JS mirror must match.
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=2000.0,
        cancelled_flash_until_ms=2000.0,
        now_ms=1500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "✓ Done"
