/* Alpine.data('cacheActions') — per-clip cache control on the clip detail
 * page (badge + Cache / Purge / Evict buttons, rendered by
 * pages/_cache_actions.html).
 *
 * Caching a clip is an async background job: POST /api/cache/prefetch
 * enqueues it, the media prefetcher drains it, and the row lands `done` or
 * `error` in /api/cache/prefetch/queue. This component gives that the
 * feedback it was missing — an info toast on start, a spinner while it
 * runs, and a success/error toast on finish — then re-fetches the control
 * (GET /ui/cache-actions/{id}) and swaps it in place so Cache flips to
 * Purge without a full-page reload (CLAUDE.md: no location.reload()).
 */
document.addEventListener('alpine:init', () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

  Alpine.data('cacheActions', ({ id, kind }) => ({
    id,
    kind: kind || 'video',
    busy: false,
    busyLabel: 'Working…',

    async cacheNow() {
      if (this.busy) return;
      const toast = Alpine.store('toast');
      this.busy = true;
      this.busyLabel = 'Caching…';
      toast.push(`Caching ${this.kind}…`, { level: 'info' });
      try {
        const res = await fetch('/api/cache/prefetch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clip_keys: [['catdv', String(this.id)]] }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const rid = (data.ids || [])[0] ?? null;
        await this._pollUntilDone(rid);
        toast.push(`${cap(this.kind)} cached.`, { level: 'success' });
        await this._refresh();
      } catch (e) {
        this.busy = false;
        toast.push(`Caching failed: ${e.message || e}`, { level: 'error' });
      }
    },

    async purge() {
      if (this.busy) return;
      if (!confirm(`Purge the cached ${this.kind} for this clip?`)) return;
      await this._evict(['media-ai'], 'Purging…', 'Cache purged.', 'Purge');
    },

    async evictLocal() {
      if (this.busy) return;
      if (!confirm('Evict the local proxy for this clip?')) return;
      await this._evict(['media-local'], 'Evicting…', 'Local proxy evicted.', 'Evict');
    },

    async _evict(layers, busyLabel, okMsg, verb) {
      const toast = Alpine.store('toast');
      this.busy = true;
      this.busyLabel = busyLabel;
      try {
        const res = await fetch(`/api/cache/clip/catdv/${this.id}/evict`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ layers, force: false }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        toast.push(okMsg, { level: 'success' });
        await this._refresh();
      } catch (e) {
        this.busy = false;
        toast.push(`${verb} failed: ${e.message || e}`, { level: 'error' });
      }
    },

    async _pollUntilDone(rid) {
      const deadline = Date.now() + 5 * 60 * 1000;
      while (Date.now() < deadline) {
        await sleep(1500);
        let res;
        try {
          res = await fetch('/api/cache/prefetch/queue');
        } catch {
          continue; // transient; keep polling
        }
        if (!res.ok) continue;
        const q = await res.json();
        const rows = [...(q.active || []), ...(q.recent || [])];
        const mine = rows.find(
          (r) =>
            String(r.provider_clip_id) === String(this.id) &&
            (rid == null || r.id === rid),
        );
        if (!mine) continue;
        if (mine.status === 'done') return;
        if (mine.status === 'error') throw new Error(mine.error || 'prefetch failed');
        if (mine.status === 'cancelled') throw new Error('cancelled');
      }
      throw new Error('timed out waiting for the cache to fill');
    },

    async _refresh() {
      try {
        const res = await fetch(
          `/ui/cache-actions/${this.id}?kind=${encodeURIComponent(this.kind)}`,
        );
        if (!res.ok) {
          this.busy = false;
          return;
        }
        const html = await res.text();
        const node = document.getElementById(`cache-ctl-${this.id}`);
        if (!node) {
          this.busy = false;
          return;
        }
        node.outerHTML = html;
        const fresh = document.getElementById(`cache-ctl-${this.id}`);
        if (fresh && window.htmxAlpine) window.htmxAlpine.reinit(fresh);
      } catch {
        this.busy = false;
      }
    },
  }));
});
