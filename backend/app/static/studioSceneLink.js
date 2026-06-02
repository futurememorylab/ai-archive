/* Scene-compare linkage bridge (vanilla).

   The compare table rows (_studio_compare_table.html) and the timeline ranges
   (_player_overlay.html) both carry `data-scene-key`. Both are injected via
   HTMX innerHTML, where Alpine directives on directive-less subtrees don't
   reliably wire up (see studio.js window.studio shim rationale). So linkage is
   plain delegated DOM events: hovering any [data-scene-key] highlights every
   element with the same key (table row + timeline blocks), and mirrors the key
   into Alpine.store('studio').selectedSceneKey for any reactive consumers. */
(function () {
  function applyLinked(key) {
    document.querySelectorAll('[data-scene-key].is-linked')
      .forEach((el) => el.classList.remove('is-linked'));
    if (!key) return;
    document.querySelectorAll(`[data-scene-key="${CSS.escape(key)}"]`)
      .forEach((el) => el.classList.add('is-linked'));
  }

  function setKey(key) {
    applyLinked(key);
    const store = window.Alpine && window.Alpine.store('studio');
    if (store) store.selectedSceneKey = key;
  }

  document.addEventListener('mouseover', (evt) => {
    const el = evt.target.closest('[data-scene-key]');
    if (el) setKey(el.getAttribute('data-scene-key'));
  });
  document.addEventListener('mouseout', (evt) => {
    const el = evt.target.closest('[data-scene-key]');
    if (!el) return;
    // mouseout bubbles and fires on child→child moves within the same keyed
    // element; don't clear if the pointer is still inside it (avoids flicker).
    if (el.contains(evt.relatedTarget)) return;
    setKey(null);
  });

  window.studioSceneLink = { setKey, applyLinked };
})();
