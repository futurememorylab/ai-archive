from backend.app.services.prompt_compare import build_prompt_compare


def _body(*paras):
    return "\n\n".join(paras)


def test_identical_bodies_all_unchanged():
    b = _body("Intro line.", "Second paragraph here.", "Third.")
    model = build_prompt_compare(b, b)
    assert model["scene_count"] == 3
    assert {r["status"] for r in model["scenes"]} == {"unchanged"}
    assert model["fields"] == [] and model["notes"] is None


def test_changed_paragraph_is_paired_with_word_diff():
    cmp = _body("Intro.", "The quick brown fox.", "End.")
    cur = _body("Intro.", "The quick red fox.", "End.")
    model = build_prompt_compare(cur, cmp)
    assert [r["status"] for r in model["scenes"]] == ["unchanged", "changed", "unchanged"]
    changed = model["scenes"][1]
    types = {s["type"] for s in changed["segs"]}
    assert "del" in types and "ins" in types
    assert changed["cmp"] is not None and changed["cur"] is not None


def test_added_paragraph_only_on_cur():
    cmp = _body("A.", "B.")
    cur = _body("A.", "NEW.", "B.")
    model = build_prompt_compare(cur, cmp)
    statuses = [r["status"] for r in model["scenes"]]
    assert "added" in statuses
    added = next(r for r in model["scenes"] if r["status"] == "added")
    assert added["cmp"] is None and added["cur"] is not None


def test_removed_paragraph_only_on_cmp():
    cmp = _body("A.", "GONE.", "B.")
    cur = _body("A.", "B.")
    model = build_prompt_compare(cur, cmp)
    statuses = [r["status"] for r in model["scenes"]]
    assert "removed" in statuses
    removed = next(r for r in model["scenes"] if r["status"] == "removed")
    assert removed["cmp"] is not None and removed["cur"] is None


def test_rows_carry_no_timecode_and_stable_keys():
    model = build_prompt_compare(_body("A.", "B."), _body("A.", "C."))
    keys = [r["key"] for r in model["scenes"]]
    assert keys == [f"line-{i}" for i in range(len(keys))]
    for r in model["scenes"]:
        if r["cur"]:
            assert "tc" not in r["cur"] and "in_secs" not in r["cur"]


def test_empty_bodies_produce_empty_model():
    model = build_prompt_compare("", "")
    assert model["scene_count"] == 0 and model["scenes"] == []


def test_inserted_paragraph_does_not_mispair_a_later_edit():
    # Regression for the old exact-LCS + positional-zip alignment: inserting a
    # paragraph at the top used to shift everything and pair unrelated
    # paragraphs as "changed". With similarity-based alignment, "Hello" is a
    # clean insertion and the lightly-edited intro stays a tight CHANGED.
    cmp = _body(
        "Big intro paragraph about the archive.",
        "Identify scenes here.",
        "Footer note.",
    )
    cur = _body(
        "Hello",
        "Big intro paragraph about the silent archive.",
        "Identify scenes here.",
        "Footer note.",
    )
    model = build_prompt_compare(cur, cmp)
    assert [r["status"] for r in model["scenes"]] == [
        "added", "changed", "unchanged", "unchanged",
    ]
    changed = model["scenes"][1]
    types = {s["type"] for s in changed["segs"]}
    assert "eq" in types and "ins" in types  # shared words kept, "silent" added


def test_wholesale_rewrite_in_place_is_a_single_changed_row():
    # A 1:1 replaced paragraph (even heavily rewritten) stays one CHANGED row
    # in its slot, not a mispaired mess.
    cmp = _body("Alpha.", "Completely different old text.", "Omega.")
    cur = _body("Alpha.", "Totally fresh new wording instead.", "Omega.")
    model = build_prompt_compare(cur, cmp)
    assert [r["status"] for r in model["scenes"]] == [
        "unchanged", "changed", "unchanged",
    ]


def test_splits_on_lines_so_a_one_line_edit_stays_localized():
    # Line granularity (not blank-line paragraphs): only the edited line is a
    # CHANGED row; its unchanged neighbours stay unchanged. This is the fix for
    # bodies that don't use blank-line separators consistently.
    cmp = "line one\nline two\nline three"
    cur = "line one\nline two changed\nline three"
    model = build_prompt_compare(cur, cmp)
    assert model["scene_count"] == 3
    assert [r["status"] for r in model["scenes"]] == [
        "unchanged", "changed", "unchanged",
    ]


def test_blank_line_differences_do_not_misalign_shared_lines():
    # One body groups its lines into blank-line blocks, the other doesn't —
    # the shared lines must still align (the real-world v8/v9 failure).
    cmp = "Alpha\n\nBeta\n\nGamma"          # blank-line separated
    cur = "Alpha\nBeta\nGamma"              # same lines, no blanks
    model = build_prompt_compare(cur, cmp)
    assert [r["status"] for r in model["scenes"]] == ["unchanged"] * 3
