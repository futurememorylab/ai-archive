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

document.addEventListener('alpine:init', () => {
  Alpine.data('studioPage', (initial) => ({
    promptId: initial.promptId,
    activeVersionId: initial.activeVersionId,
    activeVersionNum: initial.activeVersionNum,
    activeModel: initial.activeModel,
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
      // Show the player region and load the player partial for this clip.
      const body = document.querySelector('.studio-body');
      if (body) body.classList.remove('no-player');
      const slot = document.querySelector('[data-studio-player-slot]');
      if (slot) {
        fetch(`/studio/_player?clip_id=${clipId}`)
          .then(r => r.text())
          .then(html => { slot.innerHTML = html; });
      }
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

  Alpine.data('studioPromptCard', () => ({
    mode: 'prompt',
    dirty: false,

    async save() {
      this.dirty = true;
      const versionId = this.$root.activeVersionId;
      const promptId = this.$root.promptId;
      if (!versionId || !promptId) {
        this.dirty = false;
        return;
      }
      const body = this.$refs.editor ? this.$refs.editor.value : null;
      if (body == null) {
        this.dirty = false;
        return;
      }
      // The prompts PUT endpoint requires the full version body. Fetch the
      // existing version to round-trip target_map / output_schema / model.
      try {
        const v = await fetch(`/api/prompts/${promptId}/versions/${versionId}`).then(r => r.json());
        const res = await fetch(`/api/prompts/${promptId}/versions/${versionId}`, {
          method: 'PUT',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            body,
            target_map: v.target_map,
            output_schema: v.output_schema,
            model: v.model,
          }),
        });
        this.dirty = !res.ok;
      } catch (err) {
        console.error('studio save failed', err);
        this.dirty = false;
      }
    },

    async loadOutput() {
      const versionId = this.$root.activeVersionId;
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
