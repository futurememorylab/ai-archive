// reviewMixin — data-driven Draft review, composed into the clip-detail
// x-data alongside player() + clipAnnotate(). Operates on the Alpine arrays
// draftMarkers / draftFields / draftNotes (each item: { item_id, status, … }).
// Persists via /api/review/items/{id}/decision and /clips/{id}/apply; marker
// in/out edits go through player.js::_persistMarker. Walks a clip queue held
// in sessionStorage['catdv:reviewQueue'] (seeded by the clips list / batches).
function reviewMixin(clipId) {
  return {
    reviewQueue: [],
    _reviewInit() {
      try { this.reviewQueue = JSON.parse(sessionStorage.getItem("catdv:reviewQueue") || "[]"); }
      catch (e) { this.reviewQueue = []; }
    },
    // ── counts ────────────────────────────────────────────────────
    _allDraft() { return [...this.draftMarkers, ...this.draftFields, ...this.draftNotes]; },
    totalCount() { return this._allDraft().length; },
    // ── queue / walk ─────────────────────────────────────────────
    _qIdx() { return this.reviewQueue.indexOf(clipId); },
    reviewPos() { const i = this._qIdx(); return i >= 0 ? (i + 1) : 1; },
    reviewLen() { const i = this._qIdx(); return i >= 0 ? this.reviewQueue.length : 1; },
    navClip(d) {
      const i = this._qIdx();
      if (i < 0) return;
      const t = i + d;
      if (t < 0 || t >= this.reviewQueue.length) return;
      location.href = `/clips/${this.reviewQueue[t]}?review=1&scope=draft`;
    },
    // ── accept / delete / edit ───────────────────────────────────
    // In-flight decision POSTs. `applyDraft` awaits these before enqueuing the
    // upstream apply, so a freshly-accepted item can't be missed by a race
    // between the (fire-and-forget) decision write and the apply read.
    _inflight: new Set(),
    _persist(item, decision, editedValue) {
      const body = { decision };
      if (editedValue !== undefined) body.edited_value = editedValue;
      const p = fetch(`/api/review/items/${item.item_id}/decision`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); })
        .catch(e => {
          Alpine.store("toast").push(`Decision not saved: ${e.message || e}`, { level: "error" });
        });
      this._inflight.add(p);
      p.finally(() => this._inflight.delete(p));
      return p;
    },
    acceptAll() {
      for (const it of this._allDraft()) {
        if (it.status !== "accepted") { it.status = "accepted"; this._persist(it, "accepted"); }
      }
    },
    del(item, ev) {
      if (ev) ev.stopPropagation();
      for (const key of ["draftMarkers", "draftFields", "draftNotes"]) {
        const i = this[key].findIndex(x => x.item_id === item.item_id);
        if (i >= 0) { this[key].splice(i, 1); break; }
      }
      if (this.editingItemId === item.item_id) this.editingItemId = null;
      this._persist(item, "rejected");
      Alpine.store("toast").push("Proposal deleted.", { level: "info" });
    },
    toggleEdit(itemId) {
      this.editingItemId = (this.editingItemId === itemId ? null : itemId);
      const m = this.draftMarkers.find(x => x.item_id === itemId);
      if (this.editingItemId && m) this.seek(m.in_secs);
    },
    // Persist a field/note edit (markers persist via player._persistMarker).
    persistField(item) { item.status = "accepted"; this._persist(item, "accepted", item.value); },
    persistNote(item) { item.status = "accepted"; this._persist(item, "accepted", item.text); },
    // ── accept everything + apply, in one click ─────────────────
    // Accepts every still-visible proposal (rejected/deleted ones are already
    // gone from the arrays), waits for those decisions to persist, then applies.
    async acceptApplyAll() {
      this.acceptAll();
      await this.applyDraft();
    },
    // ── apply (stay) + refresh ───────────────────────────────────
    async applyDraft() {
      try {
        // Wait for any in-flight accept/edit decisions to land first, so the
        // upstream apply enqueues exactly what the UI shows as accepted.
        await Promise.allSettled([...this._inflight]);
        const r = await fetch(`/api/review/clips/${clipId}/apply`, { method: "POST" });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        Alpine.store("toast").push("Accepted proposals applied.", { level: "success" });
        await this.refreshDraft();
      } catch (e) {
        Alpine.store("toast").push(`Apply failed: ${e.message || e}. Nothing was applied.`, { level: "error" });
      }
    },
    async refreshDraft() {
      try {
        const r = await fetch(`/api/review/clips/${clipId}/draft-data`);
        if (!r.ok) return;
        const d = await r.json();
        // Replace arrays in place so player()'s draftMarkers ref stays bound.
        this.draftMarkers.splice(0, this.draftMarkers.length, ...d.markers);
        this.draftFields.splice(0, this.draftFields.length, ...d.fields);
        this.draftNotes.splice(0, this.draftNotes.length, ...d.notes);
        this.editingItemId = null;
      } catch (e) { /* keep current view */ }
    },
  };
}
window.reviewMixin = reviewMixin;
