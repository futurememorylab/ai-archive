from backend.app.services.live_context import build_context_text


def _published_block_present(out: str) -> bool:
    return "=== Publikované anotace (z CatDV) ===" in out


def _draft_block_present(out: str) -> bool:
    return "=== Rozpracované anotace (můj draft" in out


def _make_clip(**overrides):
    base = dict(
        id=42,
        name="P1010001",
        format="9,5 mm",
        fps=25,
        duration_secs=120.0,
        duration_smpte="00:02:00:00",
        notes="rodinný výlet",
        big_notes="dlouhý popis...",
        markers=[
            dict(
                in_secs=0.0,
                out_secs=10.0,
                in_smpte="00:00:00:00",
                out_smpte="00:00:10:00",
                name="Otevírací záběr",
                description="auto u domu",
            ),
        ],
        fields={
            "pragafilm.rok.natočení": ["1928", "1929"],
            "pragafilm.dekáda.natočení": "20.léta",
            "pragafilm.barva": "false",
        },
    )
    base.update(overrides)
    return base


def _make_draft(**overrides):
    base = dict(
        markers=[
            dict(
                in_secs=5.0,
                out_secs=8.0,
                in_smpte="00:00:05:00",
                out_smpte="00:00:08:00",
                name="možná Praha?",
                description="ulice s tramvají",
            ),
        ],
        fields={"pragafilm.popis.materialu": "rodinné video, ulice"},
        notes="myslím, že je to Praha 1928",
    )
    base.update(overrides)
    return base


def test_both_blocks_present_with_full_data():
    out = build_context_text(_make_clip(), _make_draft())
    assert _published_block_present(out)
    assert _draft_block_present(out)
    assert "P1010001" in out
    assert "00:02:00:00" in out
    assert "rodinný výlet" in out
    assert "00:00:00:00 – 00:00:10:00" in out
    assert "Otevírací záběr" in out
    assert "pragafilm.rok.natočení: 1928, 1929" in out
    assert "možná Praha?" in out
    assert "myslím, že je to Praha 1928" in out
    assert "(Konec kontextu." in out


def test_omits_empty_draft_block():
    draft = dict(markers=[], fields={}, notes="")
    out = build_context_text(_make_clip(), draft)
    assert _published_block_present(out)
    assert not _draft_block_present(out)


def test_omits_empty_published_block():
    clip = _make_clip(
        notes="",
        big_notes="",
        markers=[],
        fields={},
    )
    out = build_context_text(clip, _make_draft())
    assert _published_block_present(out)
    assert "Poznámky:" not in out
    assert "Markery" not in out
    assert "Vlastní pole" not in out
    assert _draft_block_present(out)


def test_minimal_clip_no_draft():
    clip = _make_clip(
        notes="",
        big_notes="",
        markers=[],
        fields={},
    )
    out = build_context_text(clip, dict(markers=[], fields={}, notes=""))
    assert _published_block_present(out)
    assert not _draft_block_present(out)
    assert "P1010001" in out
    assert "(Konec kontextu." in out


def test_mojibake_in_published_notes_is_fixed():
    bad_notes = "rodinnÃ½ vÃ½let"
    clip = _make_clip(notes=bad_notes)
    out = build_context_text(clip, dict(markers=[], fields={}, notes=""))
    assert "rodinný výlet" in out
    assert bad_notes not in out


def test_mojibake_in_draft_marker_description_is_fixed():
    draft = _make_draft(
        markers=[
            dict(
                in_secs=0,
                out_secs=1,
                in_smpte="00:00:00:00",
                out_smpte="00:00:01:00",
                name="x",
                description="ulice s tramvajÃ­",
            ),
        ],
    )
    out = build_context_text(_make_clip(), draft)
    assert "ulice s tramvají" in out


def test_pragafilm_fields_only_listed_when_value_non_empty():
    clip = _make_clip(
        fields={
            "pragafilm.barva": "",
            "pragafilm.dekáda.natočení": "20.léta",
            "pragafilm.rok.natočení": [],
            "pragafilm.popis.materialu": None,
        }
    )
    out = build_context_text(clip, dict(markers=[], fields={}, notes=""))
    assert "pragafilm.dekáda.natočení: 20.léta" in out
    assert "pragafilm.barva" not in out
    assert "pragafilm.rok.natočení" not in out
    assert "pragafilm.popis.materialu" not in out
