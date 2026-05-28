"""The :root block must define the studio range overlay tokens used by
.ranges.range-cur and .ranges.range-cmp. PR3 introduces these so the
range colors track the palette instead of hardcoded rgba."""

from pathlib import Path


def test_root_defines_range_cur_and_range_cmp():
    css = Path("backend/app/static/app.css").read_text()
    root_block = css.split(":root", 1)[1].split("}", 1)[0]
    assert "--range-cur:" in root_block, "missing --range-cur in :root"
    assert "--range-cmp:" in root_block, "missing --range-cmp in :root"


def test_range_tokens_use_color_mix_with_existing_tokens():
    css = Path("backend/app/static/app.css").read_text()
    root_block = css.split(":root", 1)[1].split("}", 1)[0]
    cur_line = next(
        ln for ln in root_block.splitlines() if "--range-cur" in ln
    )
    cmp_line = next(
        ln for ln in root_block.splitlines() if "--range-cmp" in ln
    )
    assert "color-mix" in cur_line and "var(--info)" in cur_line
    assert "color-mix" in cmp_line and "var(--accent)" in cmp_line
