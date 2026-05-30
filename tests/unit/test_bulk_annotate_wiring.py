from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "backend" / "app" / "templates" / "pages"
STATIC = ROOT / "backend" / "app" / "static"


def test_clips_actions_menu_has_annotate_selected():
    html = (TPL / "clips.html").read_text()
    assert "openAnnotate()" in html
    assert "bulkAnnotateMixin()" in html
    assert "_bulk_annotate_modal.html" in html


def test_topbar_has_job_indicator():
    html = (TPL / "_topbar_pills.html").read_text()
    assert "jobsIndicator()" in html


def test_new_scripts_loaded_in_layout():
    html = (TPL / "layout.html").read_text()
    assert "bulkAnnotate.js" in html
    assert "jobsIndicator.js" in html


def test_static_files_exist():
    assert (STATIC / "bulkAnnotate.js").exists()
    assert (STATIC / "jobsIndicator.js").exists()
    assert (TPL / "_bulk_annotate_modal.html").exists()
