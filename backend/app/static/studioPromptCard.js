/* Studio prompt card — `Alpine.data('studioPromptCard')`.

   One card per side of the Studio prompt pane: `cur` (the active version,
   editable while it is a draft) and `cmp` (the compare version, injected by
   the store's openCompare()). Page state is read through
   `Alpine.store('studio')` (studioStore.js); see `_page()` below for why.
*/

document.addEventListener('alpine:init', () => {
  Alpine.data('studioPromptCard', (side = 'cur', model = '', state = '') => ({
    side,
    state,
    diff: false,
    // Editing is explicit (matches the prompt screen). The editable fields are
    // the body and the model; `baseline`/`modelBaseline` hold their last-saved
    // values, `editorBody`/`model` hold the live values, and `hasChanges`
    // (a getter) flips true once either diverges — driving the Save button +
    // footer. `saving` guards the PUT. Only a draft on the `cur` side is
    // editable (`canEdit`).
    baseline: '',
    editorBody: '',
    model,
    modelBaseline: model,
    modelOpen: false,
    saving: false,

    get canEdit() { return this.side === 'cur' && this.state === 'draft'; },
    get hasChanges() {
      if (!this.canEdit) return false;
      return this.editorBody !== this.baseline || this.model !== this.modelBaseline;
    },

    // Seed the store's run model from this version on mount (page load AND
    // every HTMX version swap, since the swapped card re-inits) so "Run" uses
    // the version actually shown. cmp cards never own the run model.
    init() {
      if (this.side === 'cur') {
        const p = this._page();
        if (p) p.activeModel = this.model;
      }
    },

    // Pick a model in the editor. Flips hasChanges (Save appears) via the
    // getter and keeps the store's run model in sync with the selection.
    pickModel(m) {
      this.modelOpen = false;
      if (!this.canEdit) return;
      this.model = m;
      const p = this._page();
      if (p) p.activeModel = m;
    },
    // _anno_panels.html (the shared output renderer) reads `tab`, `seek`,
    // `historyLoaded`, `historyHtml`, `loadHistory` from its enclosing
    // Alpine scope. Clip-detail provides these via `player()` + a tab
    // mix-in. Studio doesn't have a per-run history view in v1, so the
    // History tab is suppressed (see _anno_panels.html change) and
    // loadHistory is a noop.
    tab: 'markers',
    historyLoaded: true,
    historyHtml: '',
    loadHistory() {},

    // Alpine's `$root` refers to the root of the CURRENT component, not
    // the topmost ancestor. Since this card is its own x-data, `$root.X`
    // resolves to the card itself (where X is undefined), not to the
    // shared page state. So we proxy page state through the studio store
    // via getters/methods.
    _page() {
      return Alpine.store('studio');
    },
    get mode()             { return this._page()?.mode || 'prompt'; },
    set mode(v)            { const p = this._page(); if (p) p.mode = v; },
    get compareVersionId() { return this._page()?.compareVersionId; },
    get layout()           { return this._page()?.layout; },
    get activeVersionNum() { return this._page()?.activeVersionNum; },
    get pendingRunSwap()   { return this._page()?.pendingRunSwap; },
    openCompare()          { return this._page()?.openCompare(); },
    closeCompare()         { return this._page()?.closeCompare(); },

    async save() {
      if (this.side !== 'cur') return;  // never save from the cmp card.
      if (this.saving || !this.hasChanges) return;
      const page = this._page();
      const versionId = page?.activeVersionId;
      const promptId = page?.promptId;
      if (!versionId || !promptId) return;
      // `editorBody` is the single live body source (seeded on x-init, kept in
      // sync by the textarea's @input), so we don't re-read the DOM ref here.
      const body = this.editorBody;
      this.saving = true;
      try {
        // The Studio prompt pane edits the body + model; round-trip the rest
        // of the version (target_map / output_schema) unchanged.
        const v = await fetch(`/api/prompts/${promptId}/versions/${versionId}`).then(r => r.json());
        const res = await fetch(`/api/prompts/${promptId}/versions/${versionId}`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            body, target_map: v.target_map,
            output_schema: v.output_schema, model: this.model,
          }),
        });
        if (!res.ok) {
          Alpine.store('toast').push(
            `Save failed (HTTP ${res.status}).`,
            { level: 'error' },
          );
          return;
        }
        // Success — re-baseline so `hasChanges` flips false (hiding the Save
        // button) and the footer returns to "saved". editorBody is already the
        // saved value (it IS `body`), so only the baselines move.
        this.baseline = body;
        this.modelBaseline = this.model;
        // Nudge the compare diff to recompute against the just-saved body.
        page.savedTick = (page.savedTick || 0) + 1;
        Alpine.store('toast').push('Changes saved.', { level: 'success' });
      } catch (err) {
        console.error('studio save failed', err);
        Alpine.store('toast').push(
          `Save failed: ${err.message || String(err)}`,
          { level: 'error' },
        );
      } finally {
        this.saving = false;
      }
    },

    seek(secs) {
      // _anno_panels.html marker articles call seek(secs). Proxy through
      // the page root to the player's Alpine instance.
      this._page()?.seekFocusedClip(secs);
    },

    async loadOutput() {
      const page = this._page();
      // When the Diff toggle is on, the full-width compare table owns the
      // output and the per-card panes are hidden — skip the wasted /studio/_run
      // fetch for those hidden panes.
      if (page?.compareDiff) return;
      const versionId = this.side === 'cur'
        ? page?.activeVersionId
        : page?.compareVersionId;
      const clipId = page?.focusedClipId;
      if (!versionId) return;
      const slot = this.$refs.runSlot;
      if (!slot) return;
      if (!clipId) {
        slot.innerHTML = '<div class="muted">Click a clip in a set to focus it.</div>';
        return;
      }
      try {
        const html = await fetch(
          `/studio/_run?prompt_version_id=${versionId}&clip_id=${clipId}`,
        ).then(r => r.text());
        slot.innerHTML = html;
        // Alpine doesn't auto-init innerHTML-injected subtrees that have no
        // x-data of their own — without this the marker @click="seek(...)"
        // and the Markers/Fields tab switches are dead. The studio output is
        // read-only (review_mode=False), so no player-only directives throw.
        // (htmxAlpine.reinit also runs an idempotent HTMX re-scan — harmless
        // on this read-only output.)
        window.htmxAlpine.reinit(slot);
      } catch (err) {
        console.error('loadOutput failed', err);
        Alpine.store('toast').push(
          `Load failed: ${err.message || String(err)}`,
          { level: 'error' },
        );
      }
    },
  }));
});
