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

## 8. Red flags — stop and reuse

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
