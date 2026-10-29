// The Files view's editor (#458): CodeMirror 6 behind a few calls, so nothing
// else in the client imports it. The core and each language are vendored ESM
// (scripts/vendor_codemirror/build.sh), imported the first time they are
// needed, as js/highlight.js does for highlight.js: a page that never opens a
// file downloads none of it.
//
// The buffer keeps the file's line endings: a file whose every line ends in
// \r\n is edited with \r\n as the line separator, so value() gives back the
// same bytes it was opened with when nothing changed.

// Every vendored language, by the name of its file (lang-<name>.mjs).
export const LANGS = ["cpp", "csharp", "css", "dart", "dockerfile", "go", "haskell", "html", "java", "javascript", "json", "jsx", "kotlin", "lua", "markdown", "perl", "php", "powershell", "python", "r", "ruby", "rust", "scala", "shell", "sql", "swift", "toml", "tsx", "typescript", "xml", "yaml"];
// An extension, lowercase and without the dot, to the language that edits it.
const BY_EXT = {
  py: "python", pyi: "python", pyw: "python",
  js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "jsx",
  ts: "typescript", mts: "typescript", cts: "typescript", tsx: "tsx",
  java: "java", c: "cpp", h: "cpp", cc: "cpp", cpp: "cpp", cxx: "cpp", hpp: "cpp", hh: "cpp", hxx: "cpp", ino: "cpp",
  go: "go", rs: "rust", php: "php", sql: "sql", html: "html", htm: "html", css: "css",
  md: "markdown", markdown: "markdown", json: "json", jsonc: "json", webmanifest: "json",
  yaml: "yaml", yml: "yaml", xml: "xml", svg: "xml", xsd: "xml", xsl: "xml", plist: "xml",
  cs: "csharp", kt: "kotlin", kts: "kotlin", swift: "swift", scala: "scala", sc: "scala", dart: "dart",
  rb: "ruby", rake: "ruby", gemspec: "ruby", sh: "shell", bash: "shell", zsh: "shell", ksh: "shell",
  ps1: "powershell", psm1: "powershell", r: "r", lua: "lua", pl: "perl", pm: "perl", hs: "haskell",
  toml: "toml", dockerfile: "dockerfile",
};
// Whole file names that say their language without an extension.
const BY_NAME = {
  Dockerfile: "dockerfile", Containerfile: "dockerfile", Gemfile: "ruby", Rakefile: "ruby",
  ".bashrc": "shell", ".zshrc": "shell", ".profile": "shell", ".bash_profile": "shell",
};

// A file's kind for its glyph in the Files tree and the picker (glyphs.js,
// #500): the kinds no editor language names, then the languages: Markdown,
// config, or code. A new language in BY_EXT is code without a row here.
const KIND_BY_EXT = {
  png: "image", jpg: "image", jpeg: "image", gif: "image", webp: "image", svg: "image", bmp: "image", ico: "image", avif: "image",
  pdf: "pdf",
  mp3: "audio", wav: "audio", ogg: "audio", m4a: "audio", flac: "audio", opus: "audio", aac: "audio",
  mp4: "video", webm: "video", mov: "video", mkv: "video", avi: "video",
  zip: "archive", tar: "archive", gz: "archive", tgz: "archive", bz2: "archive", xz: "archive", "7z": "archive", rar: "archive", zst: "archive",
  ini: "config", cfg: "config", conf: "config", env: "config", lock: "config",
};
const KIND_BY_LANG = { markdown: "md", json: "config", yaml: "config", toml: "config" };

export function kindOf(path) {
  const name = (path || "").split("/").pop();
  const dot = name.lastIndexOf(".");
  const kind = dot > 0 && KIND_BY_EXT[name.slice(dot + 1).toLowerCase()];
  if (kind) return kind;
  const lang = langOf(path);
  return lang ? KIND_BY_LANG[lang] || "code" : "file";
}

const FOLDER_ICON = { open: "folder-open", shut: "folder" };

// The glyph id for a Files row: a folder open or closed, or the file's kind.
export function fileGlyph(path, isDir, open = false) {
  if (isDir) return FOLDER_ICON[open ? "open" : "shut"];
  const kind = kindOf(path);
  return kind === "file" ? "file" : `f-${kind}`;
}

export function langOf(path) {
  const name = (path || "").split("/").pop();
  if (BY_NAME[name]) return BY_NAME[name];
  if (name.startsWith("Dockerfile.")) return "dockerfile";
  const dot = name.lastIndexOf(".");
  return dot > 0 ? BY_EXT[name.slice(dot + 1).toLowerCase()] || null : null;
}

// A failed import is forgotten so the next file asks again under a new URL,
// as in js/highlight.js: the browser keeps a failed module URL failed.
const failures = new Map();
function vendored(file) {
  const n = failures.get(file) || 0;
  return import(`../vendor/codemirror/${file}${n ? `?retry=${n}` : ""}`).catch((err) => {
    failures.set(file, n + 1);
    throw err;
  });
}

let core = null;
function cm() {
  core ??= vendored("core.mjs").catch((err) => {
    core = null;
    throw err;
  });
  return core;
}

const langs = new Map(); // name -> Promise of an Extension
function language(name) {
  if (!name) return Promise.resolve([]);
  if (!langs.has(name)) {
    langs.set(
      name,
      vendored(`lang-${name}.mjs`).then(
        (m) => m.lang(),
        (err) => {
          langs.delete(name);
          throw err;
        },
      ),
    );
  }
  return langs.get(name);
}

// Colours from the theme's variables, so the editor matches the transcript's
// code blocks (css/base.css, .hljs-*) in every theme.
function look(C) {
  const t = C.tags;
  const style = C.HighlightStyle.define([
    { tag: [t.comment, t.quote], color: "var(--h-com)", fontStyle: "italic" },
    { tag: [t.keyword, t.bool, t.null, t.atom, t.modifier, t.controlKeyword, t.operatorKeyword], color: "var(--h-kw)" },
    { tag: [t.string, t.regexp, t.special(t.string), t.docString], color: "var(--h-str)" },
    { tag: [t.number, t.integer, t.float, t.unit], color: "var(--h-num)" },
    { tag: [t.function(t.variableName), t.function(t.propertyName), t.heading, t.macroName], color: "var(--h-fn)" },
    { tag: [t.typeName, t.className, t.namespace, t.standard(t.variableName), t.tagName], color: "var(--h-type)" },
    { tag: [t.propertyName, t.attributeName, t.definition(t.variableName), t.labelName, t.self], color: "var(--h-var)" },
    { tag: [t.meta, t.processingInstruction, t.punctuation, t.bracket, t.angleBracket], color: "var(--h-meta)" },
    { tag: t.inserted, color: "var(--ok)" },
    { tag: t.deleted, color: "var(--err)" },
    { tag: t.emphasis, fontStyle: "italic" },
    { tag: t.strong, fontWeight: "600" },
    { tag: t.link, textDecoration: "underline" },
  ]);
  const theme = C.EditorView.theme({
    "&": { height: "100%", color: "var(--ink)", background: "var(--bg)", fontSize: "13px" },
    ".cm-scroller": { fontFamily: "var(--font-mono, monospace)", lineHeight: "1.5" },
    ".cm-content": { caretColor: "var(--accent)" },
    ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--accent)" },
    ".cm-gutters": { background: "var(--surface)", color: "var(--muted)", border: "none", borderRight: "1px solid var(--rule)" },
    ".cm-activeLine": { background: "color-mix(in srgb, var(--surface) 60%, transparent)" },
    ".cm-activeLineGutter": { background: "var(--surface)", color: "var(--ink)" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
      background: "color-mix(in srgb, var(--accent) 28%, transparent)",
    },
    ".cm-panels": { background: "var(--surface)", color: "var(--ink)" },
    ".cm-panels.cm-panels-top": { borderBottom: "1px solid var(--rule)" },
    ".cm-searchMatch": { background: "color-mix(in srgb, var(--accent) 22%, transparent)" },
    ".cm-tooltip": { background: "var(--surface)", border: "1px solid var(--rule)" },
  });
  return [theme, C.syntaxHighlighting(style)];
}

function separator(text) {
  // \r\n only when no line ends in a bare \n: a mixed file is normalised.
  return text.includes("\r\n") && !/(^|[^\r])\n/.test(text) ? "\r\n" : "\n";
}

// Open `text` in `parent`. onChange(dirty) runs when the buffer starts or stops
// differing from what was last saved; onSave() on Mod-s. `wrap` wraps long
// lines, for prose such as a memory.
export async function open(parent, { text, path, readOnly = false, wrap = false, onChange, onSave }) {
  const C = await cm();
  const lang = await language(langOf(path)).catch(() => []);
  const langSlot = new C.Compartment();
  let saved = null;
  let dirty = false;
  const extensions = (body) => [
    C.basicSetup,
    C.keymap.of([C.indentWithTab]),
    C.Prec.highest(
      C.keymap.of([{ key: "Mod-s", preventDefault: true, run: () => (onSave?.(), true) }]),
    ),
    C.EditorState.lineSeparator.of(separator(body)),
    langSlot.of(lang),
    look(C),
    C.EditorState.readOnly.of(readOnly),
    wrap ? C.EditorView.lineWrapping : [],
    C.EditorView.updateListener.of((u) => {
      if (!u.docChanged) return;
      const now = !u.state.doc.eq(saved);
      if (now !== dirty) {
        dirty = now;
        onChange?.(dirty);
      }
    }),
  ];
  const view = new C.EditorView({
    parent,
    state: C.EditorState.create({ doc: text, extensions: extensions(text) }),
  });
  saved = view.state.doc;
  // sliceDoc joins lines with the file's separator; doc.toString() always uses \n.
  const value = () => view.state.sliceDoc();
  return {
    view,
    value,
    dirty: () => dirty,
    markSaved() {
      saved = view.state.doc;
      if (dirty) {
        dirty = false;
        onChange?.(false);
      }
    },
    // Replace the buffer with the disk's text, which becomes the saved one,
    // keeping the cursor's line and the scroll position.
    setValue(next) {
      const line = view.state.doc.lineAt(view.state.selection.main.head).number;
      const top = view.scrollDOM.scrollTop;
      view.setState(C.EditorState.create({ doc: next, extensions: extensions(next) }));
      saved = view.state.doc;
      const at = view.state.doc.line(Math.min(line, view.state.doc.lines)).from;
      view.dispatch({ selection: { anchor: at } });
      view.scrollDOM.scrollTop = top;
      if (dirty) {
        dirty = false;
        onChange?.(false);
      }
    },
    goto(n) {
      const line = view.state.doc.line(Math.max(1, Math.min(n, view.state.doc.lines)));
      view.dispatch({
        selection: { anchor: line.from },
        effects: C.EditorView.scrollIntoView(line.from, { y: "center" }),
      });
      view.focus();
    },
    focus: () => view.focus(),
    destroy: () => view.destroy(),
  };
}
