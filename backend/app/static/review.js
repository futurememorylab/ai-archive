function reviewQueue(clipId) {
  return {
    queue: [],
    init() {
      try { this.queue = JSON.parse(sessionStorage.getItem('catdv:reviewQueue') || '[]'); }
      catch (e) { this.queue = []; }
      document.addEventListener('change', e => {
        const t = e.target;
        if (t.classList.contains('ri-accept')) {
          this._decide(t.dataset.itemId, t.checked ? 'accepted' : 'rejected');
        } else if (t.classList.contains('ri-mfield')) {
          this._decideMarker(t.closest('.ri-marker'));
        } else if (t.classList.contains('ri-edit')) {
          const val = t.dataset.multi === '1'
            ? t.value.split(',').map(s => s.trim()).filter(s => s.length)
            : t.value;
          this._decide(t.dataset.itemId, 'accepted', val);
        } else if (t.classList.contains('ri-note')) {
          this._decide(t.dataset.itemId, 'accepted', t.value);
        }
      });
    },
    _idx() { return this.queue.indexOf(clipId); },
    progressLabel() {
      const i = this._idx();
      return i >= 0 ? `${i + 1} / ${this.queue.length}` : '';
    },
    async _decide(itemId, decision, editedValue) {
      const body = { decision };
      if (editedValue !== undefined) body.edited_value = editedValue;
      try {
        const r = await fetch(`/api/review/items/${itemId}/decision`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          console.error(`decision persist failed for item ${itemId}: ${r.status}`);
          Alpine.store('toast').push(
            `Decision not saved (HTTP ${r.status}).`,
            { level: 'error' },
          );
        }
      } catch (e) {
        console.error(`decision persist error for item ${itemId}`, e);
        Alpine.store('toast').push(
          `Decision not saved: ${e.message || String(e)}`,
          { level: 'error' },
        );
      }
    },
    _next() {
      const i = this._idx();
      if (i >= 0 && i + 1 < this.queue.length) {
        location.href = `/clips/${this.queue[i + 1]}?review=1`;
      } else {
        this._exitReview();
      }
    },
    _exitReview() {
      // End of the review queue — return to the clips list we came from.
      // (There is no standalone /review page on this build, so navigating
      // there 404'd at the end of the queue / on a single-clip review.)
      location.href = '/' + (sessionStorage.getItem('catdv:clipsListQuery') || '');
    },
    _decideMarker(container) {
      if (!container) return;
      const itemId = container.dataset.itemId;
      const get = k => {
        const el = container.querySelector(`.ri-mfield[data-k="${k}"]`);
        return el ? el.value : '';
      };
      const edited = {
        name: get('name'),
        category: get('category') || null,
        description: get('description') || null,
        in: { secs: parseFloat(get('in')) || 0 },
      };
      const outRaw = get('out');
      if (outRaw !== '' && !isNaN(parseFloat(outRaw))) {
        edited.out = { secs: parseFloat(outRaw) };
      }
      // editing implies keep
      const keep = container.querySelector('.ri-accept');
      if (keep) keep.checked = true;
      this._decide(itemId, 'accepted', edited);
    },
    prev() {
      const i = this._idx();
      if (i > 0) location.href = `/clips/${this.queue[i - 1]}?review=1`;
      else this._exitReview();
    },
    skip() { this._next(); },
    async applyAndNext() {
      // Pre-accepted opt-out: make the current checkbox state authoritative,
      // then apply. Accept all currently-checked items first.
      const checked = Array.from(document.querySelectorAll('.ri-accept:checked'));
      await Promise.all(checked.map(cb => this._decide(cb.dataset.itemId, 'accepted')));
      const r = await fetch(`/api/review/clips/${clipId}/apply`, { method: 'POST' });
      if (r.ok) {
        this._next();
      } else {
        Alpine.store('toast').push(
          `Apply failed (${r.status}). Nothing was applied; staying on this clip.`,
          { level: 'error' },
        );
      }
    },
    async applyStay() {
      const checked = Array.from(document.querySelectorAll('.ri-accept:checked'));
      await Promise.all(checked.map(cb => this._decide(cb.dataset.itemId, 'accepted')));
      // HX-Request: true makes the apply route return the re-rendered draft
      // aside partial instead of JSON, so we swap it in place (no full
      // reload) and toast success. `applyAndNext` deliberately omits the
      // header — it navigates away on success and still wants the JSON path.
      const r = await fetch(`/api/review/clips/${clipId}/apply`, {
        method: 'POST',
        headers: { 'HX-Request': 'true' },
      });
      if (r.ok) {
        const html = await r.text();
        const aside = document.getElementById('draft-aside');
        if (aside) {
          aside.innerHTML = html;
          // Re-scan the injected subtree through the single lifecycle helper
          // (Alpine.initTree + htmx.process) so the draft panels' x-text /
          // x-for / @click="seek(...)" directives come alive. The partial
          // has no hx-* attributes, so the htmx.process pass is a harmless
          // no-op. (Direct Alpine.initTree calls are reserved to
          // htmxAlpine.js — see test_htmx_alpine_single_lifecycle.)
          window.htmxAlpine.reinit(aside);
        }
        Alpine.store('toast').push('Changes applied.', { level: 'success' });
      } else {
        Alpine.store('toast').push(
          `Apply failed (${r.status}). Nothing was applied.`,
          { level: 'error' },
        );
      }
    },
  };
}
