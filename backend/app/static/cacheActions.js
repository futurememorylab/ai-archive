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

    // Caching is a background job that outlives the page: if the user reloads
    // or navigates away and back while a prefetch for this clip is still in
    // flight, the server re-renders the plain Cache button with no progress.
    // On mount, check the live queue and resume the spinner + poll so the
    // in-progress state survives a reload (testing finding, issue #78).
    async init() {
      let res;
      try {
        res = await fetch('/api/cache/prefetch/queue');
      } catch {
        return; // offline / transient — nothing to resume
      }
      if (!res.ok) return;
      let q;
      try {
        q = await res.json();
      } catch {
        return;
      }
      const mine = (q.active || []).find(
        (r) =>
          String(r.provider_clip_id) === String(this.id) &&
          (r.status === 'queued' || r.status === 'downloading') &&
          // An annotate job drives its own progress in the annotate button —
          // don't also spin the cache badge for it (one indicator, not two).
          r.requested_by !== 'annotate',
      );
      if (!mine) return;
      this.busy = true;
      this.busyLabel = 'Caching…';
      try {
        await this._watch(mine.id);
      } catch (e) {
        this.busy = false;
        Alpine.store('toast').push(`Caching failed: ${e.message || e}`, {
          level: 'error',
        });
      }
    },

    // Annotation of an uncached clip caches it as its first step, but the
    // annotate button shows that progress itself — the cache badge must NOT
    // also spin (one indicator, not two). So we react only when caching is
    // DONE, to flip the badge to its cached state.
    onAnnotateCached() {
      // _refresh() re-fetches the control and swaps the node (busy resets to
      // false on the fresh node), or clears busy itself on a fetch failure.
      this._refresh();
    },

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
        await this._watch(rid);
      } catch (e) {
        this.busy = false;
        toast.push(`Caching failed: ${e.message || e}`, { level: 'error' });
      }
    },

    // Poll a running prefetch to completion, then toast + swap in the fresh
    // (now-cached) control. Shared by cacheNow() and the init() resume path;
    // assumes busy is already set. Throws on prefetch error/timeout so the
    // caller's catch can surface it.
    async _watch(rid) {
      await this._pollUntilDone(rid);
      Alpine.store('toast').push(`${cap(this.kind)} cached.`, {
        level: 'success',
      });
      await this._refresh();
    },

    async purge() {
      if (this.busy) return;
      if (!confirm(`Purge the cached ${this.kind} for this clip?`)) return;
      // Clear BOTH media layers so the clip is genuinely uncached. In local
      // dev a clip can sit in the local proxy cache AND the AI store at once
      // (annotation uploads to GCS even in local mode), so evicting only one
      // leaves the other behind and the clip still counts as cached.
      await this._evict(['media-local', 'media-ai'], 'Purging…', 'Cache purged.', 'Purge');
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
        if (mine.status === 'downloading' && mine.bytes_total > 0) {
          const pct = Math.floor((100 * mine.bytes_downloaded) / mine.bytes_total);
          this.busyLabel = `Caching… (${pct}%)`;
        }
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
