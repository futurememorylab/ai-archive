// Verbose per-status labels shown in the draft panel's empty state.
const CA_STATUS_LABEL = {
  resolving:    "Locating proxy…",
  uploading:    "Uploading proxy to GCS…",
  prompting:    "Calling Gemini…",
  review_ready: "Done — loading draft…",
};
// The backend runs strictly cache-then-annotate. Collapse the fine-grained
// statuses into the two coarse phases the button narrates, in that order.
const CA_PHASE = {
  resolving: "caching",
  uploading: "caching",
  prompting: "annotating",
};

function clipAnnotate(clipId, clipKind) {
  return {
    open: false,
    prompts: null,
    clipKind: clipKind || "video",
    loading: false,
    error: null,
    running: false,
    phase: null,
    runStatus: null,
    runError: null,
    jobId: null,
    runElapsed: "0:00",
    _timer: null,
    _didUpload: false,
    _cachedAnnounced: false,
    _promptName: null,
    _announcedAnnotating: false,
    historyLoaded: false,
    historyHtml: "",

    _kindLabel() {
      return this.clipKind === "image" ? "Image" : "Video";
    },

    // clipAnnotate and cacheActions are sibling Alpine components — they talk
    // via window CustomEvents (cacheActions listens with @…​.window, which
    // Alpine cleans up automatically when it swaps the badge node).
    _emitCache(name) {
      window.dispatchEvent(new CustomEvent(name, { detail: { id: clipId } }));
    },

    _startRun(promptName) {
      this._promptName = promptName;
      this._didUpload = false;
      this._cachedAnnounced = false;
      this._announcedAnnotating = false;
      this.phase = null;
      this.runElapsed = "0:00";
      this._timer = window.elapsedTimer();
      this._timer.start((label) => { this.runElapsed = label; });
      // No toast here: the backend caches before it annotates, so we let the
      // status stream announce each phase in its true order (caching first,
      // then annotating) rather than claiming "Annotating…" up front.
    },

    // Drive the phase-aware button label + the sequential toasts off the
    // job's status stream. resolving/uploading → "Caching", prompting →
    // "Annotating"; each phase announces itself exactly once. An already-
    // cached clip skips resolving/uploading and enters at prompting, so it
    // goes straight to "Annotating" with no caching toast — correct.
    _applyStatus(status) {
      const phase = CA_PHASE[status];
      if (phase) this.phase = phase;
      if (status === "uploading") this._onUploading();
      if (status === "prompting") {
        // Entering the prompting phase means the proxy upload already
        // finished (the annotator awaits ensure_uploaded before emitting
        // prompting). Settle the cache badge now — it must not keep spinning
        // through the whole annotation — then announce annotating.
        this._onCachingDone();
        this._onAnnotating();
      }
      const label = CA_STATUS_LABEL[status];
      if (label) this.runStatus = label;
    },

    // The job only emits resolving/uploading on a cache miss (annotator
    // uploads the proxy to the AI store). The uploading event is our signal
    // that this run is also caching the clip — mirror the Cache button's
    // feedback. Idempotent: replayed/duplicate frames fire it only once.
    _onUploading() {
      if (this._didUpload) return;
      this._didUpload = true;
      this._emitCache("clip-cache-uploading");
      Alpine.store("toast").push(
        `Caching ${this._kindLabel().toLowerCase()}…`, { level: "info" });
    },

    _onAnnotating() {
      if (this._announcedAnnotating) return;
      this._announcedAnnotating = true;
      Alpine.store("toast").push(
        `Annotating with “${this._promptName}”…`, { level: "info" });
    },

    // Caching finished (proxy is in the AI store). Flip the cache badge out
    // of its "Caching…" spinner to the cached/Purge state and announce it —
    // at the handoff, not at the end of the run. Only when this run actually
    // did the upload, and only once.
    _onCachingDone() {
      if (!this._didUpload || this._cachedAnnounced) return;
      this._cachedAnnounced = true;
      this._emitCache("clip-cache-refresh");
      Alpine.store("toast").push(
        `${this._kindLabel()} cached.`, { level: "success" });
    },

    _finishRun(ok, errMsg) {
      if (this._timer) { this._timer.stop(); this._timer = null; }
      this.running = false;
      this.phase = null;
      this.runStatus = null;
      const toast = Alpine.store("toast");
      if (ok) {
        // The cache badge + "… cached." already settled at the caching→
        // annotating handoff (_onCachingDone); nothing cache-related to do.
        toast.push("Annotation complete.", { level: "success" });
      } else {
        this.runError = errMsg || "Annotation failed";
        toast.push(`Annotation failed: ${this.runError}`, { level: "error" });
        // If we flipped the badge into its "Caching…" spinner but the run
        // died before the handoff settled it, reset it now so it doesn't
        // spin forever.
        if (this._didUpload && !this._cachedAnnounced) {
          this._emitCache("clip-cache-refresh");
        }
      }
    },
    async loadHistory() {
      this.historyLoaded = true;
      const r = await fetch(`/clips/${clipId}/live-history`);
      this.historyHtml = r.ok
        ? await r.text()
        : "<p class='error'>Selhalo načtení.</p>";
    },

    async toggleOpen() {
      this.open = !this.open;
      if (this.open && this.prompts === null) {
        await this.loadPrompts();
      }
    },

    async loadPrompts() {
      this.loading = true;
      this.error = null;
      try {
        const r = await fetch("/api/prompts?archived=0");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.prompts = (data || []).filter(
          (p) =>
            p.current_production_version_id != null &&
            (p.media_kind === this.clipKind || p.media_kind === "any"),
        );
      } catch (e) {
        this.error = String(e);
        this.prompts = [];
      } finally {
        this.loading = false;
      }
    },

    async pick(prompt) {
      this.open = false;
      this.runError = null;
      this.runStatus = "starting";
      this.running = true;
      this.scope = "draft";
      this._startRun(prompt.name);

      try {
        const r = await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt_version_id: prompt.current_production_version_id,
            clip_ids: [clipId],
            auto_start: true,
          }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        this.jobId = data.id;
        this.attachStream(this.jobId);
      } catch (e) {
        this._finishRun(false, String(e));
      }
    },

    attachStream(jobId) {
      const es = new EventSource(`/api/jobs/${jobId}/events`);
      es.onmessage = async (evt) => {
        let payload;
        try { payload = JSON.parse(evt.data); } catch { return; }
        if (payload.status === "error") {
          this._finishRun(false, payload.error || "Unknown error");
          es.close();
          return;
        }
        this._applyStatus(payload.status);
        if (payload.status === "review_ready") {
          await this.swapDraft();
          es.close();
        }
      };
      es.onerror = () => {
        es.close();
        this.pollJob(jobId);
      };
    },

    async swapDraft() {
      // The draft panel is Alpine-data-driven now — repopulate its arrays via
      // the JSON draft-data endpoint (reviewMixin.refreshDraft) rather than
      // swapping the server-rendered partial into #draft-aside.
      if (typeof this.refreshDraft === "function") {
        await this.refreshDraft();
      }
      this.scope = "draft";
      this._finishRun(true);
    },

    async pollJob(jobId) {
      const TERMINAL = new Set(["completed", "failed", "cancelled"]);
      while (this.running) {
        await new Promise((res) => setTimeout(res, 2000));
        let job;
        try {
          const r = await fetch(`/api/jobs/${jobId}`);
          if (!r.ok) continue;
          job = await r.json();
        } catch {
          continue;
        }
        // SSE was unavailable, so mirror the phase off the item status the
        // poll surfaces — the same resolving/uploading/prompting the stream
        // would have emitted (single-clip job, so one item).
        const item = (job.items || [])[0];
        if (item) this._applyStatus(item.status);
        if (TERMINAL.has(job.status)) {
          if (job.status === "completed") {
            await this.swapDraft();
          } else {
            const errItem = (job.items || []).find((it) => it.status === "error");
            this._finishRun(false, errItem?.error || `Job ${job.status}`);
          }
          return;
        }
      }
    },
  };
}
window.clipAnnotate = clipAnnotate;
