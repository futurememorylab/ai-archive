// Studio word-level inline diff (Word track-changes style).
// LCS-aligned diff over word + whitespace tokens, coalesced into runs.
// Mirror of tests/unit/test_studio_word_diff.py:word_diff — keep the two
// in sync. The Python tests are authoritative; if you change the algorithm
// here, change it there too and rerun the tests.

// Split into word + whitespace tokens, preserving everything so the text is
// reconstructable by concatenation. Empty pieces are dropped.
function tokenize(s) {
  if (!s) return [];
  return s.split(/(\s+)/).filter((t) => t !== "");
}

// Diff from aText (old) to bText (new). Returns coalesced segments:
//   {type: "eq",  text}  unchanged
//   {type: "del", text}  removed (only in old)  → red + strikethrough
//   {type: "ins", text}  added   (only in new)  → green
function wordDiff(aText, bText) {
  const A = tokenize(aText);
  const B = tokenize(bText);
  const n = A.length, m = B.length;
  const lcs = Array.from({length: n + 1}, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = A[i] === B[j]
        ? lcs[i + 1][j + 1] + 1
        : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const ops = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j])                       { ops.push(["eq",  A[i]]); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { ops.push(["del", A[i]]); i++; }
    else                                     { ops.push(["ins", B[j]]); j++; }
  }
  while (i < n) { ops.push(["del", A[i++]]); }
  while (j < m) { ops.push(["ins", B[j++]]); }
  // Coalesce adjacent ops of the same type into one segment.
  const segs = [];
  for (const [type, text] of ops) {
    const last = segs[segs.length - 1];
    if (last && last.type === type) last.text += text;
    else segs.push({type, text});
  }
  return segs;
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Render coalesced segments to a single flowing HTML string with <ins>/<del>
// wrappers. Text is escaped; the only HTML injected is our own wrappers.
function renderDiffHtml(segs) {
  return segs.map((s) => {
    const t = escapeHtml(s.text);
    if (s.type === "ins") return `<ins class="diff-ins">${t}</ins>`;
    if (s.type === "del") return `<del class="diff-del">${t}</del>`;
    return t;
  }).join("");
}

document.addEventListener("alpine:init", () => {
  if (!window.Alpine) return;
  window.Alpine.data("cmpDiff", () => ({
    html: "",
    changes: 0,
    hasContent: false,

    refresh() {
      // Shared page state lives in Alpine.store('studio'); guard in case
      // the store isn't registered yet (script-load ordering).
      const page = window.Alpine?.store("studio");
      if (!page) { this.html = ""; this.changes = 0; this.hasContent = false; return; }
      const mode = page.mode || "prompt";
      const curCard = document.querySelector('.studio-prompt-card[data-side="cur"]');
      const cmpCard = document.querySelector('.studio-prompt-card[data-side="cmp"]');
      if (!curCard || !cmpCard) { this.html = ""; this.changes = 0; this.hasContent = false; return; }

      const readText = (card) => {
        if (mode === "prompt") {
          const ta = card.querySelector("textarea.pc-editor");
          if (ta) {
            // The diff is between saved versions — use the card's last-saved
            // body (`baseline`), not the live edit buffer, so unsaved keystrokes
            // don't leak into the diff and a save (which re-baselines + bumps
            // savedTick) shows the new text. Fall back to the buffer if the
            // card's Alpine scope isn't readable.
            const data = window.Alpine?.$data(card);
            if (data && typeof data.baseline === "string") return data.baseline;
            return ta.value;
          }
          const pre = card.querySelector("pre.pc-readonly");
          return pre ? pre.textContent : "";
        }
        // mode === 'output': read raw JSON from the embedded script and
        // pretty-print it. The script lives inside the card's run-slot.
        const blk = card.querySelector('script[type="application/json"][data-run-json]');
        if (!blk) return "";
        try {
          const obj = JSON.parse(blk.textContent || "{}");
          return JSON.stringify(obj, null, 2);
        } catch {
          return blk.textContent || "";
        }
      };

      // cmp = older (baseline), cur = newer (target): show how cmp → cur, so
      // text only in cur is a green insertion and text only in cmp is a red
      // struck-through deletion.
      const segs = wordDiff(readText(cmpCard), readText(curCard));
      this.html = renderDiffHtml(segs);
      this.changes = segs.filter((s) => s.type !== "eq").length;
      this.hasContent = segs.length > 0;
    },
  }));
});

window.wordDiff = wordDiff;
