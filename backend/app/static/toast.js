/* Alpine.store('toast') — single source of user-facing error / info UI.
 *
 * Usage from any Alpine component or vanilla JS:
 *   Alpine.store('toast').push('Apply failed (503): EBUSY', {level: 'error'});
 *   Alpine.store('toast').push('Saved.', {level: 'success'});
 *   Alpine.store('toast').push('Completed before cancel landed.', {level: 'info'});
 *
 * Replaces alert(), console.error() and silent fetch .catch() patterns
 * across the codebase. See CLAUDE.md "Frontend error handling" rule.
 *
 * The store renders into <div id="toast-root"> which layout.html
 * unconditionally includes. Renders are inert if the root is missing,
 * so test fixtures that don't include layout.html don't crash.
 */
document.addEventListener('alpine:init', () => {
  Alpine.store('toast', {
    items: [],
    _nextId: 1,

    push(message, opts = {}) {
      const level = opts.level || 'info';  // 'info' | 'success' | 'error'
      const ttlMs = opts.ttlMs ?? (level === 'error' ? 8000 : 4000);
      const id = this._nextId++;
      // action: { label, fn } — rendered as a button; fn runs once, then dismiss.
      this.items.push({ id, message, level, action: opts.action || null });
      this._render();
      setTimeout(() => this.dismiss(id), ttlMs);
    },

    runAction(id) {
      const t = this.items.find(t => t.id === id);
      if (t && t.action && typeof t.action.fn === 'function') t.action.fn();
      this.dismiss(id);
    },

    dismiss(id) {
      this.items = this.items.filter(t => t.id !== id);
      this._render();
    },

    _render() {
      const root = document.getElementById('toast-root');
      if (!root) return;
      root.innerHTML = this.items.map(t => `
        <div class="toast toast-${t.level}" data-toast-id="${t.id}">
          <span class="toast-msg">${escapeHtml(t.message)}</span>
          ${t.action ? `<button class="toast-action"
                  onclick="Alpine.store('toast').runAction(${t.id})">${escapeHtml(t.action.label)}</button>` : ''}
          <button class="toast-close" aria-label="Dismiss"
                  onclick="Alpine.store('toast').dismiss(${t.id})">×</button>
        </div>
      `).join('');
    },
  });
});

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = String(s);
  return div.innerHTML;
}
