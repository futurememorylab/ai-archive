# 0011. Prompt management: post-merge polish (styling, alpine init, duplicate dialog)

- **Date:** 2026-05-21
- **Status:** Accepted
- **Lifespan:** Feature

## Context

First hands-on session with the shipped Prompts UI surfaced
three issues: (a) the body textarea and the `<button>`-based "model" and
"version" pills rendered with the browser-default white background on the
dark theme — the only existing CSS rule for `.txt` was a `.filters-form
input` rule scoped to a different page; (b) the kebab menu and model
picker were dead — Chrome console showed `menuOpen is not defined`,
because `promptEditor.js` was loaded inside `{% block body %}` (after
Alpine) but registered an `alpine:init` listener that had already fired;
(c) "Duplicate" was a one-click form post that always produced "Copy of
X", giving no chance to rename or adjust the description for the new
prompt.

## Alternatives

For (a): add a generic `.txt` rule, scope textarea
styling under `.prompts-page`, or set inline styles in the template.
For (b): add a `{% block head_scripts %}` to layout.html and override per
page, restructure `promptEditor.js` to call `Alpine.data(...)` directly if
Alpine is already on the page, or load it in `<head>` next to player.js.
For (c): a separate `/prompts/{id}/duplicate` form page, an inline
expanding section in the detail pane, or a modal dialog.

## Decision

(a) A generic top-level `.txt` rule (dark bg, light text,
focus highlight, read-only state) since `.txt` is project-internal and
used only on the prompts pages; plus `button.tag { background:
transparent }` so any future `<button class="tag …">` inherits the
correct look. (b) Add `promptEditor.js` to the head `<script defer>`
chain *before* the Alpine bundle — matches the established `player.js`
pattern and the explicit comment in `layout.html` ("listener must
register first"). (c) A modal dialog with Name + Description fields,
opened from the kebab via `openDuplicate()`, submitted by `fetch()`. On
422/409 the modal stays open and shows an inline error pill; on success
the response's 303 redirect is followed by the browser.

## Consequences

(a) The "fix it via inline style" path would scatter
background/color tokens across templates — Tag inline styles are already
used for layout, but theming belongs in CSS where the design tokens live.
(b) Either restructure-loading-order or restructure-the-listener works;
loading-order is one line and consistent with the existing convention,
so future scripts that need `alpine:init` have an obvious place to go.
(c) `PromptsRepo.duplicate` already had the "next-available `Copy of X`"
walker; the new contract is: `name=None` → keep the walker (preserves
the existing tests and the API's REST-default behavior), `name=...` →
use as-is and let `aiosqlite.IntegrityError` surface as 409. The page
action returns 409 JSON instead of 303 only on the failure path so the
modal can keep the user's typed values; the success path is still 303
to `/prompts/{new_pid}` (matches every other page action in the file).
The dialog approach matches what users expect from "Duplicate…" with an
ellipsis affordance — explicit on the menu item.

**Out of scope (deliberately).** Did not refactor the Alpine component to
register itself idempotently regardless of script order — load order is
the smaller, more localized fix. Did not rename `.txt` to something more
descriptive (e.g. `.field-input`) — that's a follow-up across the
templates that touch it, not a one-line CSS change.
