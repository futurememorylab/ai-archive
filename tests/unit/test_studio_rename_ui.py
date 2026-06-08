from pathlib import Path

CARD = Path("backend/app/templates/pages/_studio_set_card.html").read_text()
JS = Path("backend/app/static/studio.js").read_text()


def test_card_has_rename_affordance():
    assert "renameSet(" in CARD


def test_studiosets_has_renameset_method():
    assert "renameSet(" in JS
    assert "method: 'PATCH'" in JS
    assert "/api/studio/sets/" in JS
