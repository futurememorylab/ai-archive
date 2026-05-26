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
});
