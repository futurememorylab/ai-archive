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
    refresh() { /* filled in Task 13 */ },
  }));
});

window.lineDiff = lineDiff;
