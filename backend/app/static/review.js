function reviewSel() {
  return Object.assign(rowSelect(), {
    kinds: { marker: true, field: true, note: true },
    init() { this.initSelection(); },
    _selectedClipIds() {
      return this._selected().map(el => parseInt(el.value.split('/')[1], 10));
    },
    _activeKinds() {
      return Object.entries(this.kinds).filter(([, on]) => on).map(([k]) => k);
    },
    reviewSelected() {
      const ids = this._selectedClipIds();
      if (!ids.length) return;
      sessionStorage.setItem('catdv:reviewQueue', JSON.stringify(ids));
      location.href = `/clips/${ids[0]}?review=1`;
    },
    async applySelected() {
      const clip_ids = this._selectedClipIds();
      const kinds = this._activeKinds();
      if (!clip_ids.length || !kinds.length) return;
      if (!confirm(`Apply ${kinds.join(', ')} drafts for ${clip_ids.length} clip(s)?`)) return;
      const r = await fetch('/api/review/apply-batch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ clip_ids, kinds }),
      });
      if (r.ok) htmx.ajax('GET', window.location.href, '#review-table-region');
    },
  });
}
