// One renderer per entry kind: render(entry) -> Node.
//
// Every fact in an entry (glyph, title, summary, status, what collapses) was
// decided in Python. These functions only turn it into the markup the themes
// style; they compute nothing about tools.

import { who } from "./me.js";
import markdownit from "../vendor/markdown-it.mjs";
import { copyButton } from "./copy.js";
import { speakButton } from "./speak.js";
import { money } from "./fleet.js";
import { CARET_ICON, entryIcon, icon, iconText } from "./glyphs.js";
import { diffView } from "./diff.js";
import { cachedColour, colour, isPlain, langName, paint, splitLines } from "./highlight.js";
import { hostIcon, repoUrl, shortLabel, sureRepo } from "./refs.js";

const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

// Two renderers: the person's messages keep a typed newline (breaks), an
// agent's text follows Markdown's own rule.
const mdAgent = makeMd(false);
const mdTyped = makeMd(true);

function makeMd(breaks) {
  const m = markdownit({ html: false, linkify: true, breaks });
  const defaultLink = m.renderer.rules.link_open || ((t, i, o, e, s) => s.renderToken(t, i, o));
  m.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    tokens[idx].attrSet("target", "_blank");
    tokens[idx].attrSet("rel", "noopener noreferrer");
    tokens[idx].attrJoin("class", "web");
    // markdown-it's own link from bare text: its label is ours to shorten.
    if (tokens[idx].markup === "linkify" || tokens[idx].markup === "autolink") tokens[idx].attrJoin("class", "auto");
    return defaultLink(tokens, idx, options, env, self);
  };
  // A table sits in a block that holds its copy button, over a scroller; its
  // source lines go along for the copy (copy.js).
  m.renderer.rules.table_open = (t, i) => `<div class="tblk" data-lines="${t[i].map[0]}-${t[i].map[1]}"><div class="tw"><table>`;
  m.renderer.rules.table_close = () => "</table></div></div>";
  m.renderer.rules.fence = (t, i, o, env) => fenceHtml(t[i].content, t[i].info.trim().split(/\s+/)[0] || "", !closed(t[i], env));
  m.core.ruler.after("inline", "tasks", tasks);
  return m;
}

// A fence is closed when its last line is a closing marker; an open one runs
// to the end of the text (a reply still streaming, or one cut off). Inside a
// quote or a list that line keeps the container's ">" and indent.
function closed(tok, env) {
  const [a, z] = tok.map;
  const last = env.lines?.[z - 1];
  if (z - 1 <= a || last === undefined) return false;
  const m = /^[\s>]*(`{3,}|~{3,})\s*$/.exec(last);
  return !!m && m[1][0] === tok.markup[0] && m[1].length >= tok.markup.length;
}

// A fenced block: a header with its language, then the code. A block whose
// colour is already cached is drawn coloured at once, so a reply re-rendered
// by every delta does not flash plain; the rest is coloured by
// highlight.paint when it nears the view (transcript.js).
export function fenceHtml(text, label, open = false) {
  const name = langName(label);
  const may = !open && !isPlain(label) && (name || !label);
  const hit = may ? cachedColour(text, name) : undefined;
  const tag = hit?.guessed ? `<span class="lang guessed">${esc(hit.lang)}</span>` : `<span class="lang">${esc(label)}</span>`;
  const attrs = `data-lang="${esc(label)}"${open ? " data-open" : ""}${hit !== undefined ? ' data-painted="1"' : ""}`;
  return `<div class="fence" ${attrs}><div class="fh" data-chrome>${tag}</div><pre><code${hit ? ' class="hljs"' : ""}>${hit ? hit.html : esc(text)}</code></pre></div>`;
}

// "- [ ] x" and "- [x] x" become a disabled checkbox; ten lines, no plugin.
function tasks(state) {
  const ts = state.tokens;
  for (let i = 2; i < ts.length; i++) {
    const inl = ts[i];
    if (inl.type !== "inline" || ts[i - 1].type !== "paragraph_open" || ts[i - 2].type !== "list_item_open") continue;
    const m = /^\[([ xX])\] /.exec(inl.content);
    const first = inl.children?.[0];
    if (!m || first?.type !== "text" || !first.content.startsWith(m[0])) continue;
    first.content = first.content.slice(m[0].length);
    const box = new state.Token("html_inline", "", 0);
    box.content = `<input type="checkbox" disabled${m[1] === " " ? "" : " checked"}> `;
    inl.children.unshift(box);
    ts[i - 2].attrJoin("class", "task");
  }
}

// A table wider than its row fades at the edge it can still scroll to.
function fade(tw) {
  tw.classList.toggle("more-r", tw.scrollLeft + tw.clientWidth < tw.scrollWidth - 2);
}
export function fadeTables(root) {
  for (const tw of root.querySelectorAll(".md .tw")) fade(tw);
}

// Where a sent file's URL is fetched from: "" on this server, `/via/<server>`
// for a transcript from a linked one (web.py streams it from there).
let fileBase = "";
export function setFileBase(base) {
  fileBase = base;
}

// A horizontal swipe across `node` on a touch screen calls step(+1) for the
// next item (finger moving left) or step(-1). A swipe that began inside
// something that scrolls sideways (a wide table or code line) is that
// thing's scroll, not a page turn; a frame's touches never reach here.
export function swipe(node, step) {
  let x0 = null;
  let y0 = 0;
  node.addEventListener(
    "touchstart",
    (ev) => {
      x0 = null;
      if (ev.touches.length !== 1) return;
      for (let n = ev.target; n && n !== node; n = n.parentElement) if (n.scrollWidth > n.clientWidth + 1) return;
      x0 = ev.touches[0].clientX;
      y0 = ev.touches[0].clientY;
    },
    { passive: true },
  );
  node.addEventListener(
    "touchend",
    (ev) => {
      if (x0 === null) return;
      const t = ev.changedTouches[0];
      const dx = t.clientX - x0;
      const dy = t.clientY - y0;
      x0 = null;
      if (Math.abs(dx) >= 50 && Math.abs(dx) > 2 * Math.abs(dy)) step(dx < 0 ? 1 : -1);
    },
    { passive: true },
  );
}

// Every stored file an entry can keep: a file_send card, the person's input
// attachments, a file-tool peek, or a local path opened from a reply. The last
// two live inside their row instead of adding another transcript card.
export function entryFiles(e) {
  const detail = e?.detail || {};
  return [
    ...(detail.files || []),
    ...(detail.peek?.files || []),
    ...Object.values(detail.path_peeks || {}).flatMap((p) => p.files || []),
  ];
}

// A set of sent files as one card: a bar with the name and the actions, the
// preview below. A set pages in place (‹ ›, or a swipe on a touch screen),
// round from the last file to the first, and a preview is built only when
// its file is shown. The preview kind was decided at send (files.py); Open
// hands the set to the viewer, at the file shown.
function viaFile(f) {
  // A linked server's file: every link is rebuilt here from a path of the
  // one shape /via serves, so no URL the far server chose reaches an href
  // or a frame on this origin (a javascript: download ran with the cookie).
  const ok = typeof f.url === "string" && /^\/files\/[A-Za-z0-9_-]+\/[^/?#]+$/.test(f.url);
  f.url = ok ? fileBase + f.url : "about:blank";
  f.download = ok ? `${f.url}?download=1` : "about:blank";
  return f;
}
function fileCard(files, glyph) {
  const sent = files.map((f) => ({ ...f }));
  const remote = fileBase !== "";
  if (remote) for (const f of sent) viaFile(f);
  const card = el("div", "fcard");
  const bar = el("div", "fbar");
  const name = el("span", "fn");
  const size = el("span", "fs");
  const { acts, set } = fileActions(remote);
  const view = iconButton(el("button", "btn primary view"), "expand", "Open");
  view.addEventListener("click", () => opener({ files: sent, at, remote, glyph }));
  const dock = dockButton("");
  acts.prepend(view, ...(remote ? [] : [dock]));
  bar.append(entryIcon(glyph), name, size);
  const prev = withIcon(el("button", "btn prev"), "left");
  const next = withIcon(el("button", "btn next"), "right");
  const count = el("span", "count");
  if (sent.length > 1) {
    prev.title = "Previous file";
    next.title = "Next file";
    const pager = el("span", "pager");
    pager.append(prev, count, next);
    bar.append(pager);
  }
  bar.append(acts);
  card.append(bar);
  let at = 0;
  let stage = null;
  const show = (i) => {
    at = i;
    const f = sent[i];
    name.textContent = f.name;
    size.textContent = f.summary;
    set(f);
    // An older version docks nothing: its key's pane shows the newest.
    dock.dataset.dock = f.replaced ? "" : f.key || "";
    dock.hidden = !docker.can(dock.dataset.dock);
    count.textContent = `${i + 1} / ${sent.length}`;
    stage?.remove();
    // A replaced file, or one docked as a pane, holds a line instead of its
    // preview; syncDock() redraws it when the docking changes.
    const hd = holds(f);
    card.dataset.held = hd;
    stage =
      hd === "replaced" ? el("div", "held", `replaced by v${f.replaced}`)
      : hd === "docked" ? heldLine(f.key, DOCKED)
      : hd ? heldLine(f.key, hd)
      : preview(f, remote, () => view.click());
    if (stage) card.append(stage);
  };
  // What the shown page holds now: "replaced", "docked", "in window <n>" or "".
  const holds = (f) => {
    if (f.replaced) return "replaced";
    const at = f.key && !remote && docker.docked(f.key);
    return at === true ? "docked" : at ? inWindow(at) : "";
  };
  card._sync = () => {
    if (holds(sent[at]) !== card.dataset.held) show(at);
  };
  const step = (d) => show((at + d + sent.length) % sent.length);
  prev.addEventListener("click", () => step(-1));
  next.addEventListener("click", () => step(1));
  if (sent.length > 1) swipe(card, step);
  show(0);
  return card;
}

// The buttons a file offers on its card and in the viewer's bar: a new tab,
// the desktop app and a download, each an icon that names itself in its
// title. Open natively shows only where the server said so (#a2[data-native])
// and never for a linked server's file.
export function fileActions(remote) {
  const acts = el("span", "acts");
  const open = iconButton(el("a", "btn open"), "open", "Open in a new tab");
  open.target = "_blank";
  open.rel = "noopener noreferrer";
  const native = iconButton(el("button", "btn native"), "window", "Open in the desktop app for it");
  const dl = iconButton(el("a", "btn dl"), "latest", "Download");
  acts.append(open, ...(remote ? [] : [native]), dl);
  const set = (f) => {
    open.href = f.url;
    dl.href = f.download;
    native.dataset.fileId = f.file_id;
    native.dataset.name = f.name;
  };
  return { acts, set };
}

// What a card's Open does: app.js hands it the viewer (js/viewer.js), which
// takes {files, at, remote, glyph} for a set of files, {artifact} for a page
// or {workflow} for a run's card.
let opener = () => {};
// The browser's one Speaker (speak.js), which says whether a reply's chip
// shows as reading. Unset, as on a page without it, prose rows get no chip.
let speaker = null;
export function setSpeaker(s) {
  speaker = s;
}

export function setOpener(fn) {
  opener = fn;
}

// What an approval card needs from outside: the schedule as the schedules
// channel has it now, and a way to call an operation (app.js sets both).
let approvals = { schedule: () => null, call: async () => {} };
export function setApprovals(a) {
  approvals = a;
}

// Redraw every approval card from the current state, after the schedules
// channel changed. A card is the object as it is now, not as it was made.
export function refreshApprovals() {
  for (const c of document.querySelectorAll(".apcard")) fillApproval(c);
}

function fillApproval(card) {
  // Set when the agent made it on another server, across a trusted link.
  const where = card.dataset.server;
  const s = approvals.schedule(card.dataset.name, where);
  const bar = el("div", "fbar");
  bar.append(entryIcon(card.dataset.glyph), el("span", "fn", card.dataset.name));
  if (!s) {
    card.dataset.state = "gone";
    bar.append(el("span", "fs", where ? `on ${where}: see its Schedules view` : "deleted"));
    card.replaceChildren(bar);
    return;
  }
  const waiting = s.paused?.by === "approval";
  card.dataset.state = waiting ? "waiting" : s.paused ? "paused" : "running";
  bar.append(el("span", "fs", waiting ? "waits for your go" : s.paused ? "paused" : s.next ? `next ${s.next.slice(11, 16)}` : "finished"));
  const kv = el("dl", "apkv");
  const clock = Object.entries(s.clock).map(([k, v]) => `${k} ${v}`).join(" ");
  const fire = s.fire.target === "existing" ? `into ${s.fire.session}` : `a new ${s.fire.agent || "default"} session in ${s.fire.cwd || "."}`;
  for (const [k, v] of [["clock", clock], ["overlap", s.overlap], ["check", s.check?.bash || "none"], ["fires", fire], ["prompt", s.prompt]]) {
    kv.append(el("dt", null, k), el("dd", k === "prompt" || k === "check" ? "pre" : null, v));
  }
  const kids = [bar, kv];
  const wf = s.workflow;
  if (wf) kids.push(...scheduleWorkflow(wf));
  if (waiting) {
    const go = el("button", "btn primary", "Unpause");
    go.type = "button";
    go.onclick = async () => {
      go.disabled = true;
      try {
        // A workflow's version goes with it: the server pins what this card showed.
        await approvals.call("schedule.resume", { name: card.dataset.name, ...(wf?.sha ? { workflow: wf.sha } : {}) }, where);
      } catch (err) {
        go.disabled = false;
        go.title = String(err?.message || err);
      }
    };
    const foot = el("div", "apbar");
    foot.append(go);
    kids.push(foot);
  }
  card.replaceChildren(...kids);
}

// What a secret's card needs from outside: an operation on the shown
// session's server (app.js sets it). secret_ops.py, spec 2026-10-22.
let secretCall = async () => {};
export function setSecrets(call) {
  secretCall = call;
}
// Values revealed in this page, until it reloads or the person hides them:
// never in storage, a draft, the device store or the find index.
const revealed = new Map(); // secret id -> text
const fromB64 = (s) => Uint8Array.from(atob(s || ""), (c) => c.charCodeAt(0));
const SECRET_GONE = {
  revealed: "Already viewed. It cannot be shown again.",
  expired: "Expired before it was revealed. Ask the agent to send it again.",
  restarted: "No longer available: aegis restarted before it was revealed. Ask the agent to send it again.",
};
const lapsed = (e) => Date.parse(e.detail?.expires_at || "") < Date.now();
const until = (iso) => `expires ${new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;

function secretBar(e, title, meta) {
  const bar = el("div", "fbar");
  const acts = el("span", "acts");
  bar.append(entryIcon(e.glyph), el("span", "fn", title), el("span", "fs", meta), acts);
  return { bar, acts };
}

function fillSecret(card, e) {
  const size = `${e.detail?.bytes ?? "?"} B`;
  const value = revealed.get(e.id);
  const why = e.status === "pending" && lapsed(e) ? "expired" : e.status;
  card.classList.toggle("gone", value === undefined && why !== "pending");
  if (value !== undefined) {
    const { bar, acts } = secretBar(e, e.title || "secret", `${size} · revealed`);
    const copy = el("button", "btn primary", "Copy");
    copy.type = "button";
    copy.onclick = () => navigator.clipboard.writeText(value).then(() => (copy.textContent = "Copied"), () => (copy.textContent = "Copy failed"));
    const hide = el("button", "btn", "Hide");
    hide.type = "button";
    hide.onclick = () => {
      revealed.delete(e.id);
      fillSecret(card, { ...e, status: "revealed" });
    };
    acts.append(copy, hide);
    const shown = el("div", "sbody val", value);
    shown.append(el("span", "hint", "Shown in this tab only. Reloading or hiding it ends it."));
    card.replaceChildren(bar, shown);
    return;
  }
  if (why !== "pending") {
    const { bar } = secretBar(e, e.title || "secret", `${size} · ${why === "revealed" ? "viewed" : why}`);
    card.replaceChildren(bar, el("div", "sbody dim", SECRET_GONE[why] || SECRET_GONE.restarted));
    return;
  }
  const { bar, acts } = secretBar(e, e.title || "secret", `${size} · ${until(e.detail.expires_at)}`);
  const go = el("button", "btn primary", "Reveal");
  go.type = "button";
  go.onclick = async () => {
    go.disabled = true;
    try {
      const r = await secretCall("secret.reveal", { id: e.id });
      if (r?.pad) {
        const ct = fromB64(e.detail.ct);
        const pad = fromB64(r.pad);
        revealed.set(e.id, new TextDecoder().decode(ct.map((b, i) => b ^ pad[i])));
      }
      // The reveal's patch can redraw the row before this reply lands: fill
      // the card that is on the page now, not only the one clicked.
      const now = [...document.querySelectorAll(`.row[data-id="${CSS.escape(e.id)}"] .scard`)];
      for (const c of now.length ? now : [card]) fillSecret(c, r?.pad ? e : { ...e, status: r?.gone || "restarted" });
    } catch (err) {
      go.disabled = false;
      go.title = String(err?.message || err);
    }
  };
  acts.append(go);
  card.replaceChildren(bar, el("div", "sbody dim", "Only you can reveal this, once. The agent never saw it."));
}

function fillRequest(card, e) {
  const d = e.detail || {};
  const why = e.status === "waiting" && lapsed(e) ? "expired" : e.status;
  card.classList.toggle("gone", why !== "waiting");
  if (why === "provided") {
    const { bar } = secretBar(e, "secret requested", "provided");
    card.replaceChildren(bar, el("div", "sbody dim", `Provided: ${d.bytes} B written to ${d.path}. The agent was woken.`));
    return;
  }
  if (why !== "waiting") {
    const { bar } = secretBar(e, "secret requested", why);
    card.replaceChildren(bar, el("div", "sbody dim", "Expired before it was provided. Nothing was written."));
    return;
  }
  const { bar } = secretBar(e, "secret requested", until(d.expires_at));
  const body = el("div", "sbody");
  const form = el("form", "sform");
  const input = el("input");
  Object.assign(input, { type: "password", autocomplete: "off", placeholder: "paste the secret", required: true });
  input.setAttribute("aria-label", "secret");
  input.dataset.lpignore = "true";
  const go = el("button", "btn primary", "Provide");
  go.type = "submit";
  const hint = el("span", "hint", `Written to ${d.path} on the server, mode 0600. It does not enter the transcript.`);
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    if (!input.value) return;
    go.disabled = true;
    try {
      await secretCall("secret.provide", { id: e.id, secret: input.value });
      input.value = "";
    } catch (err) {
      go.disabled = false;
      hint.textContent = String(err?.message || err);
      hint.classList.add("err");
    }
  };
  form.append(input, go);
  body.append(el("div", "prompt", d.prompt || ""), form, hint);
  card.replaceChildren(bar, body);
}

// The Claude sign-in card (claude/login.py): how to get a setup-token, and a
// field the server checks it in. The token never enters the transcript.
function fillLogin(card, e) {
  const d = e.detail || {};
  const who = (d.handles || []).join(", ");
  card.classList.toggle("gone", e.status !== "waiting");
  if (e.status !== "waiting") {
    const { bar } = secretBar(e, "Claude sign-in renewed", "renewed");
    const done = (d.restarted || []).length ? `Restarted ${d.restarted.join(", ")}.` : "No session was waiting.";
    card.replaceChildren(bar, el("div", "sbody dim", `The new token is in ${d.path}. ${done}`));
    return;
  }
  const n = (d.handles || []).length;
  const { bar } = secretBar(e, "Claude sign-in failed", `${n} ${n === 1 ? "session" : "sessions"} waiting`);
  const body = el("div", "sbody");
  const steps = el("ol", "steps");
  for (const [before, code, after] of [
    ["On any machine with a browser, run ", "claude setup-token", "."],
    ["Approve access in the browser. If the browser shows a code, paste it back into that terminal.", "", ""],
    ["The terminal prints a line starting with ", "sk-ant-oat01-", ". Paste that line here."],
  ]) {
    const li = el("li", null, before);
    if (code) li.append(el("code", null, code), after);
    steps.append(li);
  }
  const form = el("form", "sform");
  const input = el("input");
  Object.assign(input, { type: "password", autocomplete: "off", placeholder: "sk-ant-oat01-…", required: true });
  input.setAttribute("aria-label", "Claude token");
  input.dataset.lpignore = "true";
  const go = el("button", "btn primary", "Save and check");
  go.type = "submit";
  const hint = el("span", "hint", `Waiting: ${who}. The token lasts a year and is kept in ${d.path}, mode 0600.`);
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    if (!input.value) return;
    go.disabled = true;
    hint.classList.remove("err");
    hint.textContent = "Checking with one request on haiku…";
    try {
      await secretCall("claude.token", { id: e.id, secret: input.value });
      input.value = "";
    } catch (err) {
      go.disabled = false;
      hint.textContent = String(err?.message || err);
      hint.classList.add("err");
    }
  };
  form.append(input, go);
  body.append(steps, form, hint);
  card.replaceChildren(bar, body);
}

// A config change an agent suggested (config_suggest.py): the diff, and for
// an admin Apply and Not now. The outcome comes back as the folded entry.
const CONFIG_DONE = {
  applied: "Applied. .aegis.yaml now holds this text; the agent was told.",
  declined: "Declined. Nothing was written; the agent was told.",
  stale: ".aegis.yaml changed since this was suggested. Nothing was written; the agent was told.",
};

function fillConfig(card, e) {
  const d = e.detail || {};
  card.classList.toggle("gone", e.status !== "pending");
  const bar = el("div", "fbar");
  const acts = el("span", "acts");
  bar.append(entryIcon(e.glyph), el("span", "fn", ".aegis.yaml"), el("span", "fs", e.status === "pending" ? "suggested" : e.status), acts);
  const diff = el("pre", "cdiff");
  for (const line of String(d.diff || "").split("\n")) {
    const cls = line.startsWith("+++") || line.startsWith("---") ? "h" : line.startsWith("@@") ? "at" : line[0] === "+" ? "add" : line[0] === "-" ? "del" : null;
    diff.append(el("span", cls, line + "\n"));
  }
  const kids = [bar, el("div", "sbody why", d.why || ""), diff];
  if (e.status !== "pending") {
    kids.push(el("div", "sbody dim", CONFIG_DONE[e.status] || e.status));
  } else if (who.role === "member") {
    kids.push(el("div", "sbody dim", "An admin applies this."));
  } else {
    const hint = el("span", "hint");
    const press = (op, label) => {
      const b = el("button", op === "config.suggestion.apply" ? "btn primary" : "btn", label);
      b.type = "button";
      b.onclick = async () => {
        for (const x of acts.querySelectorAll("button")) x.disabled = true;
        try {
          await secretCall(op, { id: e.id });
        } catch (err) {
          for (const x of acts.querySelectorAll("button")) x.disabled = false;
          hint.textContent = String(err?.message || err);
          hint.classList.add("err");
        }
      };
      return b;
    };
    acts.append(press("config.suggestion.apply", "Apply"), press("config.suggestion.decline", "Not now"));
    kids.push(hint);
  }
  card.replaceChildren(...kids);
}

// A memory or skill an agent saved (knowledge_ops.py): a proposal with
// Approve and Discard, or with Undo a change to an approved item or an item
// saved approved because the person asked. The card
// is a transcript record, so its buttons work after a restart; the outcome
// comes back as the folded entry.
const KNOWLEDGE_VERB = { new: "proposed", changed: "changed", deleted: "deleted", saved: "saved as you asked" };
const KNOWLEDGE_DONE = {
  approved: "Approved. It is hinted from now on; the agent was told.",
  discarded: "Discarded. It was deleted; the agent was told.",
  undone: "Undone. The change was reverted; the agent was told.",
  stale: "It changed since this card was drawn. Nothing was done; the agent was told.",
  gone: "It no longer exists. Nothing was done; the agent was told.",
  conflict: "It changed again since, so this change cannot be undone by itself. Nothing was done; the agent was told.",
};

function fileSize(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

// What /setup moved into aegis (knowledge.import): a row per item, each with
// its own Undo, which reverts that item's import commit.
const IMPORT_DONE = { undone: "undone: no longer in aegis", conflict: "changed since, not undone" };

function fillImported(card, e) {
  const d = e.detail || {};
  const items = d.items || [];
  const bar = el("div", "fbar");
  bar.append(entryIcon(e.glyph), el("span", "fn", `imported ${items.length === 1 ? "1 item" : `${items.length} items`}`), el("span", "fs", "approved"));
  const hint = el("span", "hint");
  const rows = items.map((x) => {
    const r = el("div", "krow");
    r.dataset.name = x.name;
    const what = el("span", "kwhat");
    what.append(el("b", null, x.name), el("span", "dim", ` ${x.kind} · from ${x.source}`));
    r.append(what);
    if (x.outcome) r.append(el("span", "dim", IMPORT_DONE[x.outcome] || x.outcome));
    else {
      const b = el("button", "btn", "Undo");
      b.type = "button";
      b.onclick = async () => {
        b.disabled = true;
        try {
          await secretCall("knowledge.card.unimport", { id: e.id, name: x.name });
        } catch (err) {
          b.disabled = false;
          hint.textContent = String(err?.message || err);
          hint.classList.add("err");
        }
      };
      r.append(b);
    }
    return r;
  });
  const about = el("div", "sbody dim", "Moved in from /setup, as you ticked. Undo takes one out of aegis; it does not put back an original that was removed.");
  card.replaceChildren(bar, about, ...rows, hint);
}

function fillKnowledge(card, e) {
  const d = e.detail || {};
  if (d.op === "imported") return fillImported(card, e);
  const pending = e.status === "pending";
  card.classList.toggle("gone", !pending);
  const bar = el("div", "fbar");
  const acts = el("span", "acts");
  bar.append(entryIcon(e.glyph), el("span", "fn", d.name || ""), el("span", "fs", pending ? KNOWLEDGE_VERB[d.op] || d.op : e.status), acts);
  const about = [d.op === "saved" ? `${d.item_kind} saved by the agent, as you asked` : `${d.item_kind} ${KNOWLEDGE_VERB[d.op] || d.op} by the agent`, d.type, d.lifetime === "temporary" && d.expires ? `until ${d.expires}` : d.lifetime];
  const kids = [bar, el("div", "sbody dim", about.filter(Boolean).join(" · ")), el("div", "sbody why", d.description || "")];
  if (d.body) kids.push(el("pre", "cdiff kbody", d.body));
  if (d.partial) kids.push(el("div", "sbody dim", "Shown in part: the first 4,000 characters of the text."));
  // A skill's every file, so a script that changed is not hidden behind its text.
  if (d.files?.length) kids.push(el("div", "sbody dim", "files: " + d.files.map((f) => `${f.name} (${fileSize(f.size)})`).join(" · ")));
  if (!pending) {
    kids.push(el("div", "sbody dim", KNOWLEDGE_DONE[e.status] || e.status));
  } else {
    const hint = el("span", "hint");
    const press = (op, label, primary) => {
      const b = el("button", primary ? "btn primary" : "btn", label);
      b.type = "button";
      b.onclick = async () => {
        for (const x of acts.querySelectorAll("button")) x.disabled = true;
        try {
          await secretCall(op, { id: e.id });
        } catch (err) {
          for (const x of acts.querySelectorAll("button")) x.disabled = false;
          hint.textContent = String(err?.message || err);
          hint.classList.add("err");
        }
      };
      return b;
    };
    // Edit opens the item in the Knowledge view, where a saved edit approves it.
    const edit = Object.assign(el("a", "btn", "Edit"), { href: `#knowledge=${encodeURIComponent(d.name || "")}` });
    if (d.op === "new") acts.append(press("knowledge.card.approve", "Approve", true), edit, press("knowledge.card.discard", "Discard"));
    else acts.append(press("knowledge.card.undo", "Undo", true));
    kids.push(hint);
  }
  card.replaceChildren(...kids);
}

// The workflow a schedule fires, as it is now: its steps, who saved it, and
// what changed since the version a person approved (schedules.py). The
// approval card and the Schedules view draw the same, so a Resume in either
// sends only a version the person was shown.
export function scheduleWorkflow(wf) {
  const kids = [];
  const who = wf.saved_by ? `saved by ${wf.saved_by.by.handle || wf.saved_by.by.principal || "?"} ${hhmm(wf.saved_by.t)}` : null;
  const runs = el("div", "aprun");
  runs.append("runs ", el("b", null, wf.id));
  const parts = wf.sha ? [wf.title, `v${wf.sha.slice(0, 8)}`, who] : ["deleted"];
  runs.append(...parts.filter(Boolean).map((p) => ` · ${p}`));
  if (wf.changed) runs.append(" · ", el("span", "apchanged", wf.sha ? "changed since you approved it" : "nothing to approve until the prompt names another"));
  kids.push(runs);
  if (wf.say) kids.push(el("div", "wflede", wf.say));
  if (wf.changed && wf.diff?.length) kids.push(diffPanel({}, wf.diff, null));
  else if (wf.steps?.length) {
    const list = el("ul", "apsteps");
    for (const say of wf.steps) list.append(el("li", null, say));
    kids.push(list);
  }
  // Every command it runs without asking, as the arm card lists them.
  if (wf.cmds?.length) {
    const line = runsLine({ commands: wf.commands, cwd: wf.cwd });
    if (line) kids.push(line);
    kids.push(el("pre", "apcmds", wf.cmds.join("\n")));
  }
  return kids;
}

// A workflow's card: the plan before approval, the live run after (spec §07).
// Every node shows its sentence; an agent's prompt folds under it. The run
// comes from the workflows channel; refreshWorkflows redraws the cards of
// the runs that changed (every card when not told which), in the transcript,
// the viewer and a canvas pane. The card docks as a file
// or a page does, by its entry id; while docked, its transcript card is one
// line, as a docked file's is.
// get gives a run whole, for its card; summary only what the rest reads.
let workflows = { get: () => null, summary: () => null, call: async () => {}, view: () => {} };
const openPrompts = new Set(); // "run/node" whose prompt a person opened
export function setWorkflows(w) {
  workflows = w;
}
export const workflowTitle = (run) => workflows.summary(run)?.title;
// A workflow's node while its run lives: no tab, read-only (spec §05). The
// server refuses its send, interrupt, unquiet, stop, close, configure and
// rename; this only draws that.
export const workflowLocked = (m) => !!m?.workflow && ["armed", "running"].includes(workflows.summary(m.workflow.run)?.status);
export function refreshWorkflows(runs) {
  const mine = (c) => !runs || runs.has(c.dataset.run);
  for (const c of document.querySelectorAll(".wfcard")) if (mine(c)) fillWorkflow(c);
  for (const c of document.querySelectorAll(".wfdcard")) if (mine(c)) fillDecision(c);
}

// The once-a-second count of a running card: only its times change, so only
// their text is written. A redraw would drop a selection or a focus, and give
// back a Stop already pressed.
export function tickWorkflows() {
  for (const c of document.querySelectorAll(".wfcard")) {
    const w = workflows.get(c.dataset.run);
    if (!w || w.status !== "running") continue;
    const host = hostOf(c, `wf-${w.run}`);
    const fs = (host?.querySelector(".pbar, .vbar") || c).querySelector(".fs");
    if (fs) fs.textContent = workflowStatus(w);
    const eta = c.querySelector(".wfhead .eta");
    if (eta) eta.textContent = etaLine(w);
    for (const r of w.tree) {
      if (r.state !== "running") continue;
      // The tree's row and its Now line.
      for (const dur of c.querySelectorAll(`[data-node="${CSS.escape(r.id)}"] .dur`)) dur.textContent = since(r.started, r.ended);
    }
  }
}

// Seconds as a workflow writes them: 2s, 20m, 1h30m.
function fmtSecs(s) {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = Math.round(s % 60);
  return `${h ? `${h}h` : ""}${m ? `${m}m` : ""}${r || (!h && !m) ? `${r}s` : ""}`;
}

// A probe as the card names it: how often an sh runs, or what judges.
function probeTag(p) {
  if (!p) return "";
  if (p.sh) return `sh · every ${fmtSecs(p.every)}`;
  if (p.expr) return "expr";
  return `judge · ${p.judge.agent}`;
}

// A row's kind as the card names it: who runs an agent, how a par or a map
// spreads, what a check, an if or a loop asks. A map's do, drawn once before
// its items are known, says so.
function kindTag(r) {
  const cap = r.cap ? ` · ${r.cap} at a time` : "";
  if (r.kind === "agent" && r.session) return `agent · in ${r.session}'s session`;
  if (r.kind === "agent") return r.main ? "agent · main agent" : `agent · ${r.agent}`;
  if (r.kind === "gate") return r.by === "person" ? "gate · you decide" : "gate · the main agent decides";
  if (r.kind === "map") return `for each ${r.as}${cap}`;
  if (r.kind === "par") return `at once${cap}`;
  if (r.kind === "check") return `check · ${probeTag(r.probe)}`;
  if (r.kind === "if") return `if · ${probeTag(r.probe).split(" ·")[0]}`;
  if (r.kind === "loop") return `repeat · up to ${r.max}`;
  if (r.kind === "wait") return `wait ${r.wait}`;
  return r.kind;
}

// The command or expression a row runs, never folded: a person approves the
// action itself. An sh's is rendered once it has started.
// A step's progress reading (#682) as a thin bar, the client's .bar.
function progressBar(r) {
  const bar = el("div", "bar thin");
  const fill = el("i");
  fill.style.width = `${r.percent}%`;
  bar.append(fill);
  return bar;
}

function commandOf(r) {
  if (r.kind === "sh") return `$ ${r.cmd}`;
  if (r.probe?.sh) return `$ ${r.cmd || r.probe.sh}`;
  if (r.probe?.expr) return r.probe.expr;
  return null;
}

// The commands a person approves, counted for the line above Approve, with
// how often each group runs: once, for each item of a map, each round.
function runsLine(w) {
  const { sh = 0, probes = 0, groups } = w.commands || {};
  if (!sh && !probes) return null;
  const n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  const counts = (g) =>
    [g.sh && n(g.sh, "shell command", "shell commands"), g.probes && n(g.probes, "probe", "probes")].filter(Boolean);
  const all = groups || [{ sh, probes, per: "" }];
  if (all.length === 1) {
    const g = all[0];
    return el("span", "runs", `Runs ${counts(g).join(" and ")}${g.per ? ` ${g.per}` : ""} in ${w.cwd}, without asking.`);
  }
  const parts = all.map((g) => counts(g).join(" and ") + (g.per ? ` ${g.per}` : ""));
  const list = parts.length === 2 ? parts.join(" and ") : `${parts.slice(0, -1).join(", ")}, and ${parts.at(-1)}`;
  return el("span", "runs", `Runs ${list}, in ${w.cwd}, without asking.`);
}

const COUNT_ORDER = ["done", "running", "queued", "pending", "failed", "stopped"];
function countLine(count) {
  return COUNT_ORDER.filter((k) => count[k])
    .map((k) => `${count[k]} ${k === "pending" ? "waiting" : k}`)
    .join(" · ");
}

function since(a, b) {
  if (!a) return "";
  const s = Math.max(0, Math.round((b || Date.now() / 1000) - a));
  return s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}`;
}

const WF_WORDS = { armed: "waits for your go", done: "done", failed: "failed", stopped: "stopped", rejected: "rejected", replaced: "replaced" };
const STORY_ICON = { ok: "read", bad: "close", ask: "comment", note: "pip" };

// What a run waits on, in one line under its lede: the person first.
function openLine(w) {
  const open = w.open || [];
  const mine = open.filter((o) => o.kind === "gate" && o.by === "person");
  if (mine.length) return el("div", "wfopen you", `waits on you: ${mine.map((o) => w.tree.find((r) => r.id === o.node)?.say || o.node).join(", ")}`);
  const n = (k, one, many) => {
    const c = open.filter((o) => o.kind === k).length;
    return c ? `${c} ${c === 1 ? one : many}` : null;
  };
  const parts = [n("failed", "failed step", "failed steps"), n("gate", "gate", "gates"), n("ask", "question", "questions")].filter(Boolean);
  return parts.length ? el("div", "wfopen", `waits on the main agent: ${parts.join(", ")}`) : null;
}

// Done, failed and open questions over every leaf the run will run: a map
// not started counts its steps once, and a step under a branch not taken
// does not count.
function tally(w) {
  const t = { done: 0, failed: 0, total: 0 };
  for (const r of w.tree) {
    if (!LEAF_KINDS.has(r.kind) || r.state === "skipped") continue;
    t.total++;
    if (r.state === "done") t.done++;
    else if (r.state === "failed") t.failed++;
  }
  t.asks = (w.open || []).filter((o) => o.kind === "ask").length;
  return t;
}

// The header under the card's bar: counts over a progress bar; before
// approval, how many steps the plan has.
function headLine(w) {
  const t = tally(w);
  const box = el("div", "wfhead");
  if (w.status === "armed") {
    box.append(el("span", null, `${t.total} step${t.total === 1 ? "" : "s"}`));
    return box;
  }
  const n = (k, one, many) => (k ? `${k} ${k === 1 ? one : many}` : null);
  const parts = [`${t.done} of ${t.total} done`, n(t.failed, "failed", "failed"), n(t.asks, "question", "questions")];
  box.append(el("span", null, parts.filter(Boolean).join(" · ")));
  const bar = el("div", "bar");
  const fill = el("i");
  fill.style.width = `${t.total ? Math.round((100 * t.done) / t.total) : 0}%`;
  bar.append(fill);
  box.append(bar);
  const eta = etaLine(w);
  if (eta) box.append(el("span", "eta", eta));
  return box;
}

// A step's see: the server resolves the path from the run and the step, and
// the viewer opens it at its #fragment (an HTML page's frame) or its :line.
function seeChip(w, r) {
  const chip = el("button", "see", `see ${r.see}`);
  chip.type = "button";
  chip.title = "Open it";
  chip.onclick = async () => {
    try {
      const got = await workflows.call("workflow.see", { run: w.run, node: r.id });
      const remote = fileBase !== "";
      const files = got.files
        .map((f) => (remote ? viaFile({ ...f }) : f))
        .map((f) => (got.fragment ? { ...f, url: `${f.url}#${got.fragment}` } : f));
      workflows.view(files, got.line, remote);
    } catch (err) {
      chip.title = String(err?.message || err);
      chip.classList.add("bad");
    }
  };
  return chip;
}

// A step's session, as a link: its transcript while open (read-only while the
// run holds it, with no tab added), the archive's copy once closed.
function stepLink(r) {
  if (!r.log_id) return el("span", "w", "");
  const a = el("a", "who", r.handle || "transcript");
  a.href = r.handle ? `#s=${r.log_id}` : `#read=${r.log_id}`;
  a.title = "Open its transcript";
  return a;
}

// Now: each working leaf with what it runs in and for how long, then up to
// three steps that come next, queued ones first. Only while running.
function nowSection(w) {
  if (w.status !== "running") return null;
  const leaves = w.tree.filter((r) => !r.each && LEAF_KINDS.has(r.kind));
  const working = leaves.filter((r) => r.state === "running");
  const queued = leaves.filter((r) => r.state === "queued");
  const pending = leaves.filter((r) => r.state === "pending" && !r.held);
  const next = [...queued, ...pending].slice(0, 3);
  if (!working.length && !next.length) return null;
  const box = el("div", "wfnow");
  box.append(el("h5", null, "now"));
  for (const r of working) {
    const line = el("div", "nowline");
    line.dataset.node = r.id;
    const what = r.kind === "agent" ? stepLink(r) : el("span", "w", commandOf(r) || r.reading || kindTag(r));
    line.append(el("span", "dot running"), el("span", "say", r.say), what);
    if (r.percent != null) line.append(el("span", "pct", `${r.percent}%`));
    line.append(el("span", "dur", since(r.started, r.ended)));
    if (r.percent != null) line.append(progressBar(r));
    box.append(line);
  }
  for (const r of next) {
    const line = el("div", "nowline next");
    line.dataset.node = r.id;
    line.append(el("span", `dot ${r.state}`), el("span", "say", r.say), el("span", "w", r.state === "queued" ? "waits for room" : "next"));
    box.append(line);
  }
  return box;
}

// Folding (spec §07): a map of more than FOLD items shows its count line
// until a person opens it, and a loop past FOLD rounds shows only its
// current round. A person's choice is kept per run and node.
const FOLD = 6;
const openMaps = new Set(); // "run/node" a person unfolded
const itemCount = (r) => Object.values(r.count || {}).reduce((a, b) => a + b, 0);
const foldable = (r) => (r.kind === "map" && itemCount(r) > FOLD) || (r.kind === "loop" && r.rounds?.round > FOLD);
// What a fold never hides: a failed step, one held by a change, a gate
// waiting for the person.
const needsSomeone = (r) => r.state === "failed" || r.held || (r.kind === "gate" && r.by === "person" && r.state === "running");

// The rows each folded map or loop hides, by instance prefix: a map's items
// whole, a loop's rounds but the current one.
function folds(w) {
  const out = [];
  for (const r of w.tree) {
    if (!foldable(r) || openMaps.has(`${w.run}/${r.id}`)) continue;
    out.push({ under: `${r.id}.`, keep: r.kind === "loop" ? `${r.id}.${r.rounds.round}.` : null, row: r });
  }
  return out;
}
const hidden = (fs, r) => !needsSomeone(r) && fs.some((f) => r.id.startsWith(f.under) && !(f.keep && r.id.startsWith(f.keep)));

// A folded map's or loop's toggle, beside its count line.
function foldButton(w, r) {
  const key = `${w.run}/${r.id}`;
  const open = openMaps.has(key);
  const n = r.kind === "map" ? itemCount(r) : r.rounds.round - 1;
  const label = open ? "fold" : r.kind === "map" ? `show all ${n}` : `${n} earlier round${n === 1 ? "" : "s"}`;
  const b = el("button", "fold", label);
  b.type = "button";
  b.onclick = () => {
    open ? openMaps.delete(key) : openMaps.add(key);
    refreshWorkflows();
  };
  return b;
}

// A folded map's or loop's story folds with it: the lines of the steps it
// hides become one line, where the last of them was, with the same toggle as
// the tree. A failure, a question and a note never fold.
function storyLines(w, fs) {
  const hider = (s) => s.mark === "ok" && s.node && fs.find((f) => s.node.startsWith(f.under) && !(f.keep && s.node.startsWith(f.keep)));
  const count = new Map();
  const last = new Map();
  w.story.forEach((s, i) => {
    const f = hider(s);
    if (f) count.set(f, (count.get(f) || 0) + 1), last.set(f, i);
  });
  const out = [];
  w.story.forEach((s, i) => {
    const f = hider(s);
    if (!f) out.push(storyLine(s.t, s.mark, icon(STORY_ICON[s.mark] || "comment"), s.text));
    else if (last.get(f) === i) {
      const line = storyLine(s.t, "ok", "…", `${count.get(f)} lines folded · ${f.row.say}`);
      line.classList.add("folded");
      line.append(foldButton(w, f.row));
      out.push(line);
    }
  });
  return out;
}

function storyLine(t, mark, mk, text) {
  const line = el("div", "line");
  line.dataset.mark = mark;
  const m = el("span", "mk");
  m.append(mk);
  line.append(el("span", "tm", hhmm(t)), m, el("span", null, text));
  return line;
}

// Where a run's card is held: "docked", "in window <n>" or "".
function docked(id) {
  const at = docker.docked(id);
  return at === true ? "docked" : at ? inWindow(at) : "";
}

// A workflow's status line, for the card's bar and a pane's.
export function workflowStatus(w) {
  return w.status === "running" ? `running ${since(w.started)}` : WF_WORDS[w.status] || w.status;
}

// Where a card is drawn: a canvas pane, the viewer, or the transcript (null).
function hostOf(card, id) {
  return card.closest(`.pane[data-pane="${id}"]`) || card.closest("#viewer");
}

// A run's input (#712): a short scalar on one line; anything longer folded to
// its name and a preview until a person opens it, then a list or an object
// (or a string holding one) as coloured JSON, and other text with its line
// breaks. A person's choice is kept per run and input.
const openArgs = new Set(); // "run/name" a person unfolded
const ARG_INLINE = 60;
function argRow(run, k, v) {
  let data = v !== null && typeof v === "object" ? v : null;
  if (typeof v === "string" && /^\s*[[{]/.test(v)) {
    try {
      data = JSON.parse(v);
    } catch {
      data = null;
    }
  }
  const text = typeof v === "string" ? v : JSON.stringify(v);
  if (data === null && (text.length <= ARG_INLINE && !text.includes("\n"))) {
    const row = el("div", "wfarg inline");
    row.append(el("span", "k", k), el("span", "v", text));
    return row;
  }
  const preview = Array.isArray(data)
    ? `${data.length} item${data.length === 1 ? "" : "s"}`
    : data
      ? `{ ${Object.keys(data).join(", ")} }`
      : text.replace(/\s+/g, " ").trim();
  const key = `${run}/${k}`;
  const row = el("details", "wfarg");
  row.open = openArgs.has(key);
  const sum = el("summary");
  const caret = () => icon(CARET_ICON[row.open ? "open" : "shut"]);
  sum.append(caret(), el("span", "k", k), el("span", "v", preview));
  row.append(sum);
  let body;
  if (data !== null) {
    body = el("div", "md"); // the transcript's fence styles
    body.innerHTML = fenceHtml(JSON.stringify(data, null, 2), "json");
  } else body = el("div", "text", text);
  row.append(body);
  const fence = body.querySelector(".fence");
  const show = () => fence && paint(fence);
  if (row.open) show();
  row.addEventListener("toggle", () => {
    row.open ? openArgs.add(key) : openArgs.delete(key);
    sum.firstElementChild.replaceWith(caret());
    if (row.open) show();
  });
  return row;
}

// The run's clock and cost (#712), under its progress bar: time elapsed, then
// while running an estimate of what is left from the pace so far (finished
// steps over wall time, so parallel steps count), and what its steps' sessions
// have cost, with an estimated total from the cost of each finished step.
function etaLine(w) {
  if (!w.started) return "";
  const t = tally(w);
  const end = w.ended || Date.now() / 1000;
  const took = end - w.started;
  const parts = [`${since(w.started, end)} elapsed`];
  const live = w.status === "running" && t.done > 0 && t.done < t.total;
  if (live) parts.push(`~${fmtSecs(Math.round(((took / t.done) * (t.total - t.done)) / 60) * 60 || 60)} left`);
  const rows = w.tree.filter((r) => !r.each && LEAF_KINDS.has(r.kind));
  const spent = rows.reduce((a, r) => a + (r.cost || 0), 0);
  if (rows.some((r) => r.cost != null)) {
    parts.push(`${money(spent)} spent`);
    const doneCost = rows.filter((r) => r.state === "done").reduce((a, r) => a + (r.cost || 0), 0);
    if (live) parts.push(`~${money(Math.max(spent, (doneCost / t.done) * t.total))} total`);
  }
  return parts.join(" · ");
}

export function fillWorkflow(card) {
  const w = workflows.get(card.dataset.run);
  const id = `wf-${card.dataset.run}`;
  const host = hostOf(card, id);
  if (!w) {
    card.replaceChildren(el("div", "fbar", "workflow not found"));
    return;
  }
  card.dataset.status = w.status;
  card.dataset.held = docked(id);
  const title = [entryIcon("pip"), el("span", "fn", w.title), el("span", "fs", workflowStatus(w))];
  if (w.status === "replaced" && !host) {
    // Armed again before approval: the newer card under a later call is the one.
    const v = workflows.summary(w.replaced_by)?.version || w.version + 1;
    const head = el("div", "fbar");
    head.append(...title);
    card.replaceChildren(head, el("div", "wflede", `replaced by v${v}`));
    return;
  }
  const kids = [];
  if (host) {
    // A pane's bar is the canvas's (canvas.js draw), and the viewer's is the
    // viewer's: the title goes before their buttons, and the card has no bar
    // of its own.
    const bar = host.querySelector(".pbar, .vbar");
    bar?.replaceChildren(...title, ...[...(bar?.querySelectorAll(".acts") || [])]);
  } else {
    const head = el("div", "fbar");
    const view = iconButton(el("button", "btn primary view"), "expand", "Open");
    view.type = "button";
    view.addEventListener("click", () => opener({ workflow: card.dataset.run }));
    head.append(...title, view, dockButton(id));
    const at = docker.docked(id);
    if (at) {
      card.replaceChildren(head, heldLine(id, at === true ? DOCKED : inWindow(at)));
      return;
    }
    kids.push(head);
  }
  // Who launched it, and the args the agent made of their words: a launched
  // run is running when it is first drawn, so this is what the person reads
  // instead of an Approve.
  if (w.launch) {
    const how = w.launch.by === "schedule" ? `Started by schedule ${w.launch.name}, run ${w.launch.run}` : `Started by your ${(w.launch.line || "/workflow").split(" ")[0]} line`;
    kids.push(el("div", "wfwho", w.launch.by !== "schedule" && w.launch.typed ? `${how}: ${w.launch.typed}` : how));
  }
  const args = Object.entries(w.args || {});
  if (args.length) {
    const box = el("div", "wfargs");
    for (const [k, v] of args) box.append(argRow(w.run, k, v));
    kids.push(box);
  }
  kids.push(headLine(w), el("div", "wflede", w.say));
  const waits = w.status === "running" ? openLine(w) : null;
  if (waits) kids.push(waits);
  const now = nowSection(w);
  if (now) kids.push(now);
  const fs = folds(w);
  if (w.story.length) {
    const story = el("div", "story");
    story.append(...storyLines(w, fs));
    kids.push(story);
  }
  const tree = el("div", "wftree");
  for (const r of w.tree) {
    if (hidden(fs, r)) continue;
    const row = el("div", "wfrow");
    row.dataset.node = r.id;
    row.dataset.state = r.state;
    row.style.paddingLeft = `${12 + r.depth * 16}px`;
    if (r.each) row.classList.add("each");
    const main = el("div", "wfb");
    main.append(el("div", "say", r.say));
    const tags = el("div", "tags");
    tags.append(el("span", "kind", kindTag(r)));
    if (r.branch) tags.append(el("span", "branch", r.branch));
    if (r.log_id) tags.append(stepLink(r));
    for (const a of r.after || []) tags.append(el("span", `after${a.met ? " met" : ""}`, `after ${a.name}`));
    if (r.deadline) {
      const d = el("span", "deadline");
      d.append(...iconText("clock", fmtSecs(r.deadline)));
      tags.append(d);
    }
    if (r.retries) tags.append(el("span", "retries", `retries ${r.retries}`));
    if (r.attempts) tags.append(el("span", "attempt", `attempt ${r.attempts + 1} of ${(r.retries || 0) + 1}`));
    if (r.held) tags.append(el("span", "held", "waits for the change"));
    const key = `${w.run}/${r.id}`;
    const prompt = r.prompt || r.probe?.judge?.prompt;
    if (prompt) {
      const tog = el("button", "tog");
      tog.append(...iconText(CARET_ICON[openPrompts.has(key) ? "open" : "shut"], "prompt"));
      tog.type = "button";
      tog.onclick = () => {
        openPrompts.has(key) ? openPrompts.delete(key) : openPrompts.add(key);
        refreshWorkflows();
      };
      tags.append(tog);
    }
    if (r.see) tags.append(seeChip(w, r));
    main.append(tags);
    const cmd = commandOf(r);
    if (cmd) main.append(el("pre", "cmd", cmd));
    if (r.progress) main.append(el("pre", "cmd progress", `progress $ ${r.progress}`));
    if (prompt && openPrompts.has(key)) main.append(el("pre", null, prompt));
    if (r.reading || r.percent != null) {
      const out = el("div", "out", [r.reading, r.percent != null && `${r.percent}%`].filter(Boolean).join(" · "));
      if (r.percent != null) out.append(progressBar(r));
      main.append(out);
    }
    const counted = r.rounds ? el("div", "out", `round ${r.rounds.round} of ${r.rounds.max}`) : r.count ? el("div", "out", countLine(r.count)) : null;
    if (counted) {
      if (foldable(r)) counted.append(" · ", foldButton(w, r));
      main.append(counted);
    }
    if (r.kind === "gate" && !r.each && r.state !== "pending") main.append(gatePanel(w, r));
    else if (r.summary || r.error) main.append(el("div", `out ${r.error ? "bad" : ""}`, r.error || r.summary));
    const waiting = r.state === "pending" || r.state === "queued" || r.state === "skipped";
    row.append(el("span", `dot ${r.state}`), main, el("span", "dur", waiting ? "" : since(r.started, r.ended)));
    tree.append(row);
  }
  kids.push(tree);
  if (w.proposal && w.edits?.length) kids.push(editPanel(w, w.edits.at(-1)));
  const foot = el("div", "apbar");
  const act = (label, op, cls = "") => {
    const b = el("button", `btn ${cls}`.trim(), label);
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await workflows.call(op, { run: w.run });
      } catch (err) {
        b.disabled = false;
        b.title = String(err?.message || err);
      }
    };
    foot.append(b);
  };
  if (w.status === "armed") {
    // Armed again with a new definition: this card approves too, so it shows
    // the diff the again card shows, never an approval past what changed.
    const again = w.story.findLast((s) => s.text.startsWith("Armed again"));
    if (again?.diff?.length) kids.push(diffPanel(w, again.diff, again.commands));
    const runs = runsLine(w);
    if (runs) foot.append(runs);
    act("Approve and run", "workflow.approve", "primary");
    act("Reject", "workflow.reject");
  } else if (w.status === "running") act("Stop", "workflow.stop", "danger");
  if (foot.children.length) kids.push(foot);
  const refocus = keepDraft(card);
  card.replaceChildren(...kids);
  refocus();
}

// What a person typed at a gate, by run and node: a card is redrawn on every
// change of its run, and the answer box must keep its text and its focus.
const gateDrafts = new Map();
function keepDraft(card) {
  const f = card.contains(document.activeElement) ? document.activeElement : null;
  const key = f?.dataset?.draft;
  if (!key) return () => {};
  const at = [f.selectionStart, f.selectionEnd];
  return () => {
    const box = card.querySelector(`textarea[data-draft="${CSS.escape(key)}"]`);
    box?.focus();
    box?.setSelectionRange(...at);
  };
}

// A gate's panel: the question, the values the decider sees, and for an
// open person gate an answer box with Continue, Stop and Auto (workflow.gate
// and workflow.gate.delegate, operations no agent reaches). Auto hands this
// gate, and every later opening of the same step, to the main agent.
// Answered, one line says who and what.
function gatePanel(w, r) {
  const box = el("div", "gate");
  box.append(el("div", "q", r.ask || r.say));
  for (const [k, v] of Object.entries(r.shown || {})) box.append(el("pre", null, `${k}: ${JSON.stringify(v)}`));
  if (r.state !== "running" || r.by !== "person" || w.status !== "running") {
    if (r.summary) box.append(el("div", "out", r.summary.replace("by the person", "by you")));
    else if (r.error) box.append(el("div", "out bad", r.error));
    else if (r.state === "stopped") box.append(el("div", "out bad", "Stopped there."));
    else if (r.by === "main" && r.state === "running") box.append(el("div", "out", r.delegated ? "Handed to the main agent: it answers this one and each later one." : "Waiting for the main agent."));
    else if (r.delegated) box.append(el("div", "out", "Handed to the main agent."));
    return box;
  }
  const key = `${w.run}/${r.id}`;
  const answer = el("textarea");
  answer.rows = 2;
  answer.placeholder = "answer (optional): later steps read it";
  answer.value = gateDrafts.get(key) || "";
  answer.oninput = () => gateDrafts.set(key, answer.value);
  answer.dataset.draft = key;
  const go = (yes, b) => async () => {
    b.disabled = true;
    try {
      await workflows.call("workflow.gate", { run: w.run, node: r.id, go: yes, answer: answer.value });
      gateDrafts.delete(key);
    } catch (err) {
      b.disabled = false;
      b.title = String(err?.message || err);
    }
  };
  const btns = el("div", "btns");
  const ok = el("button", "btn primary", "Continue");
  const no = el("button", "btn danger", "Stop");
  const auto = el("button", "btn", "Auto");
  ok.type = no.type = auto.type = "button";
  ok.onclick = go(true, ok);
  no.onclick = go(false, no);
  auto.title = "Hand this gate to the main agent, now and each later time this step asks";
  auto.onclick = async () => {
    auto.disabled = true;
    try {
      await workflows.call("workflow.gate.delegate", { run: w.run, node: r.id });
    } catch (err) {
      auto.disabled = false;
      auto.title = String(err?.message || err);
    }
  };
  btns.append(ok, no, auto);
  box.append(answer, btns);
  return box;
}

// A change in the steps' sentences and the commands it adds: an edit's,
// or a new definition given when arming again.
const DIFF_OP = { add: "+", remove: "−", change: "~", move: "→" };
function diffPanel(w, diff, commands) {
  const box = el("div", "edit");
  for (const d of diff) {
    const line = el("div", `d ${d.op}`);
    const what = d.key === "$" ? `The workflow's ${d.fields.join(", ").replaceAll("its ", "")}` : d.say;
    const text = el("span", "w", what);
    if (d.was) text.title = `was: ${d.was}`;
    line.append(el("span", "op", DIFF_OP[d.op]), text);
    if (d.fields?.length && d.key !== "$") line.append(el("span", "f", d.fields.join(", ")));
    if (d.cmd) line.append(el("pre", "cmd", `$ ${d.cmd}`));
    box.append(line);
  }
  const c = commands || {};
  const n = (k, one) => (k ? `${k} ${one}${k > 1 ? "s" : ""}` : null);
  if (c.sh || c.probes) box.append(el("div", "runs", `Adds ${[n(c.sh, "shell command"), n(c.probes, "probe")].filter(Boolean).join(" and ")}, run in ${w.cwd} without asking. Steps that ran keep their results.`));
  return box;
}

// An edit's panel: its diff, and Apply change and Keep the plan while it
// is the open proposal.
function editPanel(w, e) {
  const box = diffPanel(w, e.diff, e.commands || w.proposal?.commands);
  if (e.status !== "proposed" || w.status !== "running") {
    box.append(el("div", "out", e.status === "replaced" ? "replaced by a later change" : e.status === "proposed" ? `not applied: the run is ${WF_WORDS[w.status] || w.status}` : e.status));
    return box;
  }
  const btns = el("div", "btns");
  for (const [label, op, cls] of [["Apply change", "workflow.edit.apply", "primary"], ["Keep the plan", "workflow.edit.reject", ""]]) {
    const b = el("button", `btn ${cls}`.trim(), label);
    b.type = "button";
    b.onclick = async () => {
      b.disabled = true;
      try {
        await workflows.call(op, { run: w.run, v: e.v });
      } catch (err) {
        b.disabled = false;
        b.title = String(err?.message || err);
      }
    };
    btns.append(b);
  }
  box.append(btns);
  return box;
}

const LEAF_KINDS = new Set(["agent", "sh", "wait", "check", "gate"]);

// A card for what a run waits on a person for, under the call that asked:
// a gate, an edit, or arming again. Filled from the workflows channel, as
// the run's own card is; the answer box keeps its text across redraws.
function fillDecision(card) {
  const w = workflows.get(card.dataset.run);
  if (!w) {
    card.replaceChildren(el("div", "fbar", "workflow not found"));
    return;
  }
  const { what, key } = card.dataset;
  const refocus = keepDraft(card);
  let panel = null;
  let state = "";
  if (what === "gate") {
    // key is <instance>@<attempt>: a card of an earlier attempt is history.
    const [id, at] = key.split("@");
    const r = w.tree.find((x) => x.id === id);
    if (r && Number(at || 0) !== (r.attempts || 0)) {
      panel = el("div", "gate");
      panel.append(el("div", "q", r.ask || r.say), el("div", "out", "Asked again below."));
      state = "asked again";
    } else {
      if (r) panel = gatePanel(w, r);
      const open = r && r.state === "running" && r.by === "person" && w.status === "running";
      state = open ? "waits for you" : r?.state === "done" ? "continued" : r?.state === "stopped" ? "stopped" : r?.delegated && r.state === "running" ? "handed to the main agent" : WF_WORDS[w.status] || w.status;
    }
  } else if (what === "edit") {
    const e = (w.edits || []).find((x) => String(x.v) === key);
    if (e) panel = editPanel(w, e);
    state = e?.status === "proposed" && w.status === "running" ? "waits for you" : e?.status || "";
  } else if (what === "again") {
    const agains = w.story.map((s, i) => [s, i]).filter(([s]) => s.text.startsWith("Armed again"));
    const current = agains.at(-1)?.[1] === Number(key);
    const leaves = w.tree.filter((r) => LEAF_KINDS.has(r.kind) && !r.each);
    const kept = leaves.filter((r) => r.state === "done" || r.state === "skipped").length;
    panel = el("div", "again");
    panel.append(el("div", "q", `Run again: ${kept} done step${kept === 1 ? "" : "s"} kept, ${leaves.length - kept} to run`));
    const line = w.story[Number(key)];
    if (line?.diff?.length) panel.append(diffPanel(w, line.diff, line.commands));
    if (current && w.status === "armed") {
      state = "waits for you";
      const btns = el("div", "btns");
      for (const [label, op, cls] of [["Approve and run", "workflow.approve", "primary"], ["Reject", "workflow.reject", ""]]) {
        const b = el("button", `btn ${cls}`.trim(), label);
        b.type = "button";
        b.onclick = async () => {
          b.disabled = true;
          try {
            await workflows.call(op, { run: w.run });
          } catch (err) {
            b.disabled = false;
            b.title = String(err?.message || err);
          }
        };
        btns.append(b);
      }
      panel.append(btns);
    } else {
      state = current ? (w.status === "rejected" ? "rejected" : "approved") : "armed again later";
      panel.append(el("div", "out", state));
    }
  }
  card.dataset.state = state === "waits for you" ? "waiting" : "answered";
  const head = el("div", "fbar");
  head.append(entryIcon("pip"), el("span", "fn", w.title), el("span", "fs", state));
  card.replaceChildren(head, ...(panel ? [panel] : []));
  refocus();
}

// What Dock does, on a card and on the viewer's bar: app.js hands it the
// canvas (js/canvas.js), so this module does not import it. can(id) says
// whether the button shows: not while the item is docked, not for a file
// sent before files had keys, and not on a session the person may not change.
// docked(id) is true when a pane shows it here, the window's number when a
// pane in another window does, and false otherwise. show(id) outlines that
// pane (a docked card's Show), open(n) focuses or opens window n's tab, and
// pop(id) moves a page to a window of its own; canPop(id) says whether Pop
// out shows (not on a phone, which has one window). frames() is false in a
// window tab: its transcript is never shown, and a page whose card mounted a
// frame there would run in window 1 and in that tab at once.
let docker = {
  dock: () => {},
  can: () => false,
  docked: () => false,
  show: () => {},
  open: () => {},
  pop: () => {},
  canPop: () => false,
  frames: () => true,
};
export function setDocker(d) {
  docker = { ...d, can: (id) => !!id && d.can(id) };
}
export function dockButton(id) {
  const b = iconButton(el("button", "btn dock"), "dock", "Dock beside the transcript");
  b.type = "button";
  b.dataset.dock = id;
  b.hidden = !docker.can(id);
  b.addEventListener("click", () => docker.dock(b.dataset.dock));
  return b;
}
// Pop out, on a live page's card and pane: it takes the place of Open in a
// new tab, which ran the page with no bridge behind it.
export function popButton(id) {
  const b = iconButton(el("button", "btn popout"), "open", "Pop out into a browser window of its own");
  b.type = "button";
  b.dataset.pop = id;
  b.hidden = !docker.canPop(id);
  b.addEventListener("click", () => docker.pop(b.dataset.pop));
  return b;
}
// The canvas or who may change it changed: every Dock and Pop out button
// drawn shows or hides, and a file card whose page was docked or undocked
// redraws its stage in place (an artifact's card follows hold() instead).
export function syncDock() {
  for (const b of document.querySelectorAll("#a2 .btn.dock[data-dock]")) b.hidden = !docker.can(b.dataset.dock);
  for (const b of document.querySelectorAll("#a2 .btn.popout[data-pop]")) b.hidden = !docker.canPop(b.dataset.pop);
  for (const card of document.querySelectorAll("#entries .fcard:not(.acard)")) card._sync?.();
}

// The line a held card shows instead of its stage. A docked one carries Show,
// which outlines its pane; one in another browser window carries Open, which
// focuses that window's tab or opens it.
export const DOCKED = "docked";
export const inWindow = (n) => `in window ${n}`;
function heldLine(id, label) {
  const line = el("div", "held", label);
  const away = /^in window (\d+)$/.exec(label);
  if (label === DOCKED || away) {
    const b = el("button", "btn link locate", label === DOCKED ? "show" : "open");
    b.type = "button";
    b.title = label === DOCKED ? "Outline its pane" : `Open window ${away[1]}`;
    b.addEventListener("click", () => (away ? docker.open(Number(away[1])) : docker.show(id)));
    line.append(" · ", b);
  }
  return line;
}

// One sent file's preview, or null when the browser cannot draw one. A click
// on an image calls zoom, when there is one.
export function preview(f, remote, zoom) {
  const pv = f.preview;
  let view = null;
  if (pv === "image") {
    view = el("img");
    view.src = f.url;
    view.alt = f.name;
    view.loading = "lazy";
    if (zoom) view.addEventListener("click", zoom);
  } else if (pv === "pdf" || pv === "html") {
    view = el("iframe");
    // A linked server's frame is sandboxed unless it is a PDF, which /via
    // serves as application/pdf whatever the far server says.
    if (pv === "html" || (remote && !/\.pdf$/i.test(f.url))) view.setAttribute("sandbox", "allow-scripts");
    view.loading = "lazy";
    view.src = f.url;
    view.title = f.name;
  } else if (pv === "markdown") {
    view = markdown(f.excerpt);
    view.classList.add("excerpt");
  } else if (pv === "text") {
    view = el("pre", "excerpt", f.excerpt);
  } else if (pv === "audio" || pv === "video") {
    view = el(pv);
    view.controls = true;
    view.preload = "metadata";
    view.src = f.url;
  }
  if (!view) return null;
  const stage = el("div", `stage ${pv}`);
  stage.append(view);
  return stage;
}

export function markdown(text, { breaks = false, paths = null } = {}) {
  const div = document.createElement("div");
  div.className = "md";
  const src = text || "";
  div._src = src; // what a table's copy slices its lines from (copy.js)
  div.innerHTML = (breaks ? mdTyped : mdAgent).render(src, { lines: src.split("\n") });
  for (const fh of div.querySelectorAll(".fence > .fh")) fh.append(copyButton("code"));
  for (const pre of div.querySelectorAll("pre")) if (!pre.closest(".fence")) pre.append(copyButton("code"));
  for (const blk of div.querySelectorAll(".tblk")) {
    blk.prepend(copyButton("table"));
    const heads = [...blk.querySelectorAll("thead th")].map((th) => th.textContent);
    for (const tr of blk.querySelectorAll("tbody tr")) {
      [...tr.children].forEach((td, i) => {
        td.dataset.label = heads[i] ?? "";
      });
    }
    const tw = blk.querySelector(".tw");
    tw.addEventListener("scroll", () => fade(tw), { passive: true });
  }
  for (const a of div.querySelectorAll("a.web")) decorateLink(a);
  // The spans a reply listed as paths (detail.paths): an issue ref or a
  // github.com path links at once; on this server the rest wait for
  // path.stat (transcript.js, applyKinds). A linked server's open nothing,
  // as the files are on the other machine. A span inside a link stays the
  // agent's link.
  for (const code of div.querySelectorAll(":not(pre) > code")) {
    if (code.closest("a")) continue;
    const t = code.textContent.trim();
    if (sureRepo(t)) {
      asRepo(code, t);
      continue;
    }
    const i = fileBase === "" && paths ? paths.indexOf(t) : -1;
    if (i < 0) continue;
    code.classList.add("ref");
    code.dataset.index = String(i);
    code.title = "Open in the viewer";
  }
  return div;
}

// A URL gets its host's icon; one markdown-it made from bare text also gets
// a short label, the full URL kept as its title.
function decorateLink(a) {
  let u;
  try {
    u = new URL(a.getAttribute("href"));
  } catch {
    return;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return;
  if (a.classList.contains("auto")) {
    a.title = a.getAttribute("href");
    a.textContent = shortLabel(u);
  }
  a.prepend(icon(hostIcon(u)));
}

function asRepo(code, text) {
  const a = document.createElement("a");
  a.className = "repo";
  a.href = repoUrl(text);
  a.target = "_blank";
  a.rel = "noopener noreferrer";
  code.classList.remove("ref");
  delete code.dataset.index;
  code.removeAttribute("title");
  code.prepend(icon("github"));
  code.replaceWith(a);
  a.append(code);
}

const KIND_ICON = { file: "file", dir: "folder", missing: "file-gone" };
const KIND_TITLE = { file: "Open in the viewer", dir: "A folder", missing: "No such file when shown; click to look again" };

// What path.stat answered, drawn on a reply's pending refs. Applied twice,
// it changes nothing.
export function applyKinds(node, kinds) {
  for (const code of node.querySelectorAll(".md code.ref[data-index]")) {
    if (code.dataset.kind) continue;
    const k = kinds[Number(code.dataset.index)];
    if (k === "repo") asRepo(code, code.textContent.trim());
    else if (k === "code") {
      code.classList.remove("ref");
      code.removeAttribute("title");
      delete code.dataset.index;
    } else if (KIND_ICON[k]) {
      code.dataset.kind = k;
      code.prepend(icon(KIND_ICON[k]));
      code.title = KIND_TITLE[k];
    }
  }
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// A node holding a sprite symbol and, for a button, its label.
function withIcon(n, name, text) {
  n.append(entryIcon(name));
  if (text) n.append(el("span", null, text));
  return n;
}

// A button that is only its icon: the label is its tooltip and its name.
function iconButton(n, name, label) {
  n.classList.add("icon");
  n.append(entryIcon(name));
  n.title = label;
  n.setAttribute("aria-label", label);
  return n;
}

export function hhmm(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// A click folds or opens a side note in this browser only; the server's
// "folded" is the default until then (spec /btw, section 07). Keyed by the
// side note's own id: entry ids repeat across sessions.
export const btwFolds = new Map();

function row(entry, cls, body) {
  const r = el("div", `row ${cls}`);
  r.dataset.id = entry.id;
  const t = el("span", "t", hhmm(entry.ts));
  const g = withIcon(el("span", "g"), entry.glyph);
  t.dataset.chrome = g.dataset.chrome = ""; // not text a selection quotes (quote.js)
  r.append(t, g, body);
  return r;
}

// A file's lines, numbered from where the view starts, coloured once the
// language has loaded (the row is open, so it is in view).
function fileView(file, lang) {
  const view = el("div", "lines");
  const draw = (html) => {
    view.innerHTML = html.map((h, i) => `<span class="n">${file.first + i}</span><span class="c">${h}</span>`).join("");
  };
  draw(file.text.split("\n").map(esc));
  if (lang) colour(file.text, lang).then((got) => got && draw(splitLines(got.html)), () => {});
  return view;
}

// A tool's arguments: a coloured block for a command or other arguments, the
// plain lines for a file tool's (its path and range).
function argsView(text, lang) {
  if (!lang) return el("pre", "args", text);
  const wrap = el("div", "md args");
  wrap.innerHTML = fenceHtml(text, lang);
  wrap.querySelector(".fh").append(copyButton("code"));
  paint(wrap.firstChild);
  return wrap;
}

const RENDERERS = {
  user(e) {
    const body = el("div", "body");
    if (e.md) body.append(markdown(e.md, { breaks: true }));
    if (e.detail?.files?.length) {
      // What the person attached: one card that pages, as a file_send's.
      const card = fileCard(e.detail.files, "file");
      card.classList.add("att");
      if (e.md) card.classList.add("after");
      body.append(card);
    }
    if (e.detail?.tail || e.detail?.more) {
      // A command OpenCode expanded: the line as typed, its template under it.
      const d = el("details");
      d.append(el("summary", null, "template"), e.detail.tail ? el("pre", "out", e.detail.tail) : el("div", "loading", "loading…"));
      body.append(d);
    }
    // Sent from the mic: the agent read a note asking it to allow for misheard words (#579).
    if (e.detail?.dictated) {
      const d = el("div", "dictated");
      d.append(...iconText("mic", "dictated"));
      body.append(d);
    }
    body.append(copyButton("message"));
    return row(e, `user ${e.status}`, body);
  },

  command(e) {
    const body = el("div", "body");
    body.append(el("div", "cmd", e.title));
    if (e.md) body.append(markdown(e.md));
    return row(e, `command ${e.status}`, body);
  },

  prose(e) {
    const body = markdown(e.md, { paths: e.detail?.paths });
    body.classList.add("body");
    const r = row(e, "prose", body);
    // The gutter: the read mark, then the chip that reads the reply aloud (speak.js).
    const rm = el("span", "rm");
    rm.dataset.chrome = "";
    // Only the live view carries the flag; an archived transcript draws no mark.
    if (e.unread !== undefined) {
      rm.append(icon(e.unread ? "unread" : "read"));
      r.classList.toggle("unread", e.unread);
      r.dataset.rev = String(e.rev);
    }
    if (speaker && !e.live) rm.append(speakButton(speaker.chipOn(e.id)));
    r.append(rm);
    return r;
  },

  thinking(e) {
    const body = el("div", "body");
    if (e.md || e.detail?.more) {
      const d = el("details");
      d.append(el("summary", null, e.title || "Thinking"), e.md ? markdown(e.md) : el("div", "loading", "loading…"));
      body.append(d);
    } else {
      body.textContent = e.summary || "thought";
    }
    return row(e, "think", body);
  },

  tool(e) {
    const det = e.detail || {};
    const d = el("details");
    const line = el("summary", "line");
    line.dataset.chrome = ""; // the tool's name and counts, not its output (quote.js)
    const name = el("span", "tn", e.title);
    const label = el("span", "ta", e.summary);
    if (det.steps) label.append(el("span", "steps", `${det.steps} steps`));
    line.append(name, label, el("span", "tr2", e.status === "running" ? "" : det.result || ""));
    if (e.status !== "running") line.append(copyButton("output"));
    d.append(line);
    const more = el("div", "more");
    if (det.path && fileBase === "") {
      // A file tool's row: the person can see its file without the agent
      // sending it (file.peek). Not on a linked server's row: the op is local.
      const bar = el("div", "peekbar");
      bar.dataset.chrome = "";
      const b = det.peek ? withIcon(el("button", "btn peek"), "again", "Show the file again") : withIcon(el("button", "btn peek"), "file", "Show the file");
      b.dataset.entry = e.id;
      bar.append(b, el("span", "peekerr"));
      more.append(bar);
    }
    if (det.peek) {
      const cap = el("div", "peekcap", `as of ${hhmm(det.peek.ts)}`);
      const card = fileCard(det.peek.files, det.peek.glyph);
      cap.dataset.chrome = card.dataset.chrome = "";
      more.append(cap, card);
    }
    if (det.file) more.append(fileView(det.file, det.lang));
    if (det.diff) more.append(diffView(det.diff, det.lang));
    if (det.tail) more.append(el("pre", "out", det.tail));
    if (det.args && !det.file && !det.diff) more.append(argsView(det.args, det.path ? null : det.lang));
    if (det.more) {
      const wait = el("div", "loading", "loading…");
      wait.dataset.chrome = "";
      more.append(wait);
    }
    if (more.childNodes.length) d.append(more);
    const body = el("div", "body");
    body.append(d);
    return row(e, `tool ${e.status}`, body);
  },

  system(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail || e.detail?.more) {
      const d = el("details");
      d.open = !!e.detail.open;
      d.append(el("summary", null, "show"), e.detail.tail ? el("pre", "out", e.detail.tail) : el("div", "loading", "loading…"));
      body.append(d);
    }
    return row(e, "sys", body);
  },

  inbox(e) {
    // A monitor wake, a queue result, a handoff: the header is the title.
    const body = el("div", "body");
    const from = el("div", "from", e.title);
    // An urgent handoff sent into the running turn, not after it.
    if (e.detail?.steered) from.append(el("span", "steered", "steered in"));
    body.append(from);
    const text = (e.md || "").split("\n").filter((l) => !l.startsWith("> from ")).join("\n");
    body.append(markdown(text));
    // A background command's or a subagent's notice: the command or the
    // report, and where its output went, folded under the label (#699).
    const det = e.detail || {};
    if (det.fold || det.output) {
      const d = el("details");
      d.append(el("summary", null, det.lang ? "command" : det.fold ? "report" : "output"));
      if (det.fold) d.append(det.lang ? argsView(det.fold, det.lang) : markdown(det.fold));
      if (det.output) d.append(el("div", "outfile", det.output));
      body.append(d);
    }
    return row(e, `inbox ${e.status}`, body);
  },

  file(e) {
    // An agent's file_send, as a card (fileCard) under its caption.
    const det = e.detail || {};
    // A linked server older than sets sends its one file's fields on the detail.
    const sent = det.files || [{ ...det, name: e.title, summary: e.summary }];
    const body = el("div", "body");
    if (e.md) {
      const cap = markdown(e.md);
      cap.classList.add("cap");
      body.append(cap);
    }
    body.append(fileCard(sent, e.glyph));
    return row(e, "file", body);
  },

  approval(e) {
    // An agent's schedule waiting for a person: the schedule in full and the
    // one button that approves it (fillApproval). Never folded.
    const body = el("div", "body");
    const card = el("div", "fcard apcard");
    card.dataset.object = e.detail?.object || "";
    card.dataset.name = e.detail?.name || "";
    card.dataset.server = e.detail?.server || "";
    card.dataset.glyph = e.glyph || "";
    fillApproval(card);
    body.append(card);
    return row(e, "approval", body);
  },

  secret(e) {
    // A secret for the person to reveal once (fillSecret). Never folded.
    const body = el("div", "body");
    const card = el("div", "fcard scard");
    fillSecret(card, e);
    body.append(card);
    return row(e, "secret", body);
  },

  config(e) {
    // A config change waiting for a person (fillConfig). Never folded.
    const body = el("div", "body");
    const card = el("div", "fcard scard ccard");
    fillConfig(card, e);
    body.append(card);
    return row(e, "config", body);
  },

  claude_login(e) {
    // A failed Claude sign-in: steps and a token field (fillLogin).
    const body = el("div", "body");
    const card = el("div", "fcard scard lcard");
    fillLogin(card, e);
    body.append(card);
    return row(e, "claude_login", body);
  },

  knowledge(e) {
    // A memory or skill an agent saved, waiting for a person (fillKnowledge).
    // Never folded.
    const body = el("div", "body");
    const card = el("div", "fcard scard ccard kcard");
    fillKnowledge(card, e);
    body.append(card);
    return row(e, "knowledge", body);
  },

  secret_request(e) {
    // A secret asked of the person: a password field (fillRequest).
    const body = el("div", "body");
    const card = el("div", "fcard scard");
    fillRequest(card, e);
    body.append(card);
    return row(e, "secret_request", body);
  },

  workflow(e) {
    // A workflow this session armed (fillWorkflow). Never folded.
    const body = el("div", "body");
    const card = el("div", "fcard wfcard");
    card.dataset.run = e.detail?.run || "";
    // Redrawn by syncDock only when its docking changed, as a file card is:
    // syncDock runs on every update of the shown session's card.
    card._sync = () => {
      if (docked(`wf-${card.dataset.run}`) !== card.dataset.held) fillWorkflow(card);
    };
    fillWorkflow(card);
    body.append(card);
    return row(e, "workflow", body);
  },

  workflow_decision(e) {
    // What a run waits on a person for (workflows.py): a gate, an edit, or
    // arming again. Never folded; filled from the workflows channel.
    const body = el("div", "body");
    const card = el("div", "fcard wfdcard");
    Object.assign(card.dataset, { run: e.detail?.run || "", what: e.detail?.what || "", key: e.detail?.key || "" });
    fillDecision(card);
    body.append(card);
    return row(e, "workflow_decision", body);
  },

  artifact(e) {
    // An agent's interactive page: a card like a file's, the frame while
    // live, one line with the label after. Status and label were decided in
    // the fold; the frame's state is handed over by js/artifacts.js on init.
    const det = e.detail || {};
    const body = el("div", "body");
    if (e.md) {
      const cap = markdown(e.md);
      cap.classList.add("cap");
      body.append(cap);
    }
    const card = el("div", "fcard acard");
    card.dataset.status = e.status;
    const bar = el("div", "fbar");
    const acts = el("span", "acts");
    const view = iconButton(el("button", "btn primary view"), "expand", "Open");
    view.addEventListener("click", () => opener({ artifact: e.id }));
    if (e.status === "live") acts.append(view, dockButton(e.id), popButton(e.id));
    else {
      const open = iconButton(el("a", "btn open"), "open", "Open in a new tab");
      open.href = fileUrl(det.url);
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      acts.append(view, open);
    }
    acts.prepend(phoneButton(e.id));
    if (e.status !== "live") acts.prepend(el("button", "btn show", "Show"));
    card.dataset.md = e.md || ""; // the caption's source, for update() to compare exactly
    bar.append(entryIcon(e.glyph), el("span", "fn", e.title), el("span", "fs", e.summary), acts);
    card.append(bar);
    const holder = held.get(e.id);
    holder?.onEntry(e);
    if (e.status === "live" && holder) card.append(heldLine(e.id, holder.label));
    else if (e.status === "live" && !docker.frames()) card.append(heldLine(e.id, "in window 1"));
    else if (e.status === "live") card.append(artifactStage(e));
    else {
      const what = det.label || (e.status === "closed" ? "closed by the agent" : "answered");
      card.append(el("div", "done", `${what} · ${hhmm(det.ended_ts)}`));
    }
    body.append(card);
    return row(e, "artifact", body);
  },

  recap(e) {
    // One quiet line, the latest as the older ones: what the person reads
    // coming back, not a card that outshouts the transcript. Only the latest
    // carries its ask; the model and cost are its tooltip.
    const det = e.detail || {};
    const body = el("div", "body");
    body.append(el("span", "lbl", "recap · "), el("span", "ctx", det.context));
    if (det.ask && !det.folded) body.append(el("span", "ask", ` · ${det.ask}`));
    const r = row(e, det.folded ? "recap folded" : "recap", body);
    r.querySelector(".g").replaceChildren(icon("sparkle"));
    const secs = det.duration_ms ? `${(det.duration_ms / 1000).toFixed(1)}s` : "";
    r.title = [det.model, secs, det.cost_usd ? money(det.cost_usd) : ""].filter(Boolean).join(" · ");
    return r;
  },

  btw(e) {
    const det = e.detail || {};
    const body = el("div", "body");
    const sum = el("span", "sum");
    const end = e.status === "ok" ? det.answer : e.status === "running" ? "…" : e.status === "cancelled" ? "cancelled" : det.why;
    sum.append(el("span", "lbl", "btw · "), el("span", null, `${det.question} · ${end || ""}`));
    const hd = el("div", "hd");
    hd.append(el("span", null, "btw"), el("span", "q", det.question));
    const full = el("div", "full");
    if (e.status === "running") {
      full.append(el("div", "ans wait", "reading the transcript…"));
    } else if (e.status === "ok") {
      const ans = markdown(det.answer);
      ans.classList.add("ans");
      full.append(ans);
      if (det.needs_more) full.append(el("div", "more", "needs more: the window did not hold the answer"));
    } else if (e.status === "failed") {
      full.append(el("div", "why", `failed: ${det.why}`));
    }
    const ft = el("div", "ft");
    const secs = det.duration_ms ? `${(det.duration_ms / 1000).toFixed(1)}s` : "";
    ft.append(el("span", null, [det.model, secs, det.cost_usd ? money(det.cost_usd) : "", det.header].filter(Boolean).join(" · ")));
    if (e.status === "running") {
      const stop = el("button", "btn link", "cancel");
      stop.dataset.btwCancel = det.id;
      ft.append(stop);
    }
    full.append(ft);
    body.append(sum, hd, full);
    const folded = btwFolds.has(det.id) ? btwFolds.get(det.id) : det.folded;
    const r = row(e, folded ? "btw folded" : "btw", body);
    r.dataset.btw = det.id; // unique across sessions, unlike the entry id
    r.querySelector(".g").replaceChildren(icon("btw"));
    return r;
  },

  error(e) {
    const body = el("div", "body", e.summary);
    if (e.detail?.tail) body.append(el("pre", "out", e.detail.tail));
    return row(e, "error", body);
  },
};

export function render(entry) {
  const r = (RENDERERS[entry.kind] || RENDERERS.system)(entry);
  // A part that opened has its place before its first words: nothing shows yet.
  if ((entry.kind === "prose" || entry.kind === "thinking") && !entry.md && !entry.summary && !entry.detail?.more) r.hidden = true;
  return r;
}

// A sent file's or artifact's URL as this page may use it: on a linked
// server's transcript, only a path of the one shape /via serves, rebuilt here,
// so no URL the far server chose reaches an href or a frame on this origin.
export function fileUrl(url) {
  if (fileBase === "") return url;
  const ok = typeof url === "string" && /^\/files\/[A-Za-z0-9_-]+\/[^/?#]+$/.test(url);
  return ok ? fileBase + url : "about:blank";
}

// The artifacts held elsewhere (the viewer, a pane), each with what to call
// when its entry is drawn again (a new status, a resend) and the line its card
// shows instead. While one is live its card draws no frame, so the page never
// runs twice; releasing it gives the card its frame back, if the card is on
// screen.
const held = new Map(); // artifact id -> {onEntry: (entry) => void, label}
export function hold(id, onEntry, entry, label = "open in the viewer") {
  const card = document.querySelector(`#entries .row[data-id="${CSS.escape(id)}"] .acard`);
  if (onEntry) {
    held.set(id, { onEntry, label });
    const was = card?.dataset.status === "live" && card.querySelector(":scope > .stage, :scope > .held");
    if (was) was.replaceWith(heldLine(id, label));
    return;
  }
  held.delete(id);
  const line = card?.querySelector(":scope > .held");
  if (line && entry?.status === "live" && docker.frames()) line.replaceWith(artifactStage(entry));
}

// Phone width, on every artifact's bar: the frame narrows to a phone's
// viewport so the page's own media queries answer as on a phone. Per
// artifact and per tab, so a redrawn card, the viewer and a pane agree.
const phoneIds = new Set();
export function phoneButton(id) {
  const b = iconButton(el("button", "btn phone"), "phone", "Phone width");
  b.type = "button";
  b.dataset.phone = id;
  b.setAttribute("aria-pressed", String(phoneIds.has(id)));
  b.addEventListener("click", () => {
    const on = !phoneIds.has(id);
    if (on) phoneIds.add(id);
    else phoneIds.delete(id);
    const sel = CSS.escape(id);
    for (const f of document.querySelectorAll(`#a2 iframe[data-artifact="${sel}"]`)) f.parentElement.classList.toggle("phone", on);
    for (const x of document.querySelectorAll(`#a2 .btn.phone[data-phone="${sel}"]`)) x.setAttribute("aria-pressed", String(on));
  });
  return b;
}

// The frame of an artifact's card, live or shown again read-only.
export function artifactStage(e) {
  const stage = el("div", "stage html");
  stage.classList.toggle("phone", phoneIds.has(e.id));
  const f = el("iframe");
  f.setAttribute("sandbox", "allow-scripts");
  f.loading = "lazy";
  f.src = fileUrl(e.detail.url);
  f.title = e.title;
  f.dataset.artifact = e.id;
  f.dataset.status = e.status;
  f.dataset.stateRev = String(e.detail.state_rev); // the state it starts with
  stage.append(f);
  return stage;
}

// A row updated in place, so a live frame is not reloaded by every patch.
// True when the node now shows the entry; false when it must be remounted.
const UPDATERS = {
  // Being read re-sends the message with its revision unchanged: only the
  // mark flips, and the row keeps the pointer's hover and any selection in it.
  // A delta does not move the revision either, but it grows the text (#373).
  prose(e, node, prev) {
    const rm = node.querySelector(":scope > .rm");
    if (!rm || e.unread === undefined || node.dataset.rev !== String(e.rev)) return false;
    if (prev?.md !== e.md || !!prev?.live !== !!e.live) return false;
    rm.querySelector(":scope > .ic")?.replaceWith(icon(e.unread ? "unread" : "read"));
    node.classList.toggle("unread", e.unread);
    return true;
  },
  artifact(e, node) {
    const card = node.querySelector(".acard");
    // A page the viewer or a pane holds runs only there: the card keeps a
    // line, and the agent's state goes to that frame, when the session is shown.
    const holder = held.get(e.id);
    const lined = holder || !docker.frames(); // the card shows a line, not the page
    const sel = `iframe[data-artifact="${CSS.escape(e.id)}"]`;
    const frame = holder
      ? document.querySelector(`#viewer ${sel}, #canvas .pane.docked ${sel}`)
      : lined
        ? null
        : node.querySelector("iframe[data-artifact]");
    if (!card || card.dataset.status !== e.status) return false;
    if (lined ? !card.querySelector(".held") : !frame) return false;
    // A new caption, a new title or a new page (a resend) remounts the card.
    if (card.dataset.md !== (e.md || "")) return false;
    if (node.querySelector(".fbar .fn")?.textContent !== e.title) return false;
    if (frame && frame.getAttribute("src") !== fileUrl(e.detail?.url)) return false;
    // Only a state the agent set since the frame last had one: every page
    // record patches this entry, and an emit must not echo the state back.
    const rev = String(e.detail?.state_rev);
    if (frame && e.detail?.state_by === "agent" && rev !== frame.dataset.stateRev) {
      frame.dataset.stateRev = rev;
      frame.dispatchEvent(new CustomEvent("aegis:state", { detail: e.detail.state, bubbles: true }));
    }
    return true;
  },
};
export function update(e, node, prev) {
  const u = UPDATERS[e.kind];
  return u ? u(e, node, prev) : false;
}
