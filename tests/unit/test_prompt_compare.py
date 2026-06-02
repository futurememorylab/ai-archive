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
    assert keys == [f"para-{i}" for i in range(len(keys))]
    for r in model["scenes"]:
        if r["cur"]:
            assert "tc" not in r["cur"] and "in_secs" not in r["cur"]


def test_empty_bodies_produce_empty_model():
    model = build_prompt_compare("", "")
    assert model["scene_count"] == 0 and model["scenes"] == []


def test_paragraphs_split_on_blank_lines_not_single_newlines():
    # A single paragraph with internal line breaks stays ONE row.
    cmp = "line one\nline two\nline three"
    cur = "line one\nline two changed\nline three"
    model = build_prompt_compare(cur, cmp)
    assert model["scene_count"] == 1
    assert model["scenes"][0]["status"] == "changed"
