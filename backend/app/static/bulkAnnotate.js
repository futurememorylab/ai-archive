// Bulk "Annotate selected" — composed into bulkSel() on the clips list.
// Groups the current selection by media kind, lets the user assign one
// production prompt per kind, then creates one /api/jobs job per assigned
// kind. Mirrors the prompt-loading + job-kickoff logic in clipAnnotate.js.
function bulkAnnotateMixin() {
  return {
    annoOpen: false,
    annoLoading: false,
    annoError: null,
    // [{ kind, clipIds: [int], promptVersionId: int|null }]
    annoGroups: [],
    annoPromptsByKind: {}, // kind -> [{id, name, current_production_version_id, media_kind}]

    async openAnnotate() {
      // Group selected rows by their media kind (read from the .col-type cell,
      // same approach reviewSelected() uses for .col-drafts).
      const groups = {};
      for (const el of this._selected()) {
        const id = parseInt(el.value.split("/")[1], 10);
        if (isNaN(id)) continue;
        const kind = (
          el.closest("tr")?.querySelector(".col-type")?.textContent || "video"
        ).trim();
        (groups[kind] ||= []).push(id);
      }
      this.annoGroups = Object.entries(groups).map(([kind, clipIds]) => ({
        kind,
        clipIds,
        promptVersionId: null,
      }));
      if (!this.annoGroups.length) return;
      this.annoOpen = true;
      this.annoError = null;
      await this._loadPrompts();
    },

    async _loadPrompts() {
      this.annoLoading = true;
      try {
        const r = await fetch("/api/prompts?archived=0");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const all = (await r.json()).filter(
          (p) => p.current_production_version_id != null,
        );
        for (const g of this.annoGroups) {
          this.annoPromptsByKind[g.kind] = all.filter(
            (p) => p.media_kind === g.kind || p.media_kind === "any",
          );
        }
      } catch (e) {
        this.annoError = String(e);
      } finally {
        this.annoLoading = false;
      }
    },

    annoSkippedCount() {
      return this.annoGroups
        .filter((g) => !g.promptVersionId)
        .reduce((n, g) => n + g.clipIds.length, 0);
    },
    annoRunCount() {
      return this.annoGroups
        .filter((g) => g.promptVersionId)
        .reduce((n, g) => n + g.clipIds.length, 0);
    },
    annoRunnable() {
      return this.annoGroups.some((g) => g.promptVersionId);
    },

    async runAnnotate() {
      if (!this.annoRunnable()) return;
      for (const g of this.annoGroups) {
        if (!g.promptVersionId) continue;
        await fetch("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            prompt_version_id: g.promptVersionId,
            clip_ids: g.clipIds,
            auto_start: true,
          }),
        });
      }
      this.annoOpen = false;
      // Nudge the topbar indicator to pick up the new jobs immediately.
      window.dispatchEvent(new CustomEvent("jobs-changed"));
    },
  };
}
window.bulkAnnotateMixin = bulkAnnotateMixin;
