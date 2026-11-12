"""Unit tests for the helpers extracted from `routes/pages/clips.clips_list`."""

from __future__ import annotations

from backend.app.routes.pages.clips import _parse_batch_param


def test_no_batch_param_means_no_filter():
    assert _parse_batch_param(None) == ([], "", None)
    assert _parse_batch_param("") == ([], "", None)


def test_single_batch_id_selects_the_dropdown_option():
    assert _parse_batch_param("7") == ([7], "7", 7)


def test_several_ids_are_sorted_for_the_query_and_select_nothing():
    assert _parse_batch_param("12,3, x,5") == ([12, 3, 5], "3,5,12", None)
