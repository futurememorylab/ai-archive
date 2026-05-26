/* Studio Alpine components: studioPage(), studioRunView(runId),
 * studioGoldDialog(itemId). Loaded on demand from studio*.html pages. */

function studioPage() {
  return {
    isOnline: true,
    dialog: { kind: null, itemId: null, description: "", extra: {} },

    init() {
      // Determine connection mode from a meta tag if available.
      const meta = document.querySelector('meta[name="catdv-mode"]');
      this.isOnline = !meta || meta.content === "online";

      window.addEventListener("open-add-testbench", () => this.addTestbench());
      window.addEventListener("open-gold", (e) => this.openGold(e.detail));
    },

    async addTestbench() {
      const name = prompt("New testbench name?");
      if (!name) return;
      const r = await fetch("/api/studio/testbenches", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (r.ok) {
        const tb = await r.json();
        window.location.href = `/studio/testbenches/${tb.id}`;
      } else {
        alert("create failed: " + (await r.text()));
      }
    },

    async openAddFolder(tbId) {
      const name = prompt("Folder name?");
      if (!name) return;
      const r = await fetch(`/api/studio/testbenches/${tbId}/folders`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ parent_id: null, name }),
      });
      if (r.ok) window.location.reload();
    },

    async openAddCatdv() {
      const id = prompt("CatDV clip id?");
      if (!id) return;
      // Find first folder
      const folder = document.querySelector("[data-folder-id]");
      const folderId = folder ? parseInt(folder.dataset.folderId) : null;
      if (!folderId) return alert("no folder to add to");
      await fetch(`/api/studio/folders/${folderId}/items:add_catdv`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider_clip_id: id, name: `CatDV #${id}` }),
      });
      window.location.reload();
    },

    async openUpload() {
      const folder = document.querySelector("[data-folder-id]");
      const folderId = folder ? parseInt(folder.dataset.folderId) : null;
      if (!folderId) return alert("no folder to upload into");
      const inp = document.createElement("input");
      inp.type = "file"; inp.accept = "video/*";
      inp.onchange = async () => {
        if (!inp.files[0]) return;
        const fd = new FormData(); fd.append("file", inp.files[0]);
        const r = await fetch(`/api/studio/folders/${folderId}/items:add_upload`, {
          method: "POST", body: fd,
        });
        if (r.ok) window.location.reload();
        else alert("upload failed: " + (await r.text()));
      };
      inp.click();
    },

    async openStartRun(tbId) {
      const pv = prompt("Prompt version id?");
      if (!pv) return;
      const r = await fetch("/api/studio/runs", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ testbench_id: tbId, prompt_version_id: parseInt(pv) }),
      });
      if (r.ok) window.location.reload();
    },

    openGold({ id, body }) {
      this.dialog.itemId = id;
      this.dialog.description = (body && body.description) || "";
      this.dialog.extra = Object.fromEntries(
        Object.entries(body || {}).filter(([k]) => k !== "description")
      );
      this.dialog.kind = "gold";
    },

    async saveGold() {
      const payload = { description: this.dialog.description, ...this.dialog.extra };
      const r = await fetch(`/api/studio/items/${this.dialog.itemId}/gold`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (r.ok) { this.dialog.kind = null; window.location.reload(); }
      else alert("save failed: " + (await r.text()));
    },
  };
}


function studioRunView(runId) {
  return {
    es: null,

    init() {
      this.es = new EventSource(`/api/studio/runs/${runId}/events`);
      this.es.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          if (msg.item_id !== undefined) {
            const row = document.querySelector(`tr[data-item-id="${msg.item_id}"]`);
            if (row) {
              row.dataset.status = msg.status;
              const cell = row.querySelector(".status");
              if (cell) {
                cell.textContent = msg.status;
                cell.className = `status status--${msg.status}`;
              }
            }
          }
          if (msg.run_status) {
            // run finished — refresh page to show final state.
            if (["completed", "failed", "cancelled"].includes(msg.run_status)) {
              setTimeout(() => window.location.reload(), 500);
            }
          }
        } catch (e) { /* ignore parse errors */ }
      };
    },

    async cancelRun() {
      if (!confirm("Cancel this run?")) return;
      await fetch(`/api/studio/runs/${runId}:cancel`, { method: "POST" });
    },
  };
}
