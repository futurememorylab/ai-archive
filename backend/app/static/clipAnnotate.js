function clipAnnotate(clipId) {
  return {
    open: false,
    prompts: null,
    loading: false,
    error: null,

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

    async pick(prompt, root) {
      this.open = false;
      root.runError = null;
      root.runStatus = "starting";
      root.running = true;
      root.runningPromptName = prompt.name;
      root.scope = "draft";

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
        root.jobId = data.id;
        this.attachStream(root, root.jobId);
      } catch (e) {
        root.runError = String(e);
        root.runStatus = null;
        root.running = false;
      }
    },

    attachStream(root, jobId) {
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
          root.runError = payload.error || "Unknown error";
          root.runStatus = null;
          root.running = false;
          es.close();
          return;
        }
        const label = STATUS_LABEL[payload.status];
        if (label) root.runStatus = label;
        if (payload.status === "review_ready") {
          await this.swapDraft(root);
          es.close();
        }
      };
      es.onerror = () => {
        es.close();
        // Fall back to polling — Task 14.
        this.pollJob(root, jobId);
      };
    },

    async swapDraft(root) {
      const r = await fetch(`/clips/${clipId}/draft`);
      if (!r.ok) {
        root.runError = `Failed to load draft: HTTP ${r.status}`;
        root.running = false;
        return;
      }
      const html = await r.text();
      const target = document.getElementById("draft-aside");
      if (target) target.innerHTML = html;
      root.runStatus = null;
      root.running = false;
    },

    pollJob(root, jobId) {
      // Implemented in Task 14.
    },
  };
}
window.clipAnnotate = clipAnnotate;
