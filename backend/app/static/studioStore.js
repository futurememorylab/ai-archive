/* Studio shared page state — `Alpine.store('studio')`.

   Previously this state lived on the `studioPage` Alpine component
   (x-data on .studio-page) and cross-component readers reached into it
   via Alpine's private per-element data-stack internal. That internal is
   an undocumented implementation detail that breaks across versions.

   The shared state now lives in this store. `studioPage` stays the
   `x-data` on .studio-page (so existing template bindings keep
   resolving) but is a THIN delegator (see studio.js) that forwards every
   field/method to `$store.studio`. Cross-component readers (the
   window.studio shim, htmx:afterSwap, studioPromptCard,
   cmpDiff) read `Alpine.store('studio')` directly.

   The Run button and the single-clip run lifecycle live in
   studioRunButton.js and are mixed into this store; the bulk run over
   the checked clips stays here with the selection it runs.
*/
// Module-scope (non-reactive) key for refreshEstimate() — prevents the
// x-effect from re-triggering when the store writes estimateLabel.
let _studioEstimateKey = '';

// Max concurrent studio runs during a bulk "Run on N clips" — protects the
// single CatDV seat and Gemini quota. See spec §5.
const BULK_RUN_CONCURRENCY = 2;

document.addEventListener('alpine:init', () => {
  Alpine.store('studio', {
    ...window.studioRunButton(),
    // ── Shared page state (seeded by hydrate()) ──────────────────────
    promptId: null,
    activeVersionId: null,
    activeVersionNum: null,
    activeModel: '',
    compareVersionId: null,
    compareVersionNum: null,
    mode: 'prompt',  // page-level tab state
    focusedClipId: null,
    // ── Multi-select (navigator checkboxes → bulk run) ───────────────
    // Set of clip ids selected in the CURRENT source tab. Plain array for
    // Alpine reactivity friendliness; membership ops go through helpers.
    selectedClipIds: [],
    bulkRunning: false,
    bulkDone: 0,
    bulkTotal: 0,
    showList: true,
    showPlayer: true,
    layout: 'under',                 // 'under' | 'right'
    // ── Split-pane sizes (persisted; see studioResize.js) ────────────
    // null = use the CSS default (320px / 1fr / 50%). Stored as the raw
    // CSS value string (e.g. '360px', '42%').
    playerH: null,                   // player height in 'under' layout
    playerW: null,                   // player width in 'right' layout
    cmpCur: null,                    // cur card width as % of the compare row
    // Bumped by studioPromptCard.save() so the compare diff (which watches
    // this) recomputes against the just-saved version.
    savedTick: 0,
    // Scene-compare linkage: the scene-key currently hovered/selected in the
    // compare table or on the timeline. The vanilla bridge (studioSceneLink.js)
    // keeps this in sync and toggles `.is-linked` on matching DOM nodes.
    selectedSceneKey: null,
    // Shared "Diff vs vN" toggle for the compare cards (both Prompt and
    // Output tabs). Default off = the two versions shown side by side
    // (prompt bodies / output panels); on = the word diff (Prompt) or the
    // aligned scene table (Output). Lives in the store because the full-width
    // output table sits outside the cmp card's Alpine scope. Reset when a
    // compare is opened/closed so a fresh compare starts side-by-side.
    compareDiff: false,
    _hydrated: false,
    // Pre-run cost estimate (advisory — empty string = nothing to show).
    estimateLabel: '',

    async refreshEstimate() {
      // _studioEstimateKey is module-scope (non-reactive) — writing it here
      // does not re-trigger the x-effect that calls this, preventing a loop.
      const vid = this.activeVersionId, cid = this.focusedClipId;
      const key = `${vid}:${cid}`;
      if (key === _studioEstimateKey) return;
      _studioEstimateKey = key;
      if (!vid || !cid) { this.estimateLabel = ''; return; }
      try {
        const r = await fetch('/api/jobs/estimate', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ prompt_version_id: vid, clip_ids: [cid] }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const e = await r.json();
        if (key !== _studioEstimateKey) return;  // selection changed mid-flight
        if (e.pricing_missing) {
          this.estimateLabel = `cost unknown — no rate card (${e.confidence})`
            + (e.media_resolution ? ` · ${e.media_resolution} res` : "");
        } else {
          this.estimateLabel =
            `~${fmtUsd(e.cost_usd_p50)}–${fmtUsd(e.cost_usd_p90)} (${e.confidence})`
            + (e.media_resolution ? ` · ${e.media_resolution} res` : "");
        }
      } catch (err) {
        console.error('estimate failed', err);  // advisory only
        if (key !== _studioEstimateKey) return;  // selection changed mid-flight
        this.estimateLabel = '';
      }
    },

    // Seed initial.* fields from the page component's init(). Idempotent:
    // the studio page mounts a single studioPage, but guard anyway so a
    // re-init (HTMX page swap) doesn't stack a second ticker.
    hydrate(initial) {
      const prefs = window.__studioPrefs || { showList: true, showPlayer: true, layout: 'under' };
      this.promptId = initial.promptId;
      this.activeVersionId = initial.activeVersionId;
      this.activeVersionNum = initial.activeVersionNum;
      this.activeModel = initial.activeModel;
      this.compareVersionId = initial.compareVersionId;
      this.compareVersionNum = initial.compareVersionNum;
      this.focusedClipId = initial.focusedClipId ?? null;
      this.showList = prefs.showList;
      this.showPlayer = prefs.showPlayer;
      this.layout = prefs.layout;
      this.playerH = prefs.playerH ?? null;
      this.playerW = prefs.playerW ?? null;
      this.cmpCur = prefs.cmpCur ?? null;

      if (this._hydrated) return;
      this._hydrated = true;

      // Restore a server-seeded focused clip (e.g. from ?clip_id=…). The
      // server already left `no-player` off the body in that case, so the
      // slot is visible — we just need to load the player partial.
      if (this.focusedClipId) this.refreshPlayer();
      // 1Hz ticker drives both the elapsed label and the done-flash expiry.
      setInterval(() => this._tickRunButton(performance.now()), 1000);
    },

    focusClip(clipId) {
      this.focusedClipId = clipId;
      this.pendingRunSwap++;
      this._writeUrl();
      this.refreshPlayer();
    },

    // ── Selection ────────────────────────────────────────────────────
    isClipSelected(clipId) { return this.selectedClipIds.includes(clipId); },

    toggleClip(clipId, checked) {
      const on = checked ?? !this.isClipSelected(clipId);
      if (on && !this.isClipSelected(clipId)) {
        this.selectedClipIds = [...this.selectedClipIds, clipId];
      } else if (!on) {
        this.selectedClipIds = this.selectedClipIds.filter(id => id !== clipId);
      }
    },

    _clipIdsInSet(setId) {
      const setEl = document.querySelector(`.studio-set[data-set-id="${setId}"]`);
      if (!setEl) return [];
      const kids = setEl.querySelector('.studio-set-kids');
      // Once a set has been expanded its clip cards are in the DOM (kept,
      // just hidden, when re-collapsed) and stay accurate across add/remove,
      // so they're the source of truth. Until first expand the kids hold only
      // the loading sentinel — fall back to the server-rendered membership on
      // data-clip-ids so selecting a collapsed set still selects every clip.
      const loaded = kids && !kids.querySelector('.set-kids-loading');
      if (loaded) {
        return [...kids.querySelectorAll('.studio-clip-card[data-clip-id]')]
          .map(el => Number(el.dataset.clipId));
      }
      const raw = setEl.dataset.clipIds || '';
      return raw ? raw.split(',').map(Number) : [];
    },

    setFullySelected(setId) {
      const ids = this._clipIdsInSet(setId);
      return ids.length > 0 && ids.every(id => this.isClipSelected(id));
    },

    toggleSet(setId) {
      const ids = this._clipIdsInSet(setId);
      const allOn = ids.length > 0 && ids.every(id => this.isClipSelected(id));
      ids.forEach(id => this.toggleClip(id, !allOn));
      // Reflect into the rendered clip checkboxes (HTMX-injected, no x-model).
      document.querySelectorAll(`.studio-set[data-set-id="${setId}"] .clip-check`)
        .forEach((cb, i) => { cb.checked = !allOn; });
    },

    setBadge(setId, total) {
      const ids = this._clipIdsInSet(setId);
      const sel = ids.filter(id => this.isClipSelected(id)).length;
      return sel > 0 ? `${sel}/${total}` : String(total);
    },

    clearSelection() {
      this.selectedClipIds = [];
      document.querySelectorAll('.studio-clip-card .clip-check, .studio-set .set-check')
        .forEach(cb => { cb.checked = false; });
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
      // Keep compare open across layout switches. The `right` layout now
      // renders a three-column Player | cur | cmp arrangement (see the
      // resizable-panes spec / ADR), so switching layout must NOT close the
      // compare card — only an explicit close action may do that.
      this._saveLayoutPrefs();
    },

    _saveLayoutPrefs() {
      try {
        localStorage.setItem('studio.layoutPrefs', JSON.stringify({
          showList: this.showList,
          showPlayer: this.showPlayer,
          layout: this.layout,
          playerH: this.playerH,
          playerW: this.playerW,
          cmpCur: this.cmpCur,
        }));
      } catch (err) {
        console.error('studio layout prefs save failed', err);
      }
    },

    // Persist a divider drag (studioResize.js writes playerH/playerW/cmpCur
    // then calls this on pointerup). Same localStorage blob as the toggles.
    saveResize() {
      this._saveLayoutPrefs();
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
      // Reach the player component via a public DOM event rather than
      // its private Alpine internals. The studio player root listens for
      // `studio-seek` (see _studio_player.html) and calls seek($event.detail).
      document.querySelector('.studio-player')
        ?.dispatchEvent(new CustomEvent('studio-seek', { detail: secs }));
    },

    // One run: POST + poll to terminal. Returns the final status string
    // ('ok' | 'error' | 'cancelled' | null). Bulk only (single: studioRunButton.js).
    async _runOne(clipId) {
      const res = await fetch('/api/studio/runs', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          prompt_version_id: this.activeVersionId,
          clip_id: clipId,
          model: this.activeModel || null,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const {run_id} = await res.json();
      // Poll this run id to terminal status (independent of this.running).
      while (true) {
        await new Promise(r => setTimeout(r, 1000));
        const pr = await fetch(`/api/studio/runs/${run_id}`);
        if (!pr.ok) continue;
        const run = await pr.json();
        if (['ok', 'error', 'cancelled'].includes(run.status)) return run.status;
      }
    },

    // Bulk "Run on N clips": run the active version over every selected clip
    // with bounded concurrency. Per-clip failures toast and the loop
    // continues. The navigator re-renders run-dots on next expand.
    async runOnSelectedClips() {
      if (!this.activeVersionId || this.bulkRunning) return;
      const ids = [...this.selectedClipIds];
      if (!ids.length) return;
      this.bulkRunning = true;
      this.bulkTotal = ids.length;
      this.bulkDone = 0;
      const queue = ids.slice();
      const worker = async () => {
        while (queue.length) {
          const clipId = queue.shift();
          try {
            const status = await this._runOne(clipId);
            if (status === 'error') {
              Alpine.store('toast').push(`Run failed for clip ${clipId}.`, { level: 'error' });
            }
          } catch (err) {
            console.error('bulk run failed', clipId, err);
            Alpine.store('toast').push(
              `Run failed for clip ${clipId}: ${err.message || String(err)}`,
              { level: 'error' },
            );
          } finally {
            this.bulkDone++;
          }
        }
      };
      try {
        await Promise.all(
          Array.from({length: Math.min(BULK_RUN_CONCURRENCY, ids.length)}, worker)
        );
        Alpine.store('toast').push(
          `Ran ${this.bulkDone} clip${this.bulkDone === 1 ? '' : 's'}.`,
          { level: 'success' },
        );
        this.pendingRunSwap++;  // nudge the focused-clip output to refresh
      } finally {
        this.bulkRunning = false;
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
      this.compareDiff = false;  // open in side-by-side; Diff is opt-in
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
      // Wire HTMX only (the version-picker hx-* attrs; without it the cmp
      // version-pick would be a dead click). The cmp card has its OWN x-data,
      // so Alpine's MutationObserver inits it on its own — re-running Alpine's
      // init on it here as well (reinit) double-binds every directive, which
      // made the Diff toggle fire twice per click and look dead.
      window.htmxAlpine.wireHtmx(slot);
      this.refreshPlayer();
    },

    closeCompare() {
      this.compareVersionId = null;
      this.compareVersionNum = null;
      this.compareDiff = false;
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
  });
});
