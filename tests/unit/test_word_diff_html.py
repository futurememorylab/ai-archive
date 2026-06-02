from backend.app.services.word_diff import diff_html, word_diff


def test_left_keeps_eq_and_del_strikes_removed():
    segs = word_diff("the brown fox", "the red fox")
    html = str(diff_html(segs, "left"))
    assert '<del class="diff-del">brown</del>' in html
    assert "<ins" not in html
    assert "the " in html and " fox" in html


def test_right_keeps_eq_and_ins_marks_added():
    segs = word_diff("the brown fox", "the red fox")
    html = str(diff_html(segs, "right"))
    assert '<ins class="diff-ins">red</ins>' in html
    assert "<del" not in html


def test_both_keeps_ins_and_del():
    segs = word_diff("the brown fox", "the red fox")
    html = str(diff_html(segs, "both"))
    assert "<del" in html and "<ins" in html


def test_escapes_html_text():
    segs = word_diff("", "<script>")
    html = str(diff_html(segs, "right"))
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_none_segs_is_empty():
    assert str(diff_html(None, "both")) == ""
