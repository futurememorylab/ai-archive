/* Studio page — thin Alpine delegator over `Alpine.store('studio')`.

   The shared page state (focused clip, run-button machine, layout
   prefs, compare/version state) lives in the `studio` store, registered
   in studioStore.js. The page template still puts
   `x-data="studioPage(...)"` on .studio-page so existing template
   bindings keep resolving — but studioPage is now a THIN delegator that
   forwards every field/method to `$store.studio`. Cross-component
   readers (the window.studio shim, htmx:afterSwap, modelPicker,
   studioPromptCard, cmpDiff) read `Alpine.store('studio')` directly,
   replacing the old reach-ins into Alpine's private data-stack internal.

   See studioStore.js for the run lifecycle and the actual logic.
*/

// window.studio — vanilla-JS shim for HTMX-injected content (clip cards,
// archive picker results). Alpine v3's MutationObserver does NOT re-scan
// directives on subtrees swapped in via HTMX `hx-swap="innerHTML"` when
// the inserted nodes have no `x-data` of their own (they would rely on
// inherited scope, which initTree doesn't wire up after the fact).
// Rather than fight that, dynamic content uses vanilla `onclick` and
// calls these shim methods, which proxy to the studio store.
window.studio = {
  _root() {
    return window.Alpine?.store('studio') ?? null;
  },
  focusClip(clipId) {
    this._root()?.focusClip(clipId);
    // Selected-state styling is handled here (Alpine `:class` won't bind
    // on HTMX-injected nodes — see comment above).
    document.querySelectorAll('.studio-clip-card.selected')
      .forEach(el => el.classList.remove('selected'));
    document.querySelectorAll(`.studio-clip-card[data-clip-id="${clipId}"]`)
      .forEach(el => el.classList.add('selected'));
  },
  removeClip(folderId, clipId, btnEl) {
    if (!confirm('Remove from folder?')) return;
    fetch(`/api/studio/folders/${folderId}/clips/${clipId}`, {method: 'DELETE'})
      .then(() => btnEl.closest('.studio-clip-card').remove());
  },
};

// Prompt-picker links live inside a nested Alpine x-data, so $root there
// shadows studioPage and clip_id can't be appended via :href binding.
// Intercept the click and append clip_id from the live studioPage state
// before navigation.
document.body.addEventListener('click', (evt) => {
  const a = evt.target.closest('a[data-prompt-switch]');
  if (!a) return;
  const fid = window.Alpine?.store('studio')?.focusedClipId;
  // Always normalize: if a clip is focused, write it into the href; if
  // not, strip any previously-baked clip_id. Without the strip branch a
  // prior click would leave clip_id=N on the anchor forever, so a
  // later modifier-click after focus was cleared would still navigate
  // with the stale clip id.
  const u = new URL(a.href, location.origin);
  if (fid) u.searchParams.set('clip_id', String(fid));
  else u.searchParams.delete('clip_id');
  a.href = u.toString();
});

document.body.addEventListener('htmx:afterSwap', (evt) => {
  const page = window.Alpine?.store('studio');
  if (!page) return;

  // When a folder's clip cards swap in (hx-trigger="intersect once" on
  // .studio-folder-kids), reconcile `.selected` against the live
  // focusedClipId. The server bakes a `clip_id=…` into each folder's
  // hx-get URL at page-load time, so cards arrive pre-selected based on
  // the URL at that moment — but the user may have focused a different
  // clip via JS since, and the hx-get URL doesn't update. Clear the
  // server's guess, then apply the current focus.
  if (evt.target.classList?.contains('studio-folder-kids')) {
    evt.target.querySelectorAll('.studio-clip-card.selected')
      .forEach(el => el.classList.remove('selected'));
    if (page.focusedClipId) {
      evt.target.querySelectorAll(`.studio-clip-card[data-clip-id="${page.focusedClipId}"]`)
        .forEach(el => el.classList.add('selected'));
    }
  }

  const card = evt.target.closest('.studio-prompt-card');
  if (!card) return;
  // Alpine v3's MutationObserver doesn't reliably re-init x-data subtrees
  // swapped by HTMX hx-swap="outerHTML" — after a few cycles the card
  // comes back un-initialized, then every directive in it
  // (picker, close, diff-toggle, tab clicks) becomes a dead click.
  // Initialize the swapped subtree explicitly to keep it alive.
  window.Alpine?.initTree(card);
  const side = card.getAttribute('data-side');
  const vId  = parseInt(card.getAttribute('data-version-id'), 10);
  const vNum = parseInt(card.getAttribute('data-version-num'), 10);
  if (Number.isNaN(vId)) return;
  if (side === 'cur') {
    page.activeVersionId = vId;
    page.activeVersionNum = vNum;
    page.pendingRunSwap++;
  } else if (side === 'cmp') {
    page.compareVersionId = vId;
    page.compareVersionNum = vNum;
    page.pendingRunSwap++;
  }
  page._writeUrl();
  if (page.focusedClipId) page.refreshPlayer();
});

document.addEventListener('alpine:init', () => {
  // studioPage — THIN delegator. The shared state + logic live in
  // Alpine.store('studio') (studioStore.js). This component stays the
  // x-data on .studio-page so every existing template binding still
  // resolves, but each field/method just forwards to the store. The
  // boilerplate is the deliberate safe trade-off: it keeps template
  // churn at zero while removing the private-internal reach-ins. Same
  // delegation pattern as the CoreCtx/LiveCtx property proxies elsewhere
  // in this codebase.
  Alpine.data('studioPage', (initial) => {
    const store = () => Alpine.store('studio');
    return {
      init() { store().hydrate(initial); },

      // ── State delegators (getters/setters → store) ────────────────
      get promptId()             { return store().promptId; },
      set promptId(v)            { store().promptId = v; },
      get activeVersionId()      { return store().activeVersionId; },
      set activeVersionId(v)     { store().activeVersionId = v; },
      get activeVersionNum()     { return store().activeVersionNum; },
      set activeVersionNum(v)    { store().activeVersionNum = v; },
      get activeModel()          { return store().activeModel; },
      set activeModel(v)         { store().activeModel = v; },
      get compareVersionId()     { return store().compareVersionId; },
      set compareVersionId(v)    { store().compareVersionId = v; },
      get compareVersionNum()    { return store().compareVersionNum; },
      set compareVersionNum(v)   { store().compareVersionNum = v; },
      get mode()                 { return store().mode; },
      set mode(v)                { store().mode = v; },
      get focusedClipId()        { return store().focusedClipId; },
      set focusedClipId(v)       { store().focusedClipId = v; },
      get showList()             { return store().showList; },
      set showList(v)            { store().showList = v; },
      get showPlayer()           { return store().showPlayer; },
      set showPlayer(v)          { store().showPlayer = v; },
      get layout()               { return store().layout; },
      set layout(v)              { store().layout = v; },
      get running()              { return store().running; },
      set running(v)             { store().running = v; },
      get cancelling()           { return store().cancelling; },
      set cancelling(v)          { store().cancelling = v; },
      get runId()                { return store().runId; },
      set runId(v)               { store().runId = v; },
      get runJobId()             { return store().runJobId; },
      set runJobId(v)            { store().runJobId = v; },
      get runStartMs()           { return store().runStartMs; },
      set runStartMs(v)          { store().runStartMs = v; },
      get runningElapsedLabel()  { return store().runningElapsedLabel; },
      set runningElapsedLabel(v) { store().runningElapsedLabel = v; },
      get doneFlashUntilMs()     { return store().doneFlashUntilMs; },
      set doneFlashUntilMs(v)    { store().doneFlashUntilMs = v; },
      get cancelledFlashUntilMs(){ return store().cancelledFlashUntilMs; },
      set cancelledFlashUntilMs(v){ store().cancelledFlashUntilMs = v; },
      get pendingRunSwap()       { return store().pendingRunSwap; },
      set pendingRunSwap(v)      { store().pendingRunSwap = v; },

      // ── Method delegators ─────────────────────────────────────────
      runButtonLabel()       { return store().runButtonLabel(); },
      runOrCancel()          { return store().runOrCancel(); },
      cancel()               { return store().cancel(); },
      focusClip(clipId)      { return store().focusClip(clipId); },
      toggleList()           { return store().toggleList(); },
      togglePlayer()         { return store().togglePlayer(); },
      setLayout(v)           { return store().setLayout(v); },
      refreshPlayer()        { return store().refreshPlayer(); },
      seekFocusedClip(secs)  { return store().seekFocusedClip(secs); },
      runOnFocusedClip()     { return store().runOnFocusedClip(); },
      openCompare()          { return store().openCompare(); },
      closeCompare()         { return store().closeCompare(); },
      _writeUrl()            { return store()._writeUrl(); },
    };
  });

  // Cross-component proxy to the studio store's activeModel — necessary
  // because Alpine `$root` only walks to the nearest enclosing `x-data`,
  // and nesting `x-data="{ open: false }"` on the picker hides the page
  // scope. The shared state lives in Alpine.store('studio').
  Alpine.data('modelPicker', () => ({
    open: false,
    _page() {
      return Alpine.store('studio');
    },
    get model() {
      return this._page().activeModel;
    },
    set model(v) {
      this._page().activeModel = v;
    },
  }));

  Alpine.data('archivePicker', (folderId) => ({
    folderId,
    picked: new Set(),

    toggle(id) {
      if (this.picked.has(id)) this.picked.delete(id);
      else this.picked.add(id);
    },

    async addSelected() {
      const ids = Array.from(this.picked);
      if (!ids.length) return;
      const res = await fetch(`/api/studio/folders/${this.folderId}/clips`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({clip_ids: ids}),
      });
      if (res.ok) {
        const html = await res.text();
        const kidsEl = document.querySelector(
          `.studio-folder[data-folder-id="${this.folderId}"] .studio-folder-kids`
        );
        if (kidsEl) {
          kidsEl.innerHTML = html;
          window.Alpine?.initTree(kidsEl);
          window.htmx?.process(kidsEl);
        }
        this.close();  // close the archive picker modal
        Alpine.store('toast').push(
          `Added ${ids.length} clip${ids.length === 1 ? '' : 's'} to folder.`,
          { level: 'success' },
        );
      } else {
        Alpine.store('toast').push(
          `Add clips failed (HTTP ${res.status}).`,
          { level: 'error' },
        );
      }
    },

    close() {
      const root = document.getElementById('modal-root');
      if (root) root.innerHTML = '';
    },
  }));

  Alpine.data('studioFolders', (initialExpandedId = null) => ({
    expandedId: initialExpandedId,
    newFolderOpen: false,
    newFolderName: '',

    toggle(id) {
      this.expandedId = this.expandedId === id ? null : id;
    },

    async createFolder() {
      const name = this.newFolderName.trim();
      if (!name) return;
      const res = await fetch('/api/studio/folders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({name}),
      });
      if (res.ok) {
        const html = await res.text();
        const folderList = document.querySelector('.studio-folders-list');
        if (folderList) {
          folderList.insertAdjacentHTML('beforeend', html);
          const newCard = folderList.lastElementChild;
          window.Alpine?.initTree(newCard);
          window.htmx?.process(newCard);
        }
        this.newFolderName = '';
        this.newFolderOpen = false;
        Alpine.store('toast').push(`Created folder "${name}".`, { level: 'success' });
      } else if (res.status === 409) {
        Alpine.store('toast').push(
          `Folder "${name}" already exists.`,
          { level: 'error' },
        );
      } else {
        Alpine.store('toast').push(
          `Folder create failed (HTTP ${res.status}).`,
          { level: 'error' },
        );
      }
    },
  }));

  Alpine.data('studioPromptCard', (side = 'cur') => ({
    side,
    diff: false,
    dirty: false,
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
    // via getters/methods. Same pattern as `modelPicker`.
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
      this.dirty = true;
      const page = this._page();
      const versionId = page?.activeVersionId;
      const promptId = page?.promptId;
      if (!versionId || !promptId) { this.dirty = false; return; }
      const body = this.$refs.editor ? this.$refs.editor.value : null;
      if (body == null) { this.dirty = false; return; }
      try {
        const v = await fetch(`/api/prompts/${promptId}/versions/${versionId}`).then(r => r.json());
        const res = await fetch(`/api/prompts/${promptId}/versions/${versionId}`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            body, target_map: v.target_map,
            output_schema: v.output_schema, model: v.model,
          }),
        });
        this.dirty = !res.ok;
      } catch (err) {
        console.error('studio save failed', err);
        Alpine.store('toast').push(
          `Save failed: ${err.message || err}`,
          { level: 'error' },
        );
        this.dirty = false;
      }
    },

    seek(secs) {
      // _anno_panels.html marker articles call seek(secs). Proxy through
      // the page root to the player's Alpine instance.
      this._page()?.seekFocusedClip(secs);
    },

    async loadOutput() {
      const page = this._page();
      const versionId = this.side === 'cur'
        ? page?.activeVersionId
        : page?.compareVersionId;
      const clipId = page?.focusedClipId;
      if (!versionId) return;
      const slot = this.$refs.runSlot;
      if (!slot) return;
      if (!clipId) {
        slot.innerHTML = '<div class="muted">Click a clip in a folder to focus it.</div>';
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
        window.Alpine?.initTree(slot);
      } catch (err) {
        console.error('loadOutput failed', err);
        Alpine.store('toast').push(
          `Load failed: ${err.message || err}`,
          { level: 'error' },
        );
      }
    },
  }));
});
