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

    pick(prompt) {
      // Task 12 fills this in.
      this.open = false;
    },
  };
}
window.clipAnnotate = clipAnnotate;
