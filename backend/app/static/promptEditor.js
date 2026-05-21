// promptEditor — single Alpine.data factory for the prompt detail pane.
// Tracks dirtiness, manages model + kebab popovers, posts edits via fetch().
document.addEventListener("alpine:init", () => {
  Alpine.data("promptEditor", (initial) => ({
    prompt_id: initial.prompt_id,
    version_id: initial.version_id,
    state: initial.state,
    initial: {
      body: initial.body,
      target_map_text: initial.target_map_text,
      output_schema_text: initial.output_schema_text,
      model: initial.model,
    },
    draft: {
      body: initial.body,
      target_map_text: initial.target_map_text,
      output_schema_text: initial.output_schema_text,
      model: initial.model,
    },
    menuOpen: false,
    modelOpen: false,
    error: "",
    saving: false,

    MODELS: [
      "gemini-2.5-pro",
      "gemini-2.5-flash",
      "gemini-2.5-flash-lite",
      "gemini-2.0-pro",
    ],

    get canEdit() { return this.state === "draft"; },
    get dirty() {
      if (!this.canEdit) return false;
      const d = this.draft, i = this.initial;
      return d.body !== i.body
        || d.target_map_text !== i.target_map_text
        || d.output_schema_text !== i.output_schema_text
        || d.model !== i.model;
    },

    parseOrFail(label, text) {
      try { return JSON.parse(text); }
      catch (e) { throw new Error(`${label}: invalid JSON — ${e.message}`); }
    },

    async save() {
      if (!this.dirty || this.saving) return;
      this.error = "";
      this.saving = true;
      let target_map, output_schema;
      try {
        target_map = this.parseOrFail("target_map", this.draft.target_map_text);
        output_schema = this.parseOrFail("output_schema", this.draft.output_schema_text);
      } catch (e) {
        this.error = e.message;
        this.saving = false;
        return;
      }
      try {
        const resp = await fetch(
          `/api/prompts/${this.prompt_id}/versions/${this.version_id}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              body: this.draft.body,
              target_map: target_map,
              output_schema: output_schema,
              model: this.draft.model,
            }),
          }
        );
        if (!resp.ok) {
          const data = await resp.json().catch(() => ({ message: resp.statusText }));
          this.error = data.message || `save failed (${resp.status})`;
          return;
        }
        // Success — re-baseline and reload the page so version metadata
        // (updated_at, list rail) stays in sync.
        this.initial = { ...this.draft };
        window.location.reload();
      } finally {
        this.saving = false;
      }
    },
  }));
});
