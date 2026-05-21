function clipAnnotate(clipId) {
  return {
    open: false,
    prompts: null,
    loading: false,
    error: null,
    running: false,
    runningPromptName: null,
    runStatus: null,
    runError: null,
    jobId: null,

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
          (p) => p.current_production_version_id != null,
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
      this.runningPromptName = prompt.name;
      this.scope = "draft";

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
        this.runError = String(e);
        this.runStatus = null;
        this.running = false;
      }
    },

    attachStream(jobId) {
      const STATUS_LABEL = {
        resolving:    "Locating proxy…",
        uploading:    "Uploading proxy to GCS…",
        prompting:    "Calling Gemini…",
        review_ready: "Done — loading draft…",
      };
      const es = new EventSource(`/api/jobs/${jobId}/events`);
      es.onmessage = async (evt) => {
        let payload;
        try { payload = JSON.parse(evt.data); } catch { return; }
        if (payload.status === "error") {
          this.runError = payload.error || "Unknown error";
          this.runStatus = null;
          this.running = false;
          es.close();
          return;
        }
        const label = STATUS_LABEL[payload.status];
        if (label) this.runStatus = label;
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
      const r = await fetch(`/clips/${clipId}/draft`);
      if (!r.ok) {
        this.runError = `Failed to load draft: HTTP ${r.status}`;
        this.running = false;
        return;
      }
      const html = await r.text();
      const target = document.getElementById("draft-aside");
      if (target) target.innerHTML = html;
      this.runStatus = null;
      this.running = false;
    },

    async pollJob(jobId) {
      const TERMINAL = new Set(["completed", "failed", "cancelled"]);
      const STATUS_LABEL = {
        running: "Calling Gemini…",
      };
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
        if (STATUS_LABEL[job.status]) this.runStatus = STATUS_LABEL[job.status];
        if (TERMINAL.has(job.status)) {
          if (job.status === "completed") {
            await this.swapDraft();
          } else {
            const errItem = (job.items || []).find((it) => it.status === "error");
            this.runError = errItem?.error || `Job ${job.status}`;
            this.runStatus = null;
            this.running = false;
          }
          return;
        }
      }
    },
  };
}
window.clipAnnotate = clipAnnotate;
