/* Studio Run button — the single-clip run lifecycle, mixed into
   `Alpine.store('studio')` (studioStore.js spreads `studioRunButton()` into
   the store, so `this` below is the store).

   Run lifecycle:
     1. POST /api/studio/runs {prompt_version_id, clip_id, model}
        → {run_id, job_id}
     2. Poll GET /api/studio/runs/{run_id} every 1s until status != pending|running
     3. On completion (ok or error), trigger a UI refresh by incrementing
        pendingRunSwap; the prompt card watches that and re-fetches its
        output partial.

   The bulk "Run on N clips" path (runOnSelectedClips) stays in the store
   with the selection it runs; the button label covers both. So the mixin
   expects these store fields: selectedClipIds, bulkRunning / bulkDone /
   bulkTotal, runOnSelectedClips(), activeVersionId / activeVersionNum,
   focusedClipId and activeModel.
*/

window.studioRunButton = () => ({
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

  // Called once a second by the ticker hydrate() starts in the store.
  _tickRunButton(now) {
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
  },

  runButtonLabel() {
    // Mirror of tests/_helpers/studio_state.py::run_button_label
    const now = this._nowMs || performance.now();
    if (this.doneFlashUntilMs && now < this.doneFlashUntilMs) return '✓ Done';
    if (this.cancelledFlashUntilMs && now < this.cancelledFlashUntilMs) return '⊘ Cancelled';
    if (this.cancelling) return '⟳ Cancelling…';
    if (this.bulkRunning) return `⟳ Running… ${this.bulkDone}/${this.bulkTotal}`;
    if (this.running) return `⟳ Running… ${this.runningElapsedLabel}`;
    const v = (this.activeVersionNum !== null && this.activeVersionNum !== undefined)
      ? this.activeVersionNum : '?';
    const n = this.selectedClipIds.length;
    if (n > 0) return `▶ Run on ${n} clip${n === 1 ? '' : 's'} · v${v}`;
    return `▶ Run on this clip · v${v}`;
  },

  async runOrCancel() {
    // Both flashes block re-entry — otherwise a double-click during
    // either flash would start a new run while the button still
    // visually shows ✓/⊘.
    if (this.cancelling || this.doneFlashUntilMs || this.cancelledFlashUntilMs) return;
    if (this.running) return this.cancel();
    if (this.bulkRunning) return;  // bulk has no cancel; ignore re-clicks
    // Checked clips take priority — the single button runs the selection
    // when any clips are checked, else the focused clip.
    if (this.selectedClipIds.length > 0) return this.runOnSelectedClips();
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
});
