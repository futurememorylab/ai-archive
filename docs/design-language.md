# Design language

The catalog of the small reusable UI library this app ships. **Before
writing any frontend, read this and reuse what's here.** The whole point
of this document is so future agents extend these primitives instead of
hand-rolling a second button, a second form field, or another timecode
formatter.

## 1. Overview

This is a **no-build** stack (ADR 0001): no bundler, no JSX, no CSS
preprocessor. Styling is plain CSS custom properties + classes; reusable
markup is Jinja macros; shared client logic is plain `window.*` globals.
Three places hold everything:

| What | Where | How you use it |
|---|---|---|
| Design tokens + component classes | `backend/app/static/app.css` (`:root` + `.btn`/`.field`/`.pill`/…) | reference tokens, apply classes |
| Reusable markup macros | `backend/app/templates/components/_ui.html` | `{% import "components/_ui.html" as ui %}` then `{{ ui.button(...) }}` |
| Formatting / autosize helpers | `backend/app/static/format.js` | `window.fmtTimecode(...)`, `window.fmtBytes(...)`, `window.autosize(...)` |

Rule of thumb: if you're typing a raw hex color, a `*-btn` class, a
`padStart` timecode, or a `style="..."` on a form control, stop — there
is already a primitive for it below.

## 2. Design tokens

Defined in the `:root` block at the top of `app.css`. **Use the token,
never the raw value** — re-skinning (e.g. the draft pane re-scopes
`--accent` to blue) only works because everything reads the variable.

### Color

| Token | Value | Purpose |
|---|---|---|
| `--bg` | `#0b0d10` | app background |
| `--bg-2` | `#0f1216` | inset backgrounds (inputs, timeline track) |
| `--panel` | `#14181d` | panels, topbar, rail, headers |
| `--panel-2` | `#181d23` | nested/elevated panel (menus, read-only fields) |
| `--surface` | `#1d232a` | default button / chip surface |
| `--surface-2` | `#232a32` | button hover, raised chip |
| `--hover` | `rgba(255,255,255,0.04)` | row / item hover wash |
| `--line` | `rgba(255,255,255,0.07)` | hairline borders, dividers |
| `--line-2` | `rgba(255,255,255,0.12)` | stronger border (buttons, menus) |
| `--line-3` | `rgba(255,255,255,0.20)` | strongest border |
| `--text` | `#e6e9ee` | primary text |
| `--text-2` | `#b6bcc6` | secondary text |
| `--text-3` | `#7e8693` | muted / labels |
| `--text-4` | `#545b66` | faintest / placeholders |
| `--accent` | `#f5a623` | brand amber: primary actions, active state |
| `--accent-2` | `color-mix(--accent 30%, transparent)` | accent glow / focus ring |
| `--accent-fg` | `#14181d` | text on an accent fill |
| `--good` | `#3ddc84` | success / ok |
| `--bad` | `#ff5d5d` | error / danger / destructive |
| `--info` | `#5ac8fa` | info; also the draft colorway |
| `--range-cur` | `color-mix(--info 45%, transparent)` | studio player overlay: cur version's scene ranges |
| `--range-cmp` | `color-mix(--accent 45%, transparent)` | studio player overlay: cmp version's scene ranges |

> The two `--range-*` tokens are studio-specific affordances but live
> in `:root` alongside the palette so they track future palette
> shifts. Legend dots reuse `--info` / `--accent` directly (no alpha
> mix). PR3 introduced these to replace hardcoded `rgba(74,144,226,…)`
> / `rgba(220,140,60,…)` strings.

### Radii, fonts, component sizing

| Token | Value | Purpose |
|---|---|---|
| `--r-1` | `4px` | tight radius (menu items, tags) |
| `--r-2` | `6px` | default control radius (buttons, fields) |
| `--r-3` | `10px` | large radius (metric cards, prominent search) |
| `--f-sans` | `"Inter", system-ui, …` | UI text |
| `--f-mono` | `"JetBrains Mono", ui-monospace, …` | timecodes, IDs, numbers, code |
| `--btn-h` | `32px` | standard button / field height |
| `--btn-h-sm` | `26px` | small (`.sm`) button height |
| `--field-h` | `32px` | form input height |
| `--field-label-h` | `24px` | form label row height |

## 3. Buttons

The `.btn` system is the only button vocabulary. Base `.btn` plus
modifiers — combine freely:

| Modifier | Effect |
|---|---|
| `.primary` | accent fill (the main action) |
| `.ghost` | transparent, hairline border (secondary) |
| `.danger` | red text/border (destructive) |
| `.sm` | small height (`--btn-h-sm`) |
| `.icon` | square, no horizontal padding (icon-only) |
| `.icon.sm` | small square |
| `.is-disabled` / `:disabled` | dimmed, non-interactive (also `.disabled`) |

Prefer the macro, which assembles the class string for you and renders an
`<a>` when given `href`, otherwise a `<button>`:

```jinja
{{ ui.button('Create prompt', variant='primary', type='submit') }}
{{ ui.button('Cancel', href='/prompts', variant='ghost') }}
```

Signature: `button(label, href=None, variant='', size='', type='button', attrs='')`.
`variant` takes the modifier(s) (`'primary'`, `'ghost'`, `'danger'`),
`size` takes `'sm'`, `attrs` is raw attribute markup (e.g. `hx-post=...`).

**Do NOT create `*-btn` classes.** If a button needs a one-off look, add a
modifier to the `.btn` system in `app.css`, don't invent a parallel class.

## 4. Form fields

A field is `.field` (column wrapper) → `.field-label` (the label row) →
the control (`.txt` for inputs, `.txt-area` for textareas) → optional
`.field-help` hint. Use the macros — they wrap everything in the right
`<label>`:

```jinja
{{ ui.field('Name', 'name', value=form.name, input_attrs='required') }}
{{ ui.textarea_field('Prompt', 'body', value=form.body, min_height='130px') }}
{{ ui.textarea_field('target_map (JSON)', 'target_map',
                     value=form.target_map_text, cls='json-editor') }}
```

Signatures:

- `field(label, name, value='', type='text', help='', input_attrs='', cls='')`
- `textarea_field(label, name, value='', help='', min_height='120px', input_attrs='', cls='')`

Knobs:

- `cls=` — extra class on the control, e.g. `cls='json-editor'` for the
  monospace JSON variant (`.txt-area.json-editor`).
- `input_attrs=` — raw attributes on the input/textarea, used for
  validation and Alpine/htmx bindings (e.g.
  `input_attrs='data-item-id="..." data-k="name"'`).
- `help=` — hint markup rendered as `.field-help` below the control
  (rendered with `|safe`, so it may contain markup).

**No inline label hacks, no per-field inline styles.** The only inline
style the macro itself sets is `min-height` on textareas (via
`min_height=`); everything else comes from the classes.

## 5. Page header & breadcrumb

These cover two distinct surfaces — don't conflate them:

- **Top-bar breadcrumb** = the navigational path in the global topbar
  (`{% block crumb %}`). Leaf segment (with `href=None`) is the current
  page. Built with the `breadcrumb` macro from a list of
  `(label, href|None)` tuples:

  ```jinja
  {% block crumb %}{{ ui.breadcrumb([('All clips', '/'), (clip.name, None)]) }}{% endblock %}
  {% block crumb %}{{ ui.breadcrumb([('Prompts', '/prompts'), ('New', None)]) }}{% endblock %}
  ```

  Signature: `breadcrumb(segments)`. A tuple with a truthy href renders an
  `<a>`; `None` renders the bold current-page `.strong` segment.

- **Body page header** = the in-page `.page-hdr` strip: title + meta +
  right-aligned actions. Use the `page_header` macro; pass actions via a
  `{% call %}` block (which fills the macro's `caller()` slot, right of a
  `.grow` spacer):

  ```jinja
  {% call ui.page_header('Prompts', meta='%d active' % prompts|length) %}
    {{ ui.button('Archived', href='/prompts/archived', variant='ghost') }}
    {{ ui.button('+ New prompt', href='/prompts/new', variant='primary') }}
  {% endcall %}
  ```

  Signature: `page_header(title, meta='')`. (Several existing pages still
  inline the raw `.page-hdr` div — the macro is the canonical form for new
  pages.)

## 6. Status pill

`status_pill` renders a status chip with a colored LED dot:

```jinja
{{ ui.status_pill('Online', state='ok') }}
```

Signature: `status_pill(label, state='')`. `state='ok'` turns it green
(`--good`) with a glowing LED. It's the `.pill` (+ `.pill.ok`, `.led`)
class underneath.

**Pills are status indicators, not action buttons.** They show state; if
the user can click it to *do* something, that's a `.btn`. (The
connection-chip in the topbar uses a sibling variant, `.env-pill`, for the
same look on the live connection indicator — reuse that for connection
state rather than re-styling a pill.)

### Prompt state chip

`prompt_state_chip(state)` is the single shared chip for a prompt
version's `draft` / `production` / `archived` state, used by the prompt
editor (`_prompt_detail.html`) and Studio (`_studio_version_picker.html`):

```jinja
{{ ui.prompt_state_chip(selected_version.state) }}
```

It renders the `.tag` look (`.tag.accent` draft, `.tag.good` production,
`.tag.muted` archived) with a dot, and a **lock icon for production and
archived** (the read-only states) via `icons/_lock.svg`. Draft is
editable and shows no lock. Reuse this macro wherever a version state is
shown — do not re-inline the `if/elif` chip.

## 7. JS helpers (`format.js`)

Loaded first in `layout.html` (right after htmx, before player/feature
scripts), so `window.*` exist before anything initializes. Three globals:

- `window.fmtTimecode(seconds)` → `"m:ss"` or `"h:mm:ss"`.
- `window.fmtBytes(n)` → human size (`"1.5 GB"`, `"0 B"`).
- `window.autosize(textarea)` → grow a textarea to fit its content.

`format.js` also auto-wires autosize: any `textarea.txt-area` grows on
`input` and on `DOMContentLoaded` automatically — you get it free by using
`.txt-area` (i.e. `ui.textarea_field`).

**Never re-implement timecode or byte formatting — call these.** A
divergent formatter means two clips show the same duration differently.

## 8. Menus & popovers

One dropdown vocabulary — a trigger that opens a floating panel of items
that dismisses on click-outside / `Esc`. **Do not hand-roll a new
`*-menu` class or a second `x-data="{ open: false }"` toggle.** The three
pieces:

| What | Where | How you use it |
|---|---|---|
| Open/close behavior | `static/popover.js` (`Alpine.data("popover")`) | `x-data="popover()"` → `open` / `toggle()` / `close()` |
| Panel + item classes | `app.css` (`.popover-panel`, `.menu`, `.menu-item`, …) | apply classes |
| Markup macros | `components/_ui.html` (`menu`, `menu_item`, `menu_sep`, `menu_header`) | `{% call ui.menu(...) %}…{% endcall %}` |

### Standard menu (plain `.btn` trigger)

```jinja
{% call ui.menu(label='v' ~ version.version_num, trigger_cls='mono-cell') %}
  {{ ui.menu_item('Promote', post='/prompts/%d/_promote' % p.id) }}
  {{ ui.menu_item('Open in Studio', href='/studio?prompt_id=%d' % p.id) }}
  {{ ui.menu_sep() }}
  {{ ui.menu_item('Archive', post='/prompts/%d/_archive' % p.id, danger=true) }}
{% endcall %}
```

`menu_item` picks the element from exactly one of `post=` (a
`<form>`+submit), `href=` (an `<a>`), or neither (a `<button>`, with
`action=` for an `@click` expression). Knobs: `danger`, `current`
(active row), `meta` (right-aligned mono), `desc` (sub-line), `icon`,
`attrs` (raw `hx-*` / `data-*`).

### Hosted mode (menu inside a larger component)

When the menu lives inside a component that already owns the open flag
(`studioPage`, `bulkSel`, …), pass `state=` so the macro binds to that
flag and emits **no `x-data`** — a nested `x-data` would *shadow* the
parent scope (e.g. `studioPage.focusedClipId` would read `undefined`):

```jinja
{# inside x-data="bulkSel()", which declares annoOpen #}
{% call ui.menu(label='Annotate', variant='primary', state='annoOpen') %}…{% endcall %}
```

### Bespoke trigger

When the trigger isn't a plain labeled button (a chip, a title button),
skip `menu()` and use the pieces directly — still `popover()` for
behavior and `.popover-panel`/`.menu` for the panel:

```jinja
<span class="pc-vchip" x-data="popover()"
      @click.outside="close()" @keydown.escape.window="close()">
  <button class="btn sm ghost" :class="open && 'open'" @click="toggle()">…</button>
  <div class="popover-panel menu" x-show="open" x-cloak>
    {{ ui.menu_header('versions') }}
    <button class="menu-item" @click="close()">…</button>
  </div>
</span>
```

A new bespoke `*-menu` class fails CI (`tests/unit/test_design_language_guard.py`).

## 9. Modals

One modal vocabulary — a fixed overlay + click-backdrop + centered card with
escape-to-close. **Do not hand-roll a `modal-overlay` / `modal-dialog` or a
second modal shell.** The pieces:

| Class | Purpose |
|---|---|
| `.modal` | fixed overlay (flex-centers the card) |
| `.modal-backdrop` | dim layer; click to close |
| `.modal-card` | the dialog box (`.sm` = narrow form; `.nb-card` = wide picker) |
| `.modal-hdr` + `.modal-title` | header row + title |
| `.modal-body` | scrollable content |
| `.modal-actions` | footer button row |

```jinja
{% call ui.modal('dupOpen', label='Duplicate prompt', card_cls='sm') %}
  <form @submit.prevent="duplicate()">
    <div class="modal-body">
      {{ ui.field('Name', 'dupName', input_attrs='x-model="dupName"') }}
    </div>
    <div class="modal-actions">
      <button type="button" class="btn ghost" @click="dupOpen = false">Cancel</button>
      <button type="submit" class="btn primary">Duplicate</button>
    </div>
  </form>
{% endcall %}
```

`ui.modal(state, label='', card_cls='')` owns the overlay + backdrop + escape;
`state` is the Alpine flag that shows it. Pass `label` for a default titled
header, or omit it and put a custom `.modal-hdr` in the body (e.g. a
selected-count). Use `.field` / `ui.field` for form fields inside — there is no
`modal-field` / `modal-label`. A modal with a bespoke lifecycle (HTMX-injected,
no flag — the archive picker) uses the `.modal-*` classes directly with its own
`@click` / escape wiring.

A new `modal-*` class outside this vocabulary fails CI.

## 10. Red flags — stop and reuse

If you catch yourself doing any of these, stop:

- **Writing `class="save-btn"` / any `*-btn` class.** → Use `.btn` +
  modifier, or the `{{ ui.button(...) }}` macro. Add a modifier to the
  `.btn` system if you need a new look.
- **Putting `style="..."` on a form input or its label.** → Use `.field`
  / `.field-label` / `.txt` / `.txt-area`, or `ui.field` /
  `ui.textarea_field`. Pass `cls=` / `input_attrs=` for variation.
- **An `mm:ss` string built with `padStart`.** → `window.fmtTimecode(seconds)`.
- **A `while (n >= 1024)` byte-scaling loop.** → `window.fmtBytes(n)`.
- **A hand-written `<span class="crumb">…</span>` or a bespoke title
  bar `<div>`.** → `ui.breadcrumb([...])` for the topbar path,
  `ui.page_header(...)` for the in-page title+actions strip.
- **A raw hex color in CSS or an inline style.** → Use a `--token`. If no
  token fits, the design is introducing a new color — flag it, don't
  inline it.
- **Adding a `setInterval` to resize a textarea.** → `.txt-area` autosizes
  itself via `format.js`; just use the class.
- **Writing a new `*-menu` class or a second `x-data="{ open: false }"`
  dropdown toggle.** → Use `{{ ui.menu(...) }}` / `ui.menu_item`, or
  `popover()` + `.popover-panel` / `.menu` for a bespoke trigger (§8).
  A new `*-menu` / `*-btn` class fails CI.
- **Writing a `modal-overlay` / `modal-dialog` or a new modal shell.** →
  `{% call ui.modal(state, label) %}` + `.modal-body` / `.modal-actions`, and
  `.field` / `ui.field` for form fields (§9). A new `modal-*` class fails CI.
