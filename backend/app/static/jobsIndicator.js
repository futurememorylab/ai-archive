// Persistent top-bar batch indicator. Aggregates progress across all active
// jobs. Renders on every page (lives in the topbar pillset), so it re-derives
// state from /api/jobs/active on load, then live-updates via /api/jobs/events.
function jobsIndicator() {
  return {
    jobs: {}, // id -> { done, total, errors, status }
    failed: false,
    _resolving: false, // coalesces refreshes triggered to classify a new job

    async init() {
      await this.refresh();
      window.addEventListener("jobs-changed", () => this.refresh());
      const es = new EventSource("/api/jobs/events");
      es.onmessage = (evt) => {
        let p;
        try { p = JSON.parse(evt.data); } catch { return; }
        // A brand-new job's FIRST event may lack run_group (only /api/jobs/active
        // carries it). Resolve its group via a refresh before classifying, so a
        // calibration job isn't briefly mistaken for a failed batch (or vice
        // versa). Coalesce so a 6-job sweep triggers at most one in-flight fetch.
        if (!(p.job_id in this.jobs) && !p.run_group) {
          if (!this._resolving) {
            this._resolving = true;
            this.refresh().finally(() => { this._resolving = false; });
          }
          return;
        }
        // run_group may be absent on later job-level events; the spread below
        // preserves the prior value (seeded by /api/jobs/active). Decide
        // calibration from the merged record, not the event.
        const priorGroup = this.jobs[p.job_id]?.run_group || "";
        const group = (p.run_group || priorGroup);
        const isCal = group.startsWith("calibration:");
        // Terminal = a real end state only. A partial error COUNT (p.errors) on a
        // still-'running' job must NOT drop it — else a sweep where one clip
        // errors makes the Calibrating… pill vanish mid-run.
        const terminal = ["completed", "cancelled", "failed"].includes(p.status);
        if (isCal) {
          // Calibration jobs surface via their own pill (calibratingCount),
          // not the batch banner. They're terminal on completion AND on
          // failure — drop them so a Gemini error can't stick the pill or
          // raise the batch "failed" flag (that flag is for real batches).
          if (terminal) {
            delete this.jobs[p.job_id];
          } else {
            this.jobs[p.job_id] = {
              ...this.jobs[p.job_id],
              done: p.done, total: p.total, errors: p.errors, status: p.status,
            };
          }
        } else if (["completed", "cancelled"].includes(p.status) && !p.errors) {
          delete this.jobs[p.job_id];
        } else {
          // Preserve the last-known phases — job-level events don't carry them.
          this.jobs[p.job_id] = {
            ...this.jobs[p.job_id],
            done: p.done, total: p.total, errors: p.errors, status: p.status,
          };
          if (p.status === "failed" || p.errors) this.failed = true;
        }
      };
      // Phase transitions (caching→annotating) emit per-item events, not the
      // job-level events this SSE carries, so refresh the phase breakdown on a
      // short cadence WHILE a batch is active. Calibration sweeps are excluded
      // from visible()/activeIds(), so ALSO refresh while one is in flight —
      // otherwise a dropped SSE would leave its pill stale. Idle = no fetch.
      setInterval(() => {
        if (this.visible() || this.calibratingCount() > 0) this.refresh();
      }, 2000);
    },

    async refresh() {
      try {
        const r = await fetch("/api/jobs/active");
        if (!r.ok) return;
        const list = await r.json();
        const next = {};
        for (const j of list) {
          next[j.id] = {
            done: j.done, total: j.total, errors: j.errors, status: j.status,
            phases: j.phases || null, run_group: j.run_group || null,
          };
        }
        this.jobs = next;
      } catch { /* offline — leave current state */ }
    },

    _isCalibration(id) { return (this.jobs[id]?.run_group || "").startsWith("calibration:"); },
    // The batch indicator counts real batches only; calibration sweeps surface
    // via their own pill (calibratingCount), so exclude them here to avoid
    // double-display and mislabelling a sweep as an "Annotating" batch.
    activeIds() { return Object.keys(this.jobs).map(Number).filter((id) => !this._isCalibration(id)); },
    visible() { return this.activeIds().length > 0 || this.failed; },
    done() { return this.activeIds().reduce((n, id) => n + (this.jobs[id].done || 0), 0); },
    total() { return this.activeIds().reduce((n, id) => n + (this.jobs[id].total || 0), 0); },
    hasErrors() {
      return this.failed ||
        this.activeIds().some((id) => (this.jobs[id].errors || 0) > 0);
    },
    // Phase breakdown across active jobs — surfaces the slow upload phase
    // ("Caching") instead of a bare done/total. Falls back to the count when
    // no phase info is available (e.g. before the first /active refresh).
    _sumPhase(key) {
      return this.activeIds().reduce(
        (n, id) => n + ((this.jobs[id].phases || {})[key] || 0), 0);
    },
    caching() { return this._sumPhase("caching"); },
    annotating() { return this._sumPhase("annotating"); },
    queued() { return this._sumPhase("queued"); },
    phaseLabel() {
      const parts = [];
      const c = this.caching(), a = this.annotating(), q = this.queued();
      if (c) parts.push(`Caching ${c}`);
      if (a) parts.push(`Annotating ${a}`);
      if (q) parts.push(`${q} queued`);
      return parts.length ? parts.join(" · ") : `Annotating ${this.done()}/${this.total()}`;
    },

    open() {
      // Link to every active job at once (one bulk action = one job per
      // media kind) so the batch view shows all selected clips, not just
      // the first kind's.
      const ids = this.activeIds();
      window.location.href = ids.length ? `/?batch=${ids.join(",")}` : "/";
    },

    async cancel() {
      for (const id of this.activeIds()) {
        await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
      }
      await this.refresh();
      this.failed = false;
    },

    dismiss() { this.failed = false; this.jobs = {}; },

    calibratingCount() {
      return this._calibrationIds().length;
    },

    _calibrationIds() {
      return Object.keys(this.jobs)
        .map(Number)
        .filter((id) => this._isCalibration(id));
    },

    // Cancel an in-flight calibration sweep from the topbar pill. Calibration
    // jobs are excluded from activeIds(), so cancel() (the batch X) never
    // touches them — this is their dedicated stop affordance.
    async cancelCalibration() {
      for (const id of this._calibrationIds()) {
        await fetch(`/api/jobs/${id}/cancel`, { method: "POST" });
      }
      await this.refresh();
    },
  };
}
window.jobsIndicator = jobsIndicator;
