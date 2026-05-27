// Shared row-selection model for the cache + review list pages.
// Returned object is meant to be spread into an Alpine component via
// Object.assign(rowSelect(), { ...page-specific actions }).
function rowSelect() {
  return {
    count: 0,
    totalBytes: 0,
    _selected() {
      return Array.from(document.querySelectorAll('.row-check:checked'));
    },
    _selectedKeys() {
      return this._selected().map(el => el.value.split('/'));
    },
    _recount() {
      const sel = this._selected();
      this.count = sel.length;
      this.totalBytes = sel.reduce(
        (acc, el) => acc + parseInt(el.dataset.bytes || '0', 10), 0);
    },
    bytesHuman(n) { return window.fmtBytes(n); },
    initSelection() {
      document.addEventListener('change', e => {
        if (e.target.classList.contains('row-check')) this._recount();
        if (e.target.id === 'row-select-all') {
          document.querySelectorAll('.row-check').forEach(
            cb => cb.checked = e.target.checked);
          this._recount();
        }
      });
      document.body.addEventListener('htmx:afterSwap', () => this._recount());
    },
    clearSel() {
      document.querySelectorAll('.row-check:checked').forEach(cb => cb.checked = false);
      this._recount();
    },
  };
}
