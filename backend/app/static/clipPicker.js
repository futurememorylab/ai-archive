// Shared clip-picker core — the one picker for "search the catalog,
// page through rich rows, keep a selection". Spread into a page's
// Alpine component: { ...window.clipPickerCore(), ...pageSpecific }.
// Used by batchesPage() (pages/batches.html) and archivePicker()
// (static/studio.js). Rows come from the shared /batches/picker
// endpoint (_video_list.html scaffold); markup comes from
// pages/_clip_picker_main.html + pages/_clip_picker_basket.html. See
// docs/specs/2026-06-04-studio-archive-picker-reuse-design.md.
(function () {
  'use strict';

  window.clipPickerCore = function () {
    return {
      // ── picker state ─────────────────────────────────────────────
      q: '', cacheF: 'any', annoF: 'any', selOnly: false,
      sel: {},                 // id -> { id, name, kind, thumb }
      offset: 0, perPage: 15, total: 0,

      // ── results page (shared /batches/picker renderer) ───────────
      async fetchPage() {
        const root = this.$root.querySelector('.nb-list');
        if (!root) return;
        if (this.selOnly) { this._renderSelected(root); return; }
        const params = new URLSearchParams({
          q: this.q, cache: this.cacheF, anno: this.annoF,
          offset: this.offset, limit: this.perPage,
        });
        try {
          const r = await fetch('/batches/picker?' + params.toString());
          if (!r.ok) {
            const d = await r.json().catch(() => ({}));
            root.innerHTML = '<div class="nb-empty">' + this._esc(d.detail || 'Catalog unavailable') + '</div>';
            this.total = 0;
            Alpine.store('toast').push('Catalog unavailable — connect to load clips.', { level: 'error' });
            return;
          }
          root.innerHTML = await r.text();
          window.htmxAlpine.reinit(root);
          const meta = root.querySelector('#nb-list-meta');
          this.total = meta ? parseInt(meta.dataset.total || '0', 10) : 0;
          this._applyChecked(root);
        } catch (e) {
          Alpine.store('toast').push('Failed to load clips: ' + e.message, { level: 'error' });
        }
      },

      resetAndFetch() { this.offset = 0; this.fetchPage(); },
      goPage(d) {
        const maxOff = Math.max(0, (Math.ceil(this.total / this.perPage) - 1) * this.perPage);
        this.offset = Math.max(0, Math.min(maxOff, this.offset + d * this.perPage));
        this.fetchPage();
      },
      pagerLabel() {
        if (!this.total) return 'No matches';
        return (this.offset + 1) + '–' + Math.min(this.offset + this.perPage, this.total) + ' of ' + this.total;
      },

      // ── selection sync (checkboxes come from the shared rows) ────
      onCheckChange(e) {
        const t = e.target;
        if (t.id === 'row-select-all') {
          this.$root.querySelectorAll('.nb-list .row-check').forEach((cb) => {
            cb.checked = t.checked;
            this._syncFromCheckbox(cb);
          });
        } else if (t.classList && t.classList.contains('row-check')) {
          this._syncFromCheckbox(t);
        }
      },
      _syncFromCheckbox(cb) {
        const id = parseInt((cb.value.split('/')[1] || ''), 10);
        if (isNaN(id)) return;
        if (cb.checked) {
          const tr = cb.closest('tr');
          this.sel[id] = {
            id,
            name: (tr && tr.querySelector('.name') ? tr.querySelector('.name').textContent.trim() : 'Clip ' + id),
            kind: (tr && tr.querySelector('.col-type') ? tr.querySelector('.col-type').textContent.trim() : ''),
            thumb: (tr && tr.querySelector('img.thumb') ? tr.querySelector('img.thumb').getAttribute('src') : '/api/media/' + id + '/thumb'),
          };
        } else {
          delete this.sel[id];
          if (this.selOnly) this.$nextTick(() => this.fetchPage());
        }
      },
      _applyChecked(root) {
        const boxes = [...root.querySelectorAll('.row-check')];
        boxes.forEach((cb) => {
          const id = parseInt((cb.value.split('/')[1] || ''), 10);
          cb.checked = !!this.sel[id];
        });
        const all = root.querySelector('#row-select-all');
        if (all) all.checked = boxes.length > 0 && boxes.every((cb) => cb.checked);
      },
      _esc(s) {
        return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
      },
      _renderSelected(root) {
        const items = this.selectedClips();
        this.total = items.length;
        if (!items.length) { root.innerHTML = '<div class="nb-empty">No clips selected.</div>'; return; }
        root.innerHTML = '<div class="nb-selbox">' + items.map((c) =>
          '<label class="nb-bchip"><input type="checkbox" class="row-check" value="catdv/' + c.id + '" checked>' +
          '<img class="thumb" src="' + this._esc(c.thumb) + '" alt="" onerror="this.classList.add(\'thumb--empty\'); this.removeAttribute(\'src\');">' +
          '<span class="nb-bname" title="' + this._esc(c.name) + '">' + this._esc(c.name) + '</span>' +
          '<span class="nb-bk col-type">' + this._esc(c.kind) + '</span></label>'
        ).join('') + '</div>';
      },

      // ── selection accessors ──────────────────────────────────────
      selCount() { return Object.keys(this.sel).length; },
      selectedClips() { return Object.values(this.sel); },
      selectedKinds() { return [...new Set(this.selectedClips().map((c) => c.kind).filter(Boolean))]; },
      removeSel(id) {
        delete this.sel[id];
        const cb = this.$root.querySelector('.nb-list .row-check[value="catdv/' + id + '"]');
        if (cb) cb.checked = false;
        if (this.selOnly) this.fetchPage();
      },
      clearSel() {
        this.sel = {};
        if (this.selOnly) this.fetchPage();
        else { const root = this.$root.querySelector('.nb-list'); if (root) this._applyChecked(root); }
      },
    };
  };
})();
