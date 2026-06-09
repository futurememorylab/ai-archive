// Two-mode popover behaviour for menus / dropdowns.
// See docs/design-language.md → "Menus & popovers".
//
// Standalone mode: a leaf menu owns its own open flag.
//   <div class="popover" x-data="popover()"> … </div>
//   trigger: @click="toggle()"   panel: x-show="open"
//
// Hosted mode: a menu nested inside a larger component (studioPage,
//   bulkSel, …) must NOT declare its own x-data — that shadows the parent
//   scope (e.g. studioPage.focusedClipId reads as undefined). The ui.menu
//   macro's `state=` mode binds the same open/close/click-outside/escape
//   wiring against the parent's own flag instead, emitting no x-data.
//
// Registered in `alpine:init` (fires synchronously when the Alpine bundle
// executes), matching player.js / studio.js / promptEditor.js.
document.addEventListener("alpine:init", () => {
  Alpine.data("popover", () => ({
    open: false,
    toggle() {
      this.open = !this.open;
    },
    close() {
      this.open = false;
    },
  }));
});
