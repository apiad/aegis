// An edit's hunks (transcript/describe.py) drawn two ways from the same data:
// four columns side by side, or three in one column. A container query on the
// row picks one (base.css), so rotating a phone switches without a fetch. The
// colours and the marks on changed words are computed here, so the wire
// carries only the hunks (#323).

import { colour, splitLines } from "./highlight.js";

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const MAX_WORD_TOKENS = 400; // a longer line gets no word marks: the LCS is quadratic

// A hunk's old and new texts, and for each line its index on each side.
function sides(h) {
  const olds = [];
  const news = [];
  const rows = [];
  for (const ln of h.lines) {
    const s = ln[0];
    const t = ln.slice(1);
    if (s === " ") rows.push({ s, o: olds.push(t) - 1, n: news.push(t) - 1 });
    else if (s === "-") rows.push({ s, o: olds.push(t) - 1 });
    else if (s === "+") rows.push({ s, n: news.push(t) - 1 });
  }
  return { olds, news, rows };
}

// A run of removed lines and the run of added lines after it, paired by row.
function pairs(rows) {
  const out = [];
  for (let k = 0; k < rows.length; ) {
    if (rows[k].s === " ") {
      out.push({ ctx: true, o: rows[k].o, n: rows[k].n });
      k++;
      continue;
    }
    const del = [];
    const add = [];
    while (k < rows.length && rows[k].s === "-") del.push(rows[k++].o);
    while (k < rows.length && rows[k].s === "+") add.push(rows[k++].n);
    for (let r = 0; r < Math.max(del.length, add.length); r++) out.push({ o: del[r], n: add[r] });
  }
  return out;
}

function lcs(a, b) {
  const n = a.length;
  const m = b.length;
  const L = Array.from({ length: n + 1 }, () => new Int32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const ops = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) ops.push([" ", i++, j++]);
    else if (i < n && (j >= m || L[i + 1][j] >= L[i][j + 1])) ops.push(["-", i++, null]);
    else ops.push(["+", null, j++]);
  }
  return ops;
}

// Character ranges of the tokens that differ between two paired lines.
export function wordRanges(a, b) {
  const ta = a.match(/\w+|\s+|[^\w\s]/g) || [];
  const tb = b.match(/\w+|\s+|[^\w\s]/g) || [];
  if (ta.length > MAX_WORD_TOKENS || tb.length > MAX_WORD_TOKENS) return [[], []];
  const ra = [];
  const rb = [];
  let pa = 0;
  let pb = 0;
  for (const [s, i, j] of lcs(ta, tb)) {
    if (s === " ") {
      pa += ta[i].length;
      pb += tb[j].length;
    } else if (s === "-") {
      ra.push([pa, pa + ta[i].length]);
      pa += ta[i].length;
    } else {
      rb.push([pb, pb + tb[j].length]);
      pb += tb[j].length;
    }
  }
  return [ra, rb];
}

// Wrap character ranges of a line's HTML in <mark>, closing a mark at every
// tag and reopening it after, so marks and colour spans never overlap. An
// entity is one character.
export function markRanges(html, ranges) {
  if (!ranges.length) return html;
  const inside = (p) => ranges.some(([s, e]) => p >= s && p < e);
  let out = "";
  let pos = 0;
  let open = false;
  for (const tok of html.match(/<[^>]+>|&[a-z#0-9]+;|[^<&]/g) || []) {
    if (tok[0] === "<") {
      if (open) {
        out += "</mark>";
        open = false;
      }
      out += tok;
      continue;
    }
    const want = inside(pos);
    if (want !== open) {
      out += want ? "<mark>" : "</mark>";
      open = want;
    }
    out += tok;
    pos += 1;
  }
  return out + (open ? "</mark>" : "");
}

function hunkHtml(h, hunk, cols, numbered) {
  const om = new Map();
  const nm = new Map();
  let split = "";
  for (const p of pairs(h.rows)) {
    const on = p.o !== undefined ? hunk.a + p.o : "";
    const nn = p.n !== undefined ? hunk.b + p.n : "";
    if (p.ctx) {
      split += `<span class="n">${on}</span><span class="c">${cols.olds[p.o]}</span><span class="n">${nn}</span><span class="c">${cols.news[p.n]}</span>`;
      continue;
    }
    const [ro, rn] = p.o !== undefined && p.n !== undefined ? wordRanges(h.olds[p.o], h.news[p.n]) : [[], []];
    if (p.o !== undefined) om.set(p.o, markRanges(cols.olds[p.o], ro));
    if (p.n !== undefined) nm.set(p.n, markRanges(cols.news[p.n], rn));
    split += p.o !== undefined ? `<span class="n del">${on}</span><span class="c del">${om.get(p.o)}</span>` : '<span class="n nil"></span><span class="c nil"></span>';
    split += p.n !== undefined ? `<span class="n add">${nn}</span><span class="c add">${nm.get(p.n)}</span>` : '<span class="n nil"></span><span class="c nil"></span>';
  }
  let uni = "";
  for (const r of h.rows) {
    if (r.s === " ") uni += `<span class="n">${hunk.b + r.n}</span><span class="s"> </span><span class="c">${cols.news[r.n]}</span>`;
    else if (r.s === "-") uni += `<span class="n del">${hunk.a + r.o}</span><span class="s del">−</span><span class="c del">${om.get(r.o)}</span>`;
    else uni += `<span class="n add">${hunk.b + r.n}</span><span class="s add">+</span><span class="c add">${nm.get(r.n)}</span>`;
  }
  const head = numbered === "file" ? `@@ -${hunk.a} +${hunk.b} @@` : "@@ lines of the edit @@";
  return `<div class="hunk">${esc(head)}</div><div class="split">${split}</div><div class="unified">${uni}</div>`;
}

export function diffView(diff, lang) {
  const box = document.createElement("div");
  box.className = "diff";
  const hs = diff.hunks.map(sides);
  const plain = hs.map((h) => ({ olds: h.olds.map(esc), news: h.news.map(esc) }));
  const draw = (cols) => {
    const more = diff.elided ? `<div class="elided">… ${diff.elided} more changed lines</div>` : "";
    box.innerHTML = hs.map((h, k) => hunkHtml(h, diff.hunks[k], cols[k], diff.numbered)).join("") + more;
  };
  draw(plain);
  if (lang) {
    Promise.all(
      hs.map(async (h, k) => {
        const [o, n] = await Promise.all([colour(h.olds.join("\n"), lang), colour(h.news.join("\n"), lang)]);
        return { olds: o ? splitLines(o.html) : plain[k].olds, news: n ? splitLines(n.html) : plain[k].news };
      }),
    ).then(draw, () => {});
  }
  return box;
}
