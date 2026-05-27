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
        if (!r.ok) console.error(`decision persist failed for item ${itemId}: ${r.status}`);
      } catch (e) {
        console.error(`decision persist error for item ${itemId}`, e);
      }
    },
    _next() {
      const i = this._idx();
      if (i >= 0 && i + 1 < this.queue.length) {
        location.href = `/clips/${this.queue[i + 1]}?review=1`;
      } else {
        location.href = '/review';
      }
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
      else location.href = '/review';
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
        alert(`Apply failed (${r.status}). Nothing was applied; staying on this clip.`);
      }
    },
    async applyStay() {
      const checked = Array.from(document.querySelectorAll('.ri-accept:checked'));
      await Promise.all(checked.map(cb => this._decide(cb.dataset.itemId, 'accepted')));
      const r = await fetch(`/api/review/clips/${clipId}/apply`, { method: 'POST' });
      if (r.ok) location.reload();
      else alert(`Apply failed (${r.status}). Nothing was applied.`);
    },
  };
}
