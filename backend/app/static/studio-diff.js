// Studio line-diff: LCS-aligned line diff over two strings.
// Mirror of tests/unit/test_studio_line_diff.py:line_diff — keep the
// two in sync. The Python tests are authoritative; if you change the
// algorithm here, change it there too and rerun the tests.

function lineDiff(aText, bText) {
  const A = (aText || "").split("\n");
  const B = (bText || "").split("\n");
  const n = A.length, m = B.length;
  const lcs = Array.from({length: n + 1}, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = A[i] === B[j]
        ? lcs[i + 1][j + 1] + 1
        : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out = [];
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (A[i] === B[j])                       { out.push({type: "eq",  a: A[i], b: B[j]}); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { out.push({type: "del", a: A[i]}); i++; }
    else                                     { out.push({type: "ins", b: B[j]}); j++; }
  }
  while (i < n) { out.push({type: "del", a: A[i++]}); }
  while (j < m) { out.push({type: "ins", b: B[j++]}); }
  return out;
}

// Stub Alpine component — Task 13 fills it in. Defining it now so
// loading the script before T13 doesn't break Alpine on the studio page.
document.addEventListener("alpine:init", () => {
  if (!window.Alpine) return;
  window.Alpine.data("cmpDiff", () => ({
    rows: [],

    refresh() {
      // Shared page state lives in Alpine.store('studio'); guard in case
      // the store isn't registered yet (script-load ordering).
      const page = window.Alpine?.store("studio");
      if (!page) { this.rows = []; return; }
      const mode = page.mode || "prompt";
      const curCard = document.querySelector('.studio-prompt-card[data-side="cur"]');
      const cmpCard = document.querySelector('.studio-prompt-card[data-side="cmp"]');
      if (!curCard || !cmpCard) { this.rows = []; return; }

      const readText = (card) => {
        if (mode === "prompt") {
          const ta = card.querySelector("textarea.pc-editor");
          if (ta) return ta.value;
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

      this.rows = lineDiff(readText(curCard), readText(cmpCard));
    },
  }));
});

window.lineDiff = lineDiff;
