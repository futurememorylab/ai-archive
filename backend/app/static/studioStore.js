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

   Run lifecycle:
     1. POST /api/studio/runs {prompt_version_id, clip_id, model}
        → {run_id, job_id}
     2. Poll GET /api/studio/runs/{run_id} every 1s until status != pending|running
     3. On completion (ok or error), trigger a UI refresh by incrementing
        pendingRunSwap; the prompt card watches that and re-fetches its
        output partial.
*/
document.addEventListener('alpine:init', () => {
  Alpine.store('studio', {
    // ── Shared page state (seeded by hydrate()) ──────────────────────
    promptId: null,
    activeVersionId: null,
    activeVersionNum: null,
    activeModel: '',
    compareVersionId: null,
    compareVersionNum: null,
    mode: 'prompt',  // page-level tab state
    focusedClipId: null,
    showList: true,
    showPlayer: true,
    layout: 'under',                 // 'under' | 'right'
    // ── Split-pane sizes (persisted; see studioResize.js) ────────────
    // null = use the CSS default (320px / 1fr / 50%). Stored as the raw
    // CSS value string (e.g. '360px', '42%').
    playerH: null,                   // player height in 'under' layout
    playerW: null,                   // player width in 'right' layout
    cmpCur: null,                    // cur card width as % of the compare row
    // ── Run-button state machine ─────────────────────────────────────
    running: false,
    cancelling: false,
    runId: null,
    runJobId: null,
    runStartMs: 0,
    runningElapsedLabel: '0:00',
    doneFlashUntilMs: 0,
    cancelledFlashUntilMs: 0,
    _cancelRequested: false,
    _nowMs: 0,   // bumped by the 1Hz ticker so runButtonLabel re-evaluates
    pendingRunSwap: 0,
    // Bumped by studioPromptCard.save() so the compare diff (which watches
    // this) recomputes against the just-saved version.
    savedTick: 0,
    _hydrated: false,

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
        if (this.cancelledFlashUntilMs && now >= this.cancelledFlashUntilMs) {
          this.cancelledFlashUntilMs = 0;
        }
      }, 1000);
    },

    runButtonLabel() {
      // Mirror of tests/_helpers/studio_state.py::run_button_label
      const now = this._nowMs || performance.now();
      if (this.doneFlashUntilMs && now < this.doneFlashUntilMs) return '✓ Done';
      if (this.cancelledFlashUntilMs && now < this.cancelledFlashUntilMs) return '⊘ Cancelled';
      if (this.cancelling) return '⟳ Cancelling…';
      if (this.running) return `⟳ Running… ${this.runningElapsedLabel}`;
      const v = (this.activeVersionNum !== null && this.activeVersionNum !== undefined)
        ? this.activeVersionNum : '?';
      return `▶ Run on this clip · v${v}`;
    },

    async runOrCancel() {
      // Both flashes block re-entry — otherwise a double-click during
      // either flash would start a new run while the button still
      // visually shows ✓/⊘.
      if (this.cancelling || this.doneFlashUntilMs || this.cancelledFlashUntilMs) return;
      if (this.running) return this.cancel();
      return this.runOnFocusedClip();
    },

    async cancel() {
      if (!this.runJobId || this.cancelling) return;
      this._cancelRequested = true;
      this.cancelling = true;
      try {
        await fetch(`/api/jobs/${this.runJobId}/cancel`, { method: 'POST' });
      } catch (err) {
        console.error('cancel request failed', err);
        // Keep polling; if the server didn't get the cancel, the run
        // will finish normally and we'll surface that.
      }
      // Do NOT flip this.running here. Let _poll() observe the terminal
      // status and dispatch the right UI state (Cancelled / Done /
      // Completed-before-cancel).
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
        Alpine.store('toast').push(
          `Run failed: ${err.message || String(err)}`,
          { level: 'error' },
        );
      } finally {
        this.running = false;
        this.cancelling = false;
        this.runJobId = null;
        this.pendingRunSwap++;
        if (finalStatus === 'ok') {
          this.doneFlashUntilMs = performance.now() + 1200;
          // If the user had pressed Cancel but the server completed first,
          // tell them via the toast layer (added in T2-3, guarded behind
          // window.Alpine?.store?.('toast')).
          if (window.Alpine?.store?.('toast') && this._cancelRequested) {
            window.Alpine.store('toast').push(
              'Completed before cancel landed — output saved.',
              { level: 'info' },
            );
          }
        } else if (finalStatus === 'cancelled') {
          this.cancelledFlashUntilMs = performance.now() + 1200;
        }
        // No flash for error — error state is surfaced by the run-output partial.
        this._cancelRequested = false;
      }
    },

    async _poll(runId) {
      while (this.running) {
        await new Promise(r => setTimeout(r, 1000));
        const res = await fetch(`/api/studio/runs/${runId}`);
        if (!res.ok) {
          // Network blip; keep trying.
          continue;
        }
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
      // Re-init the injected cmp card: Alpine re-scans its directives and
      // HTMX wires the version-picker hx-* attributes (without process, the
      // cmp version-pick would be a dead click). htmxAlpine owns both.
      window.htmxAlpine.reinit(slot);
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
  });
});
