// Syntax colour for the transcript and the viewer (#323): highlight.js's core
// and one file per language, each imported the first time a block needs it.
// A phone that never meets code downloads none of it; one that meets Python
// downloads the 20 KB core and Python's 4 KB. In the transcript a block is
// coloured when it comes within 600 px of the visible part, not when it mounts.
//
// highlight.js escapes what it returns, so its HTML holds only its own spans;
// that is the only HTML from here that reaches innerHTML.

// Every vendored language, by the name of its file (tests/test_client_rules.py).
export const LANGS = ["bash", "c", "cpp", "csharp", "css", "diff", "dockerfile", "go", "graphql", "ini", "java", "javascript", "json", "kotlin", "less", "lua", "makefile", "markdown", "nginx", "objectivec", "perl", "php", "php-template", "python", "python-repl", "r", "ruby", "rust", "scss", "sql", "swift", "typescript", "vbnet", "wasm", "xml", "yaml"];
// Labels and extensions agents write, by the vendored name they mean.
const ALIAS = {
  sh: "bash", shell: "bash", zsh: "bash", console: "bash", py: "python", pyi: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript",
  yml: "yaml", toml: "ini", cfg: "ini", html: "xml", svg: "xml", md: "markdown", rs: "rust", golang: "go",
  h: "c", hpp: "cpp", cc: "cpp", "c++": "cpp", kt: "kotlin", rb: "ruby", docker: "dockerfile",
  make: "makefile", mk: "makefile", patch: "diff", jsonc: "json", jsonl: "json",
  cs: "csharp", "c#": "csharp", gql: "graphql", objc: "objectivec", pl: "perl", pm: "perl",
  vb: "vbnet", wat: "wasm", pycon: "python-repl", mm: "objectivec", "obj-c": "objectivec",
};
// Vendored grammars a grammar colours inside itself (subLanguage), by grammar.
const EMBEDS = { dockerfile: ["bash"], javascript: ["css", "graphql", "xml"], markdown: ["xml"], "php-template": ["php", "xml"], "python-repl": ["python"], typescript: ["css", "graphql", "xml"], xml: ["css", "javascript"], yaml: ["ruby"] };
// Labels that mean "do not colour, do not guess".
const PLAIN = new Set(["text", "plain", "plaintext", "txt", "output", "log"]);
// What an unlabelled block may be guessed as, besides what a labelled block asked for.
const GUESS = ["bash", "python", "json", "yaml", "diff"];
const MIN_RELEVANCE = 3; // below it a guess is a coin flip: the block stays plain
const MAX_COLOUR = 512 * 1024; // above this, text stays plain: colouring would stall the page
const CACHE_MAX = 500;

let core = null; // Promise of the hljs instance
const loaded = new Map(); // name -> Promise, once per language
// The guess candidates: GUESS and every label asked for, never a grammar loaded
// only because another embeds it (yaml's ruby would win Python classes).
const asked = new Set(GUESS);
const cache = new Map(); // `${lang}\n${text}` -> result, oldest first

export function langName(label) {
  if (!label) return null;
  const l = label.toLowerCase();
  const n = ALIAS[l] || l;
  return LANGS.includes(n) ? n : null;
}

export function langOfPath(name) {
  const lower = (name || "").toLowerCase().split("/").pop();
  if (lower === "makefile" || lower.endsWith(".mk")) return "makefile";
  if (lower === "dockerfile") return "dockerfile";
  const ext = lower.includes(".") ? lower.split(".").pop() : "";
  return langName(ext);
}

export function isPlain(label) {
  return PLAIN.has((label || "").toLowerCase());
}

// A failed import is forgotten, so the next block that needs it asks again
// (the server restarting under an open page, a phone losing the network). The
// browser keeps a module URL that failed failed for the page's life, so a
// retry asks under a new one.
const failures = new Map(); // file -> times it failed to load
function vendored(file) {
  const n = failures.get(file) || 0;
  return import(`../vendor/hljs/${file}${n ? `?retry=${n}` : ""}`).catch((err) => {
    failures.set(file, n + 1);
    throw err;
  });
}

function hljs() {
  core ??= vendored("core.min.js").then(
    (m) => m.default,
    (err) => {
      core = null;
      throw err;
    },
  );
  return core;
}

// Registers name and every grammar it embeds, each once; xml and javascript
// embed each other, so the set is closed with a loop, not by recursion.
async function load(name) {
  const lib = await hljs();
  const need = new Set([name]);
  const stack = [name];
  while (stack.length) {
    for (const e of EMBEDS[stack.pop()] || []) {
      if (!need.has(e)) {
        need.add(e);
        stack.push(e);
      }
    }
  }
  for (const n of need) {
    if (!loaded.has(n)) {
      const p = vendored(`languages/${n}.min.js`).then(
        (m) => lib.registerLanguage(n, m.default),
        (err) => {
          if (loaded.get(n) === p) loaded.delete(n);
          throw err;
        },
      );
      loaded.set(n, p);
    }
  }
  await Promise.all([...need].map((n) => loaded.get(n)));
  return lib;
}

const key = (text, lang) => `${lang || "?"}\n${text}`;

// A result already computed, so a render can draw it without a flash of plain
// text; undefined when it was never computed.
export function cachedColour(text, lang) {
  return cache.get(key(text, lang));
}

// {html, lang, guessed} for text in lang (a vendored name), or a guess when
// lang is null; null when it stays plain. Rejects, caching nothing, when a
// file it needs failed to load.
export async function colour(text, lang) {
  const k = key(text, lang);
  if (cache.has(k)) return cache.get(k);
  let out = null;
  if (text.length <= MAX_COLOUR) {
    if (lang) {
      asked.add(lang);
      const lib = await load(lang);
      out = { html: lib.highlight(text, { language: lang, ignoreIllegals: true }).value, lang, guessed: false };
    } else {
      const lib = await hljs();
      await Promise.all(GUESS.map(load));
      const r = lib.highlightAuto(text, [...asked]);
      if (r.relevance >= MIN_RELEVANCE) out = { html: r.value, lang: r.language, guessed: true };
    }
  }
  if (cache.size >= CACHE_MAX) cache.delete(cache.keys().next().value);
  cache.set(k, out);
  return out;
}

// Highlighted HTML split into lines, each with its open spans closed at the
// end and reopened on the next, so a string that spans lines keeps its colour.
export function splitLines(html) {
  const out = [];
  const open = [];
  let cur = "";
  for (const tok of html.match(/<span[^>]*>|<\/span>|[^<]+/g) || []) {
    if (tok === "</span>") {
      open.pop();
      cur += tok;
    } else if (tok.startsWith("<span")) {
      open.push(tok);
      cur += tok;
    } else {
      const parts = tok.split("\n");
      parts.forEach((p, i) => {
        if (i > 0) {
          out.push(cur + "</span>".repeat(open.length));
          cur = open.join("");
        }
        cur += p;
      });
    }
  }
  out.push(cur);
  return out;
}

function apply(block, got) {
  if (!got) return;
  const code = block.querySelector("pre > code");
  code.innerHTML = got.html;
  code.classList.add("hljs");
  if (got.guessed) {
    const tag = block.querySelector(".fh .lang");
    tag.textContent = got.lang;
    tag.classList.add("guessed");
  }
}

// Colour one fenced block (div.fence, from entries.fenceHtml) in place. A block
// still open (its closing line has not arrived) stays plain; the render after
// it closes is a new block. A block whose files fail to load stays plain.
export async function paint(block) {
  if (block.dataset.painted || block.dataset.open !== undefined) return;
  block.dataset.painted = "1";
  const label = block.dataset.lang || "";
  if (isPlain(label)) return;
  const name = langName(label);
  if (label && !name) return; // labelled but not vendored: plain, the label as written
  apply(block, await colour(block.querySelector("pre > code").textContent, name).catch(() => null));
}

let root = null;
let io = null;

// The transcript's scroller (#tr): blocks under it are coloured as they near
// its view. Called once by app.js.
export function setScroller(el) {
  root = el;
  io = null;
}

export function watch(block) {
  if (!root) {
    paint(block);
    return;
  }
  io ??= new IntersectionObserver(
    (es) => {
      for (const e of es) {
        if (!e.isIntersecting) continue;
        io.unobserve(e.target);
        paint(e.target);
      }
    },
    { root, rootMargin: "600px 0px" },
  );
  io.observe(block);
}

// A row swapped out or dropped: its blocks stop being watched, so a reply
// re-rendered by every delta leaves no stale block observed.
export function unwatch(node) {
  for (const f of node.querySelectorAll(".fence")) io?.unobserve(f);
}
