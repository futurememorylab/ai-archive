/* htmxAlpine.js — the SINGLE place that re-scans a DOM subtree so Alpine
   directives and HTMX attributes injected after page-load come alive.

   Alpine v3's MutationObserver does NOT reliably re-init subtrees that
   were swapped in by HTMX (hx-swap) or injected by a JS `fetch()` +
   `innerHTML` when the inserted nodes have no `x-data` of their own.
   `Alpine.initTree(el)` re-scans Alpine directives; `htmx.process(el)`
   re-wires hx-* attributes. Spreading those two calls across studio.js /
   studioStore.js made the lifecycle wiring impossible to reason about as
   a whole, so they all funnel through here.

   This module owns:
     - `window.htmxAlpine.reinit(el)` — initTree + process for a subtree
       a caller just injected via fetch()+innerHTML.
     - one global `htmx:afterSwap` listener for studio (set-kids
       `.selected` reconciliation + prompt-card re-init + version-state
       reconciliation into `Alpine.store('studio')`).

   This is the only non-vendor file allowed to call `Alpine.initTree(` /
   `htmx.process(` (guarded by tests/unit/test_htmx_alpine_single_lifecycle.py).
*/

window.htmxAlpine = {
  // Re-init a subtree we injected ourselves (fetch() + innerHTML). Alpine
  // re-scans directives; HTMX re-wires hx-* attributes. Both are
  // idempotent, so calling them on read-only output is harmless.
  reinit(el) {
    if (!el) {
      console.warn('htmxAlpine.reinit: null element');
      return;
    }
    window.Alpine?.initTree(el);
    window.htmx?.process(el);
  },

  // Wire ONLY HTMX on a subtree we injected via fetch()+innerHTML whose root
  // carries its OWN x-data (e.g. the compare card from openCompare). Alpine's
  // MutationObserver reliably initializes a freshly-inserted x-data root on
  // its own, so ALSO calling Alpine.initTree() here (as reinit does) binds
  // every directive on that subtree TWICE — a double-bound @click flips a
  // toggle back to its original value, so e.g. the compare Diff button looked
  // dead (the click fired twice). HTMX has no equivalent auto-init for manual
  // innerHTML, so we still process() it; htmx.process is idempotent.
  //
  // Use this for injected content that HAS its own x-data root. Use reinit()
  // for content with NO x-data root (hosted-mode directives bound to an
  // ancestor scope) or for subtrees HTMX swapped via hx-swap.
  wireHtmx(el) {
    if (!el) {
      console.warn('htmxAlpine.wireHtmx: null element');
      return;
    }
    window.htmx?.process(el);
  },
};

document.body.addEventListener('htmx:afterSwap', (evt) => {
  // Connection chip: do NOT call Alpine.initTree() on #connection-chip here.
  // The chip's innerHTML (pill trigger + dropdown panel) is hosted-mode — the
  // pill/panel have no x-data of their own, only directives (@click="toggle()",
  // x-show="open") bound to the stable container's popover() scope. Alpine's
  // MutationObserver already re-binds those directives when htmx swaps the
  // innerHTML. An explicit initTree() would bind them a SECOND time on top of
  // the observer's binding, so every click fired toggle() twice (open→close)
  // and the dropdown could never be opened. The container's x-data (and its
  // `open` flag) persists across swaps because the container is never replaced.

  const page = window.Alpine?.store('studio');
  if (!page) return;

  // When a set's clip cards swap in (hx-trigger="intersect once" on
  // .studio-set-kids), reconcile `.selected` against the live
  // focusedClipId. The server bakes a `clip_id=…` into each set's
  // hx-get URL at page-load time, so cards arrive pre-selected based on
  // the URL at that moment — but the user may have focused a different
  // clip via JS since, and the hx-get URL doesn't update. Clear the
  // server's guess, then apply the current focus.
  if (evt.target.classList?.contains('studio-set-kids')) {
    evt.target.querySelectorAll('.studio-clip-card.selected')
      .forEach(el => el.classList.remove('selected'));
    if (page.focusedClipId) {
      evt.target.querySelectorAll(`.studio-clip-card[data-clip-id="${page.focusedClipId}"]`)
        .forEach(el => el.classList.add('selected'));
    }
    // Freshly-loaded clip checkboxes render unchecked; reflect the live
    // selection so a set checked while collapsed shows its clips checked
    // once expanded. (clip-check is a plain HTMX-injected box, not x-model.)
    evt.target.querySelectorAll('.studio-clip-card[data-clip-id]').forEach(card => {
      const cb = card.querySelector('.clip-check');
      if (cb) cb.checked = page.isClipSelected(Number(card.dataset.clipId));
    });
  }

  const card = evt.target.closest('.studio-prompt-card');
  if (!card) return;
  // Alpine v3's MutationObserver doesn't reliably re-init x-data subtrees
  // swapped by HTMX hx-swap="outerHTML" — after a few cycles the card
  // comes back un-initialized, then every directive in it
  // (picker, close, diff-toggle, tab clicks) becomes a dead click.
  // Initialize the swapped subtree explicitly to keep it alive.
  window.Alpine?.initTree(card);
  const side = card.getAttribute('data-side');
  const vId  = parseInt(card.getAttribute('data-version-id'), 10);
  const vNum = parseInt(card.getAttribute('data-version-num'), 10);
  if (Number.isNaN(vId)) return;
  if (side === 'cur') {
    page.activeVersionId = vId;
    page.activeVersionNum = vNum;
    page.pendingRunSwap++;
  } else if (side === 'cmp') {
    page.compareVersionId = vId;
    page.compareVersionNum = vNum;
    page.pendingRunSwap++;
  }
  page._writeUrl();
  if (page.focusedClipId) page.refreshPlayer();
});
