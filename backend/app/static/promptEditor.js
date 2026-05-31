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

    prompt_name: initial.prompt_name || "",
    mediaKind: initial.media_kind || "any",
    prompt_description: initial.prompt_description || "",
    dupOpen: false,
    dupName: "",
    dupDesc: "",
    dupError: "",
    dupSaving: false,

    MODELS: [
      "gemini-2.5-pro",
      "gemini-2.5-flash",
      "gemini-2.5-flash-lite",
      "gemini-3-flash-preview",
      "gemini-3.1-pro-preview",
      "gemini-3.1-flash-lite",
      "gemini-3.1-flash-lite-preview",
      "gemini-3.5-flash",
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

    openDuplicate() {
      this.menuOpen = false;
      this.dupName = `Copy of ${this.prompt_name}`;
      this.dupDesc = this.prompt_description || "";
      this.dupError = "";
      this.dupOpen = true;
      this.$nextTick(() => {
        const el = this.$refs.dupNameInput;
        if (el) { el.focus(); el.select(); }
      });
    },

    async duplicate() {
      if (this.dupSaving) return;
      const name = this.dupName.trim();
      if (!name) { this.dupError = "Name is required."; return; }
      this.dupSaving = true;
      this.dupError = "";
      try {
        const fd = new FormData();
        fd.set("name", name);
        fd.set("description", this.dupDesc);
        const resp = await fetch(
          `/prompts/${this.prompt_id}/_duplicate`,
          { method: "POST", body: fd, redirect: "follow" }
        );
        if (resp.redirected) { window.location.href = resp.url; return; }
        // Duplicate creates a NEW prompt — this is navigation to a
        // newly-created entity, not an in-place CRUD refresh. Navigate to
        // the new prompt's URL if the server provided one, else fall back
        // to the prompts index. (Never location.reload(): the current
        // prompt is not the thing that changed.)
        if (resp.ok) { window.location.href = resp.url || "/prompts"; return; }
        const data = await resp.json().catch(() => ({ message: resp.statusText }));
        this.dupError = data.message || `duplicate failed (${resp.status})`;
      } catch (e) {
        this.dupError = e.message || "duplicate failed";
      } finally {
        this.dupSaving = false;
      }
    },

    async setMediaKind() {
      try {
        const resp = await fetch(`/api/prompts/${this.prompt_id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ media_kind: this.mediaKind }),
        });
        if (!resp.ok) this.error = `kind update failed (${resp.status})`;
      } catch (e) {
        this.error = String(e);
      }
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
        // Success — re-baseline so `dirty` flips false (which hides the
        // Save button via x-show) and the editor reflects the saved state.
        // No reload/partial-swap is needed: a draft-version PUT changes only
        // body/target_map/output_schema/model, none of which are rendered by
        // a server-side region on this page — `updated_at` is displayed
        // nowhere, the list rail shows name/description/media_kind (unchanged
        // by this PUT), and the version picker shows version_num/state
        // (also unchanged for an in-place draft save). The model picker reads
        // the reactive `draft.model`, so it is already in sync.
        this.initial = { ...this.draft };
        Alpine.store("toast").push("Changes saved.", { level: "success" });
      } finally {
        this.saving = false;
      }
    },
  }));
});
