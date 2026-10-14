"""Shared actions + assertions for the catalog-picker scenarios (#124).

Underscore prefix → the scenario loader skips this module. The picker is a
`ui.menu` lazily loaded into the topbar's #catalog-picker; each option is a
form-submit button posting to /api/catalog/active, which redirects to `/`.
"""

from __future__ import annotations

from playwright.sync_api import expect

TRIGGER = '[data-test="catalog-picker-trigger"]'


def expect_active_catalog(p, name: str) -> None:
    expect(p.locator(TRIGGER)).to_contain_text(name)


def open_picker(p) -> None:
    p.locator(TRIGGER).click()
    expect(p.locator('[data-test^="catalog-option-"]').first).to_be_visible()


def expect_option(p, catalog_id: str, *, current: bool) -> None:
    opt = p.locator(f'[data-test="catalog-option-{catalog_id}"]')
    expect(opt).to_be_visible()
    if current:
        expect(opt).to_have_class("menu-item is-current")
    else:
        expect(opt).to_have_class("menu-item")


def switch_to(p, catalog_id: str) -> None:
    open_picker(p)
    with p.expect_navigation():
        p.locator(f'[data-test="catalog-option-{catalog_id}"]').click()


def expect_breadcrumb(p, text: str) -> None:
    expect(p.locator(".topbar .crumb")).to_contain_text(text)
