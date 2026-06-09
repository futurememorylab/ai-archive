"""Pure-Python mirror of studio.js's runButtonLabel().

Keep this function ≤ 15 lines and verbatim-equivalent to the JS in
backend/app/static/studio.js. When the JS changes, this file changes
in the same commit; both implementations are reviewed together.
"""

from __future__ import annotations


def run_button_label(
    *,
    running: bool,
    cancelling: bool,
    done_flash_until_ms: float,
    cancelled_flash_until_ms: float,
    now_ms: float,
    active_version_num: int | None,
    elapsed_label: str,
    selected_count: int = 0,
    bulk_running: bool = False,
    bulk_done: int = 0,
    bulk_total: int = 0,
) -> str:
    if done_flash_until_ms and now_ms < done_flash_until_ms:
        return "✓ Done"
    if cancelled_flash_until_ms and now_ms < cancelled_flash_until_ms:
        return "⊘ Cancelled"
    if cancelling:
        return "⟳ Cancelling…"
    if bulk_running:
        return f"⟳ Running… {bulk_done}/{bulk_total}"
    if running:
        return f"⟳ Running… {elapsed_label}"
    v = active_version_num if active_version_num is not None else "?"
    if selected_count > 0:
        s = "" if selected_count == 1 else "s"
        return f"▶ Run on {selected_count} clip{s} · v{v}"
    return f"▶ Run on this clip · v{v}"
