"""Batch naming rules (docs/specs/2026-10-17-batches-redesign-design.md).
Migration 0032 backfills old batches with the same content rule in SQL."""

from backend.app.services.batch_names import MAX_LEN, clean_batch_name, default_batch_name


def test_single_clip_is_named_after_the_clip():
    assert default_batch_name("Výlet do Tater", 101, 1) == "Výlet do Tater"


def test_several_clips_add_and_n_more():
    assert default_batch_name("Výlet do Tater", 101, 24) == "Výlet do Tater and 23 more"


def test_uncached_clip_falls_back_to_its_id():
    assert default_batch_name(None, 999, 1) == "Clip 999"
    assert default_batch_name("  ", 999, 3) == "Clip 999 and 2 more"


def test_clean_trims_and_collapses_whitespace():
    assert clean_batch_name("  Tatry \n 1934 ") == "Tatry 1934"


def test_clean_rejects_empty_and_caps_length():
    assert clean_batch_name("   ") is None
    assert clean_batch_name(None) is None
    assert clean_batch_name("x" * 500) == "x" * MAX_LEN
