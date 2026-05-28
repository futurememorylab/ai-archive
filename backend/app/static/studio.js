/* Studio page — Alpine root state.

   The page template puts `x-data="studioPage(...)"` on .studio-page.
   Child components read parent scope via $root so the header, folder
   list, and prompt card all share the same focused-clip / running flag.

   Run lifecycle:
     1. POST /api/studio/runs {prompt_version_id, clip_id, model}
        → {run_id, job_id}
     2. Poll GET /api/studio/runs/{run_id} every 1s until status != pending|running
     3. On completion (ok or error), trigger a UI refresh by incrementing
        pendingRunSwap; the prompt card watches that and re-fetches its
        output partial.
*/

// window.studio — vanilla-JS shim for HTMX-injected content (clip cards,
// archive picker results). Alpine v3's MutationObserver does NOT re-scan
// directives on subtrees swapped in via HTMX `hx-swap="innerHTML"` when
// the inserted nodes have no `x-data` of their own (they would rely on
// inherited scope, which initTree doesn't wire up after the fact).
// Rather than fight that, dynamic content uses vanilla `onclick` and
// calls these shim methods, which proxy to the Alpine root.
window.studio = {
  _root() {
    return document.querySelector('.studio-page')?._x_dataStack?.[0] ?? null;
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
  const root = document.querySelector('.studio-page');
  const fid = root?._x_dataStack?.[0]?.focusedClipId;
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
  const root = document.querySelector('.studio-page');
  if (!root || !root._x_dataStack) return;
  const page = root._x_dataStack[0];

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
  // comes back with no _x_dataStack, then every directive in it
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
  Alpine.data('studioPage', (initial) => {
    const prefs = window.__studioPrefs || { showList: true, showPlayer: true, layout: 'under' };
    return {
    promptId: initial.promptId,
    activeVersionId: initial.activeVersionId,
    activeVersionNum: initial.activeVersionNum,
    activeModel: initial.activeModel,
    compareVersionId: initial.compareVersionId,
    compareVersionNum: initial.compareVersionNum,
    mode: 'prompt',  // page-level tab state; Task 11 confirms the lift from card-level.
    focusedClipId: initial.focusedClipId ?? null,
    showList: prefs.showList,
    showPlayer: prefs.showPlayer,
    layout: prefs.layout,            // 'under' | 'right'
    // ── Run-button state machine ──────────────────────────────────────
    running: false,
    cancelling: false,
    runId: null,
    runJobId: null,
    runStartMs: 0,
    runningElapsedLabel: '0:00',
    doneFlashUntilMs: 0,
    _nowMs: 0,   // bumped by the 1Hz ticker so runButtonLabel re-evaluates
    pendingRunSwap: 0,

    init() {
      // Restore a server-seeded focused clip (e.g. from ?clip_id=…). The
      // server already left `no-player` off the body in that case, so the
      // slot is visible — we just need to load the player partial.
      if (this.focusedClipId) this.refreshPlayer();
      // 1Hz ticker drives both the elapsed label and the done-flash expiry.
      setInterval(() => {
        const now = performance.now();
        this._nowMs = now;  // touch reactive state so getters re-run
        if (this.running) {
          const s = Math.floor((now - this.runStartMs) / 1000);
          this.runningElapsedLabel = window.fmtTimecode(s);
        }
        if (this.doneFlashUntilMs && now >= this.doneFlashUntilMs) {
          this.doneFlashUntilMs = 0;
        }
      }, 1000);
    },

    runButtonLabel() {
      // Mirror of tests/_helpers/studio_state.py::run_button_label
      const now = this._nowMs || performance.now();
      if (this.doneFlashUntilMs && now < this.doneFlashUntilMs) return '✓ Done';
      if (this.cancelling) return '⟳ Cancelling…';
      if (this.running) return `⟳ Running… ${this.runningElapsedLabel}`;
      const v = (this.activeVersionNum !== null && this.activeVersionNum !== undefined)
        ? this.activeVersionNum : '?';
      return `▶ Run on this clip · v${v}`;
    },

    async runOrCancel() {
      if (this.cancelling || this.doneFlashUntilMs) return;
      if (this.running) return this.cancel();
      return this.runOnFocusedClip();
    },

    async cancel() {
      if (!this.runJobId || this.cancelling) return;
      this.cancelling = true;
      try {
        await fetch(`/api/jobs/${this.runJobId}/cancel`, { method: 'POST' });
      } catch (err) {
        console.error('cancel failed', err);
      } finally {
        // Stop the poll loop; runOnFocusedClip()'s finally tidies up.
        this.running = false;
        this.cancelling = false;
        this.pendingRunSwap++;
      }
    },

    focusClip(clipId) {
      this.focusedClipId = clipId;
      this.pendingRunSwap++;
      this._writeUrl();
      this.refreshPlayer();
    },

    toggleList() {
      this.showList = !this.showList;
      this._saveLayoutPrefs();
    },

    togglePlayer() {
      this.showPlayer = !this.showPlayer;
      this._saveLayoutPrefs();
    },

    setLayout(v) {
      if (v !== 'under' && v !== 'right') return;
      this.layout = v;
      // Compare needs the wide stacked layout; close it when going right.
      if (v === 'right' && this.compareVersionId) this.closeCompare();
      this._saveLayoutPrefs();
    },

    _saveLayoutPrefs() {
      try {
        localStorage.setItem('studio.layoutPrefs', JSON.stringify({
          showList: this.showList,
          showPlayer: this.showPlayer,
          layout: this.layout,
        }));
      } catch (err) {
        console.error('studio layout prefs save failed', err);
      }
    },

    refreshPlayer() {
      const slot = document.querySelector('[data-studio-player-slot]');
      if (!slot || !this.focusedClipId) return;
      const params = new URLSearchParams();
      params.set('clip_id', this.focusedClipId);
      if (this.activeVersionId)  params.set('version_id', this.activeVersionId);
      if (this.compareVersionId) params.set('compare_id', this.compareVersionId);
      fetch(`/studio/_player?${params.toString()}`)
        .then(r => r.text())
        .then(html => { slot.innerHTML = html; });
    },

    seekFocusedClip(secs) {
      const playerEl = document.querySelector('.studio-player');
      if (!playerEl || !playerEl._x_dataStack) return;
      const player = playerEl._x_dataStack[0];
      if (typeof player.seek === 'function') player.seek(secs);
    },

    async runOnFocusedClip() {
      if (!this.activeVersionId || !this.focusedClipId || this.running) return;
      this.running = true;
      this.runStartMs = performance.now();
      this.runningElapsedLabel = '0:00';
      let finalStatus = null;
      try {
        const res = await fetch('/api/studio/runs', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            prompt_version_id: this.activeVersionId,
            clip_id: this.focusedClipId,
            model: this.activeModel || null,
          }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const {run_id, job_id} = await res.json();
        this.runId = run_id;
        this.runJobId = job_id ?? null;
        finalStatus = await this._poll(run_id);
      } catch (err) {
        console.error('studio run failed', err);
      } finally {
        this.running = false;
        this.runJobId = null;
        this.pendingRunSwap++;
        // ✓ Done flash on success only — no flash for error / cancelled.
        if (finalStatus === 'ok') {
          this.doneFlashUntilMs = performance.now() + 1200;
        }
      }
    },

    async _poll(runId) {
      while (this.running) {
        await new Promise(r => setTimeout(r, 1000));
        if (!this.running) return null;  // cancel() flipped it
        const res = await fetch(`/api/studio/runs/${runId}`);
        if (!res.ok) return null;
        const run = await res.json();
        if (run.status === 'ok' || run.status === 'error' || run.status === 'cancelled') {
          return run.status;
        }
      }
      return null;
    },

    async openCompare() {
      const versions = window.__studioVersions || [];
      const cur = this.activeVersionId;
      const drafts = versions.filter(v => v.id !== cur && v.state === 'draft');
      const prods  = versions.filter(v => v.id !== cur && v.state === 'production');
      const others = versions.filter(v => v.id !== cur);
      const pick = (drafts[0] || prods[0] || others[0]);
      if (!pick) return;
      this.compareVersionId = pick.id;
      this.compareVersionNum = pick.version_num;
      this._writeUrl();
      const slot = document.querySelector('[data-cmp-slot]');
      if (!slot) return;
      slot.style.display = '';
      const params = new URLSearchParams();
      params.set('side', 'cmp');
      params.set('prompt_version_id', pick.id);
      if (this.focusedClipId) params.set('clip_id', this.focusedClipId);
      const html = await fetch(`/studio/_prompt_card?${params.toString()}`).then(r => r.text());
      slot.innerHTML = html;
      window.Alpine?.initTree(slot);
      // HTMX doesn't auto-scan DOM we injected ourselves — without this,
      // the cmp card's version-picker hx-* attributes never get wired
      // and picking a different cmp version is a dead click.
      window.htmx?.process(slot);
      this.refreshPlayer();
    },

    closeCompare() {
      this.compareVersionId = null;
      this.compareVersionNum = null;
      this._writeUrl();
      const slot = document.querySelector('[data-cmp-slot]');
      if (slot) { slot.innerHTML = ''; slot.style.display = 'none'; }
      this.refreshPlayer();
    },

    _writeUrl() {
      const p = new URLSearchParams(window.location.search);
      if (this.promptId)         p.set('prompt_id', this.promptId);          else p.delete('prompt_id');
      if (this.activeVersionId)  p.set('version_id', this.activeVersionId);  else p.delete('version_id');
      if (this.compareVersionId) p.set('compare_version_id', this.compareVersionId); else p.delete('compare_version_id');
      if (this.focusedClipId)    p.set('clip_id', this.focusedClipId);       else p.delete('clip_id');
      window.history.replaceState({}, '', `${window.location.pathname}?${p.toString()}`);
    },
  };
  });

  // Cross-component proxy to studioPage.activeModel — necessary because
  // Alpine `$root` only walks to the nearest enclosing `x-data`, and nesting
  // `x-data="{ open: false }"` on the picker hides the page scope.
  Alpine.data('modelPicker', () => ({
    open: false,
    _page() {
      return document.querySelector('.studio-page')._x_dataStack[0];
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
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clip_ids: ids}),
      });
      if (res.ok) {
        location.reload();
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
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name}),
      });
      if (res.ok) {
        location.reload();
      } else if (res.status === 409) {
        alert(`Folder "${name}" already exists.`);
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
    // resolves to the card itself (where X is undefined), not to
    // studioPage. So we proxy page state via getters/methods. Same
    // pattern as `modelPicker`.
    _page() {
      return document.querySelector('.studio-page')?._x_dataStack?.[0];
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
      }
    },
  }));
});
