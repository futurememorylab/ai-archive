// reviewMixin — data-driven Draft review, composed into the clip-detail
// x-data alongside player() + clipAnnotate(). Operates on the Alpine arrays
// draftMarkers / draftFields / draftNotes (each item: { item_id, status, … }).
// Persists via /api/review/items/{id}/decision and /clips/{id}/apply; ALL
// decision writes (incl. marker in/out edits) go through _persist so applyDraft
// can await them. Walks a clip queue held
// in sessionStorage['catdv:reviewQueue'] (seeded by the clips list / batches).
function reviewMixin(clipId) {
  return {
    reviewQueue: [],
    // Writeback state surfaced in the draft message: the upstream apply runs
    // asynchronously in the SyncEngine, so we poll /api/sync/clip/{id}/status
    // to learn when it lands (or fails) and refresh Published without a reload.
    syncState: "idle",   // idle | syncing | synced | failed
    syncProblems: 0,
    _reviewInit() {
      try { this.reviewQueue = JSON.parse(sessionStorage.getItem("catdv:reviewQueue") || "[]"); }
      catch (e) { this.reviewQueue = []; }
      // Reloading a page whose draft was already applied must not show a stale
      // "syncing…" forever — reconcile the message against the real queue once.
      if (this.appliedCount > 0) this._checkSyncOnce();
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
    // between the decision write and the apply read. EVERY decision write in
    // the panel must go through _persist for that guarantee to hold.
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
    // Locate a live draft item by id across the three kind arrays.
    _findDraft(itemId) {
      for (const [key, kind, bucket] of [
        ["draftMarkers", "marker", "markers"],
        ["draftFields", "field", "fields"],
        ["draftNotes", "note", "notes"],
      ]) {
        const it = this[key].find(x => x.item_id === itemId);
        if (it) return { it, key, kind, bucket };
      }
      return null;
    },
    // ── buffered edit: snapshot on open, persist on Save, revert on Cancel ──
    _editSnapshot: null,
    startEdit(itemId, opts = {}) {
      if (this.editingItemId === itemId) return;
      if (this.editingItemId != null) this.saveEdit();   // switching auto-saves
      const f = this._findDraft(itemId);
      if (!f) return;
      this._editSnapshot = JSON.parse(JSON.stringify(f.it));
      this.editingItemId = itemId;
      if (f.kind === "marker" && opts.seek !== false) this.seek(f.it.in_secs, { play: false });
      // format.js autosizes .txt-area on *input* only; an editor opening with
      // existing long text needs one explicit pass once it's visible.
      this.$nextTick(() => {
        this.$root.querySelectorAll(".ri-editor textarea.txt-area").forEach(window.autosize);
      });
    },
    cancelEdit() {
      const f = this.editingItemId != null ? this._findDraft(this.editingItemId) : null;
      if (f && this._editSnapshot) Object.assign(f.it, this._editSnapshot);
      this.editingItemId = null;
      this._editSnapshot = null;
    },
    // One tracked POST per Save. Markers send the full value shape: the
    // backend's COALESCE replaces edited_value wholesale, and write_queue
    // requires {name, in:{secs}} or the marker is silently dropped on apply.
    saveEdit() {
      const f = this.editingItemId != null ? this._findDraft(this.editingItemId) : null;
      this.editingItemId = null;
      this._editSnapshot = null;
      if (!f) return;
      const it = f.it;
      let edited;
      if (f.kind === "marker") {
        edited = {
          name: it.name || "",
          category: it.category != null ? it.category : null,
          description: it.description != null ? it.description : null,
          in: { secs: it.in_secs },
        };
        if (it.color != null) edited.color = it.color;
        if (it.out_secs != null) edited.out = { secs: it.out_secs };
      } else if (f.kind === "field") {
        edited = it.value;
      } else {
        edited = it.text;
      }
      it.status = "accepted";
      this._persist(it, "accepted", edited);
    },
    // ── delete (reject) + restore: nothing is unrecoverable ─────
    del(item, ev) {
      if (ev) ev.stopPropagation();
      const f = this._findDraft(item.item_id);
      if (!f) return;
      if (this.editingItemId === item.item_id) { this.editingItemId = null; this._editSnapshot = null; }
      this[f.key].splice(this[f.key].indexOf(f.it), 1);
      this.draftDeleted[f.bucket].push(f.it);
      this._persist(f.it, "rejected");
      Alpine.store("toast").push("Proposal deleted.", {
        level: "info", ttlMs: 6000,
        action: { label: "Undo", fn: () => this.restore(f.it) },
      });
    },
    restore(item) {
      for (const [bucket, key] of [
        ["markers", "draftMarkers"], ["fields", "draftFields"], ["notes", "draftNotes"],
      ]) {
        const i = this.draftDeleted[bucket].findIndex(x => x.item_id === item.item_id);
        if (i < 0) continue;
        const [it] = this.draftDeleted[bucket].splice(i, 1);
        it.status = "proposed";
        this[key].push(it);
        if (key === "draftMarkers") this[key].sort((a, b) => a.in_secs - b.in_secs);
        this._persist(it, "pending");
        return;
      }
    },
    deletedTotal() {
      const d = this.draftDeleted;
      return d.markers.length + d.fields.length + d.notes.length;
    },
    // ── accept everything + apply, in one click ─────────────────
    // Auto-saves any open buffered edit first (otherwise it would be
    // silently dropped), accepts every still-visible proposal, waits for
    // the decision writes to land, then applies.
    async acceptApplyAll() {
      if (this.editingItemId != null) this.saveEdit();
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
        let queued = 0;
        try { queued = (await r.json()).queued || 0; } catch (e) { /* non-JSON body */ }
        Alpine.store("toast").push("Accepted proposals applied.", { level: "success" });
        await this.refreshDraft();
        // Hand off to the async writeback: poll until it reaches CatDV, then
        // resolve the message + refresh Published. No-op if nothing enqueued.
        if (queued > 0) { this.syncState = "syncing"; this._pollSync(); }
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
        this.draftDeleted.markers.splice(0, this.draftDeleted.markers.length, ...d.deleted.markers);
        this.draftDeleted.fields.splice(0, this.draftDeleted.fields.length, ...d.deleted.fields);
        this.draftDeleted.notes.splice(0, this.draftDeleted.notes.length, ...d.deleted.notes);
        this.appliedCount = d.applied_count;
        this.editingItemId = null;
        this._editSnapshot = null;
      } catch (e) { /* keep current view */ }
    },
    // ── async writeback status surfacing ─────────────────────────
    // Message shown once the draft is fully applied; reflects whether the
    // upstream CatDV writeback has actually landed, is still syncing, or failed.
    _plural(n) { return n === 1 ? "" : "s"; },
    appliedMessage() {
      const n = this.appliedCount;
      if (this.syncState === "synced")
        return `${n} proposal${this._plural(n)} applied and synced to CatDV ✓ — see Published.`;
      if (this.syncState === "failed")
        return `${n} applied, but ${this.syncProblems} change${this._plural(this.syncProblems)} didn't reach CatDV — retry from the Sync drawer.`;
      return `${n} proposal${this._plural(n)} applied — syncing to CatDV; visible under Published once synced.`;
    },
    async _checkSyncOnce() {
      // One-shot reconciliation on page load (no toasts): set the message to
      // match the real queue, and start polling if a writeback is still mid-flight.
      try {
        const r = await fetch(`/api/sync/clip/${clipId}/status`);
        if (!r.ok) return;
        const st = await r.json();
        if (st.problems > 0) { this.syncState = "failed"; this.syncProblems = st.problems; }
        else if (st.unfinished > 0) { this.syncState = "syncing"; this._pollSync(); }
        else if (st.applied > 0) { this.syncState = "synced"; }
      } catch (e) { /* leave state as-is */ }
    },
    async _pollSync() {
      // Poll until the writeback settles, then resolve the message and refresh
      // the Published panel + timeline. Bounded (~90s) so a permanently-offline
      // tunnel leaves an honest "syncing…" rather than spinning forever.
      for (let i = 0; i < 45; i++) {
        await new Promise((res) => setTimeout(res, 2000));
        let st;
        try {
          const r = await fetch(`/api/sync/clip/${clipId}/status`);
          if (!r.ok) continue;
          st = await r.json();
        } catch (e) { continue; }
        if (st.unfinished > 0) { this.syncState = "syncing"; continue; }
        if (st.problems > 0) {
          this.syncState = "failed";
          this.syncProblems = st.problems;
          Alpine.store("toast").push(
            `${st.problems} change${this._plural(st.problems)} didn't reach CatDV. Open the Sync drawer to retry.`,
            { level: "error" });
          return;
        }
        // Settled with nothing unfinished and no problems → applied upstream.
        this.syncState = "synced";
        await this._refreshPublished();
        Alpine.store("toast").push("Synced to CatDV — Published updated.", { level: "success" });
        return;
      }
    },
    async _refreshPublished() {
      // Swap the server-rendered Published panel in place and refresh the
      // player's published-marker timeline from the partial's JSON island.
      try {
        const r = await fetch(`/clips/${clipId}/published`);
        if (!r.ok) return;
        const html = await r.text();
        const cur = document.getElementById("published-panels");
        if (!cur) return;
        cur.outerHTML = html;
        window.htmxAlpine?.reinit(document.getElementById("published-panels"));
        const island = document.getElementById("published-markers-data");
        if (island) {
          try {
            const ms = JSON.parse(island.textContent);
            this.markers.splice(0, this.markers.length, ...ms);
          } catch (e) { /* keep current markers */ }
        }
      } catch (e) { /* keep current view */ }
    },
  };
}
window.reviewMixin = reviewMixin;
