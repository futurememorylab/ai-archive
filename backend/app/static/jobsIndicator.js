// Persistent top-bar batch indicator. Aggregates progress across all active
// jobs. Renders on every page (lives in the topbar pillset), so it re-derives
// state from /api/jobs/active on load, then live-updates via /api/jobs/events.
function jobsIndicator() {
  return {
    jobs: {}, // id -> { done, total, errors, status }
    failed: false,

    async init() {
      await this.refresh();
      window.addEventListener("jobs-changed", () => this.refresh());
      const es = new EventSource("/api/jobs/events");
      es.onmessage = (evt) => {
        let p;
        try { p = JSON.parse(evt.data); } catch { return; }
        if (["completed", "cancelled"].includes(p.status) && !p.errors) {
          delete this.jobs[p.job_id];
        } else {
          this.jobs[p.job_id] = {
            done: p.done, total: p.total, errors: p.errors, status: p.status,
          };
          if (p.status === "failed" || p.errors) this.failed = true;
        }
      };
    },

    async refresh() {
      try {
        const r = await fetch("/api/jobs/active");
        if (!r.ok) return;
        const list = await r.json();
        const next = {};
        for (const j of list) {
          next[j.id] = { done: j.done, total: j.total, errors: j.errors, status: j.status };
        }
        this.jobs = next;
      } catch { /* offline — leave current state */ }
    },

    activeIds() { return Object.keys(this.jobs).map(Number); },
    visible() { return this.activeIds().length > 0 || this.failed; },
    done() { return this.activeIds().reduce((n, id) => n + (this.jobs[id].done || 0), 0); },
    total() { return this.activeIds().reduce((n, id) => n + (this.jobs[id].total || 0), 0); },
    hasErrors() {
      return this.failed ||
        this.activeIds().some((id) => (this.jobs[id].errors || 0) > 0);
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
  };
}
window.jobsIndicator = jobsIndicator;
