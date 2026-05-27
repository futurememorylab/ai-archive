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

document.body.addEventListener('htmx:afterSwap', (evt) => {
  const root = document.querySelector('.studio-page');
  if (!root || !root._x_dataStack) return;
  const page = root._x_dataStack[0];
  const card = evt.target.closest('.studio-prompt-card');
  if (!card) return;
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
  Alpine.data('studioPage', (initial) => ({
    promptId: initial.promptId,
    activeVersionId: initial.activeVersionId,
    activeVersionNum: initial.activeVersionNum,
    activeModel: initial.activeModel,
    compareVersionId: initial.compareVersionId,
    compareVersionNum: initial.compareVersionNum,
    mode: 'prompt',  // page-level tab state; Task 11 confirms the lift from card-level.
    focusedClipId: null,
    running: false,
    runId: null,
    runStartMs: 0,
    runningElapsedLabel: '00:00',
    pendingRunSwap: 0,

    init() {
      // Tick elapsed-time label while running.
      setInterval(() => {
        if (this.running) {
          const s = Math.floor((performance.now() - this.runStartMs) / 1000);
          const m = String(Math.floor(s / 60)).padStart(2, '0');
          const r = String(s % 60).padStart(2, '0');
          this.runningElapsedLabel = `${m}:${r}`;
        }
      }, 500);
    },

    focusClip(clipId) {
      this.focusedClipId = clipId;
      this.pendingRunSwap++;
      const body = document.querySelector('.studio-body');
      if (body) body.classList.remove('no-player');
      this.refreshPlayer();
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

    async runOnFocusedClip() {
      if (!this.activeVersionId || !this.focusedClipId || this.running) return;
      this.running = true;
      this.runStartMs = performance.now();
      this.runningElapsedLabel = '00:00';
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
        const {run_id} = await res.json();
        this.runId = run_id;
        await this._poll(run_id);
      } catch (err) {
        console.error('studio run failed', err);
      } finally {
        this.running = false;
        this.pendingRunSwap++;
      }
    },

    async _poll(runId) {
      while (true) {
        await new Promise(r => setTimeout(r, 1000));
        const res = await fetch(`/api/studio/runs/${runId}`);
        if (!res.ok) return;
        const run = await res.json();
        if (run.status === 'ok' || run.status === 'error') return;
      }
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
      window.history.replaceState({}, '', `${window.location.pathname}?${p.toString()}`);
    },
  }));

  Alpine.data('studioHeader', () => ({}));

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

  Alpine.data('studioFolders', () => ({
    expandedId: null,
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
    // mode is on $root now (added in Task 6). The template references
    // $root.mode directly, so this factory doesn't need a local `mode`.

    async save() {
      if (this.side !== 'cur') return;  // never save from the cmp card.
      this.dirty = true;
      const versionId = this.$root.activeVersionId;
      const promptId = this.$root.promptId;
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

    async loadOutput() {
      const versionId = this.side === 'cur'
        ? this.$root.activeVersionId
        : this.$root.compareVersionId;
      const clipId = this.$root.focusedClipId;
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
      } catch (err) {
        console.error('loadOutput failed', err);
      }
    },
  }));
});
