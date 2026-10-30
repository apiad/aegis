// Settings: .aegis.yaml as a form. The server holds the file's parsed copy and
// sends it on the `config` channel; Save writes the whole form back through
// `config.write`, which refuses a stale write and validates first. The server
// re-reads the file after any write, so the page never patches server state.
// Nothing here decides what is valid: every finding names the row it marks.
//
// An agent is drawn as a card whose chips are the new-tab composer's, so a
// preset looks like the session it starts.
//
// "This browser" comes first: the theme, which only this browser keeps
// (localStorage), so it is not part of the form and Save does not write it,
// then this device as an app: installed or not, and the build the service
// worker keeps for offline use (install.js, appworker.js).
//
// The Servers section lists the servers this one links (links.py), and a
// picker points the form at a linked server's .aegis.yaml: the same `config.*`
// operations and `config` channel, sent through the link.

import { elapsed } from "./gauges.js";
import { modelMeta, modelPrice } from "./models.js";
import "./pick.js";
import { who } from "./me.js";
import { deviceCopy } from "./appworker.js";
import { installState, onInstallState, openInstallDialog } from "./install.js";
import { onPushState, pushState, unsubscribePush } from "./pushpanel.js";
import { markNeedsNet, offline } from "./net.js";
import { ask } from "./dialog.js";
import { MIN_SPEED } from "./speak.js";

const h = (tag, props = {}, ...kids) => {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...kids.filter((k) => k != null && k !== false));
  return el;
};

const button = (text, id, onclick, className = "btn") => {
  const b = h("button", { type: "button", textContent: text, onclick, className });
  if (id) b.id = id;
  return b;
};

function select(name, values, value, label = (v) => v) {
  const s = h("pick-chip", { name, className: "pick" });
  s.options = values.map((v) => ({ value: v, label: label(v) }));
  if (value && !values.includes(value)) s.options = [...s.options, { value, label: `${value} (unknown)` }];
  s.value = value || "";
  return s;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// One window's lines in words.
const readout = (l) => `start ${l.launch === "pace" ? "on pace" : `below ${l.launch}%`} · stop at ${l.stop}%`;

export class Settings {
  constructor(conn, box, { themes, setTheme, speaker = null }) {
    this.conn = conn;
    this.speaker = speaker; // app.js's Speaker: the Read aloud row chooses its voice (speak.js)
    this.box = box;
    this.themes = themes;
    this.setTheme = setTheme; // app.js's: the one path that changes the theme
    this.wire = null; // the config the server last sent
    this.doc = null; // what the form shows
    this.stamp = null; // the stamp of the file the form was loaded from
    this.dirty = false;
    this.saving = false;
    this.stale = false;
    this.found = []; // config.detect
    this.findings = null; // the last doctor run, or the last refused save
    this.checked = false; // whether `findings` is a doctor run
    this.allChecks = false; // the full list of checks is open
    this.status = "";
    this.server = null; // the server whose config the form edits; null: this one
    this.home = ""; // this server's name
    this.links = []; // the `links` channel
    this.homeWire = null;
    this.unsubFar = null;
    this.linkError = "";
    this.models = null; // models.list
    this.modelView = { harness: "", price: "", sort: "use" };
  }

  call(op, params = {}) {
    return this.conn.call(op, params, this.server);
  }

  // This server's config: shown unless the picker is on a linked server.
  onHomeConfig(wire) {
    this.homeWire = wire;
    if (this.server === null) this.onConfig(wire);
  }

  onLinks(links, home) {
    this.links = links;
    this.home = home;
    this.incoming = undefined; // a way back opened or went: read it again
    if (this.server && !links.some((l) => l.name === this.server)) this.pick(null);
    else if (this.box.isConnected && this.box.offsetParent !== null && !this.dirty) this.editing() ? this.later() : this.draw();
  }

  // Whether a field of the form has the focus. A chip's typed filter is not
  // an edit until Enter takes it, so the form is not dirty yet, and a redraw
  // would replace the chip and drop the choice (#284).
  editing() {
    const el = document.activeElement;
    return this.box.contains(el) && el.matches("input, textarea");
  }

  // Catch up with what the channels sent once the focus leaves the form.
  later() {
    if (this.waiting) return;
    this.waiting = true;
    const done = () =>
      setTimeout(() => {
        if (this.editing()) return this.box.addEventListener("focusout", done, { once: true });
        this.waiting = false;
        if (this.wire) this.onConfig(this.wire);
        else this.draw();
      });
    this.box.addEventListener("focusout", done, { once: true });
  }

  // Point the form at a server: its config replaces the form, edits and all.
  pick(server) {
    if (server === this.server) return;
    this.unsubFar?.();
    this.unsubFar = null;
    this.server = server;
    this.wire = null;
    this.doc = null;
    this.savedWf = undefined; // the saved workflows are that server's
    this.wfStatus = null;
    this.dirty = false;
    this.findings = null;
    this.found = [];
    this.models = null;
    if (server === null) {
      if (this.homeWire) this.onConfig(this.homeWire);
    } else
      this.unsubFar = this.conn.subscribe(
        "config",
        (w) => this.onConfig(w),
        (ops) => {
          for (const op of ops) if (op.set) this.onConfig(op.set);
        },
        (e) => {
          this.status = e.message;
          this.draw();
        },
        undefined,
        server,
      );
    this.draw();
    this.open();
  }

  // A config from the channel. An untouched form follows the file; an edited
  // one keeps its edits and says the file moved. Under a field being typed in
  // nothing is redrawn: the chips hold the doc they were drawn from. An edited
  // form says the file moved at once, in place; an untouched one waits.
  onConfig(wire) {
    const moved = wire.stamp !== this.wire?.stamp;
    this.wire = wire;
    if (this.saving) return;
    if (this.doc !== null && this.editing()) {
      if (this.dirty && wire.stamp !== this.stamp && !this.stale) {
        this.stale = true;
        this.box.querySelector(".set-checks")?.before(this.staleAlert());
      }
      return this.later();
    }
    if (moved && this.models) this.loadModels(); // who uses which model
    if (!this.dirty || this.doc === null) this.load(wire);
    else if (wire.stamp !== this.stamp) this.stale = true;
    if (this.box.isConnected && this.box.offsetParent !== null) this.draw();
  }

  load(wire) {
    this.doc = structuredClone(wire.doc);
    this.stamp = wire.stamp;
    this.dirty = false;
    this.stale = false;
  }

  // Entering the view. render() calls this only when the view changes, so a
  // sessions patch never redraws the form under the person's cursor.
  async open() {
    this.draw();
    this.loadNative();
    this.loadModels();
    if (this.found.length) return;
    try {
      this.found = await this.call("config.detect");
    } catch (e) {
      this.status = e.message;
    }
    if (this.dirty) return;
    // A redraw replaces the form: never under a field being typed in, which
    // would drop what was typed. It waits until the focus leaves the page.
    if (!this.box.contains(document.activeElement)) return this.draw();
    const later = () =>
      setTimeout(() => {
        if (this.box.contains(document.activeElement)) return this.box.addEventListener("focusout", later, { once: true });
        if (!this.dirty) this.draw();
      });
    this.box.addEventListener("focusout", later, { once: true });
  }

  touch() {
    this.dirty = true;
    this.status = "";
    this.drawBar();
  }

  draw() {
    // A redraw replaces the page, and with it the focus: while the theme chip
    // has it (its list open, say), wait until it leaves.
    if (this.browserNode?.contains(document.activeElement)) {
      this.drawLater = true;
      return;
    }
    this.drawLater = false;
    // A member changes nothing on the server: this browser, the doctor, a word.
    if (who.role === "member")
      return this.box.replaceChildren(
        h(
          "div",
          { className: "set-page" },
          h("p", { className: "notice", id: "set-member", textContent: "Server settings (agents, queues, parking, workflows, servers, tokens) are edited by an admin." }),
          this.browser(),
          this.knowledge(),
          this.checks(),
          this.modelsView(),
        ),
      );
    if (this.tokenRows === undefined) {
      this.tokenRows = null;
      this.loadTokens();
    }
    const w = this.wire;
    // The title first, then what this browser keeps, then the servers; the
    // loading and no-config pages keep the same width and order.
    const page = (...kids) => h("div", { className: "set-page" }, ...kids, this.browser(), this.knowledge(), this.servers(), this.tokens());
    if (!w || !this.doc) return this.box.replaceChildren(page(h("p", { className: "notice", textContent: "Loading…" })));
    if (!w.exists && !this.dirty) return this.box.replaceChildren(page(this.empty()));
    const full = h(
      "div",
      { className: "set-page" },
      this.head(),
      this.browser(),
      this.knowledge(),
      this.servers(),
      this.pauseView(),
      this.tokens(),
      w.error && h("p", { className: "set-alert err", id: "set-error", textContent: `The file on disk does not parse, so aegis is still using the last version that did. ${w.error}` }),
      this.stale && this.staleAlert(),
      this.checks(),
      this.agents(),
      this.modelsView(),
      this.queues(),
      this.fieldSection("generators"),
      this.terminals(),
      this.fieldSection("memory"),
      this.fieldSection("context"),
      this.fieldSection("nudges"),
      this.fieldSection("notices"),
      this.park(),
      this.untested(),
      this.workflows(),
      this.usage(),
      this.fieldSection("embeddings"),
      this.fieldSection("network"),
      this.fieldSection("host"),
    );
    this.box.replaceChildren(full, this.bar());
    this.mark();
  }

  staleAlert() {
    return h(
      "p",
      { className: "set-alert", id: "set-stale", textContent: "The file changed on disk since you opened it. " },
      button("Reload from disk", "set-reload", () => {
        this.load(this.wire);
        this.findings = null;
        this.draw();
      }),
    );
  }

  // What this browser alone keeps. Built once and moved into each draw, with
  // the chip set to the theme in use.
  browser() {
    if (!this.browserNode) {
      this.themeChip = h("pick-chip", { id: "set-theme", name: "theme", className: "pick" });
      this.themeChip.setAttribute("aria-label", "Theme");
      this.themeChip.options = this.themes;
      this.themeChip.addEventListener("change", () => this.setTheme(this.themeChip.value));
      this.speakNode = h("div", { id: "set-speak" });
      this.drawSpeak();
      this.appNode = h("div", { id: "set-app" });
      this.pushNode = h("div", { id: "set-push" });
      onInstallState(() => this.drawApp());
      onPushState(() => {
        this.drawApp();
        this.loadPush();
      });
      this.loadPush();
      this.browserNode = h(
        "section",
        { className: "set-browser", id: "set-browser" },
        h("h3", { textContent: "This browser" }),
        h("div", { className: "set-browser-row" }, h("span", { className: "set-browser-k", textContent: "Theme" }), this.themeChip),
        this.speakNode,
        this.appNode,
        this.pushNode,
        h("p", { className: "set-none", textContent: "Kept in this browser only: other browsers and devices keep their own." }),
      );
      // The draw that waited for the chip runs once the focus has left it.
      this.browserNode.addEventListener("focusout", () =>
        setTimeout(() => {
          if (this.drawLater) this.draw();
        }),
      );
    }
    this.themeChip.value = document.documentElement.dataset.theme;
    this.drawApp();
    return this.browserNode;
  }

  // The person's own knowledge switch (knowledge.setting): off, the harness
  // loads its own skills and memory beside aegis's; on, their sessions start
  // without them. Always this server's and the caller's own, whichever
  // server the form below edits; built once, like browser(), and read on
  // each open of Settings (loadNative).
  knowledge() {
    if (!this.knowledgeNode) {
      this.nativeBox = h("input", { type: "checkbox", id: "set-native" });
      this.nativeNote = h("p", { className: "set-none", id: "set-native-note" });
      this.nativeBox.disabled = true;
      this.nativeBox.addEventListener("change", async () => {
        const want = this.nativeBox.checked;
        this.nativeBox.disabled = true;
        try {
          const r = await this.conn.call("knowledge.setting", { native: want });
          this.drawNative(r.native, "Saved. A session already open keeps how it started; sessions you start from now on use this.");
        } catch (e) {
          this.nativeBox.checked = !want;
          this.drawNative(!want, String(e?.message || e), true);
        }
      });
      this.nativeRetry = button("Retry", "set-native-retry", () => this.loadNative());
      this.nativeRetry.hidden = true;
      this.knowledgeNode = h(
        "section",
        { className: "set-knowledge", id: "set-knowledge" },
        h("h3", { textContent: "Skills and memory" }),
        h("label", { className: "set-browser-row set-native" }, this.nativeBox, h("span", { textContent: "Only aegis's skills and memory in my sessions" })),
        this.nativeNote,
        this.nativeRetry,
      );
    }
    return this.knowledgeNode;
  }

  // The switch as the server has it now. A failed read leaves the box usable,
  // as it last was, with a Retry: never stuck disabled.
  loadNative() {
    this.knowledge();
    if (this.nativeLoading) return;
    this.nativeLoading = true;
    this.conn
      .call("knowledge.setting", {})
      .then((r) => this.drawNative(r.native))
      .catch((e) => this.drawNative(this.nativeBox.checked, `The setting could not be read (${e?.message || e}).`, true, true))
      .finally(() => (this.nativeLoading = false));
  }

  drawNative(on, said = "", err = false, retry = false) {
    this.nativeBox.checked = on;
    this.nativeBox.disabled = false;
    this.nativeRetry.hidden = !retry;
    const what = on
      ? "On: Claude Code starts without its Skill tool and its auto-memory, Codex without its skills and memories, OpenCode without its skills. Your aegis skills and memories replace them."
      : "Off: each harness also loads its own skills and memory, beside aegis's. /setup moves them into aegis first.";
    this.nativeNote.textContent = said ? `${said} ${what}` : what;
    this.nativeNote.classList.toggle("err", err);
  }

  // Whether aegis is installed here, and what the service worker keeps.
  // Read aloud: the browser's own voice, or Phonon after its download and
  // speed check here (speak.js). `busy` is a line shown while one runs.
  drawSpeak(busy = null) {
    if (!this.speaker) return;
    const s = this.speaker.settings;
    const chip = h("pick-chip", { id: "set-speak-engine", name: "speech", className: "pick" });
    chip.setAttribute("aria-label", "Read aloud voice");
    chip.options = [
      { value: "builtin", label: "Built-in" },
      { value: "phonon", label: "Phonon" },
    ];
    chip.value = s.engine;
    chip.addEventListener("change", () => this.chooseSpeech(chip.value));
    const m = s.measured;
    let note;
    if (busy) note = busy;
    else if (s.engine === "phonon") note = `Phonon for English replies, ${m.speed}× real time on ${m.device === "webgpu" ? "the GPU" : "the CPU"}; other languages use the browser's voice`;
    else if (m && m.speed < MIN_SPEED) note = `Phonon made speech at ${m.speed}× real time here, under ${MIN_SPEED}×: it would stutter`;
    else note = "the browser's own voice, in every language it has; Phonon sounds better: a 65 MB download, English only";
    const kids = [h("span", { className: "set-app-v set-speak-note", textContent: note })];
    if (!busy && s.engine !== "phonon" && m && m.speed < MIN_SPEED) {
      const b = button("Use it anyway", null, () => this.chooseSpeech("phonon", true));
      b.dataset.act = "speak-anyway";
      kids.push(b);
    }
    this.speakNode.replaceChildren(
      h("div", { className: "set-browser-row" }, h("span", { className: "set-browser-k", textContent: "Read aloud" }), chip),
      h("div", { className: "set-speak-line" }, ...kids),
    );
  }

  async chooseSpeech(engine, anyway = false) {
    if (this.speechBusy) return this.drawSpeak(this.speechBusy);
    const files = new Map();
    const onProgress = (p) => {
      files.set(p.file, p);
      const all = [...files.values()];
      const got = all.reduce((a, f) => a + f.loaded, 0) / 1e6;
      const total = all.reduce((a, f) => a + (f.total || f.loaded), 0) / 1e6;
      this.speechBusy = got < total ? `downloading Phonon: ${got.toFixed(1)} of ${total.toFixed(1)} MB` : `checking how fast this device speaks, on the ${p.device === "webgpu" ? "GPU" : "CPU"}…`;
      this.drawSpeak(this.speechBusy);
    };
    if (engine === "phonon" && !anyway) this.drawSpeak((this.speechBusy = "preparing Phonon's files on the server…"));
    try {
      await this.speaker.choose(engine, { onProgress, anyway });
      this.speechBusy = null;
      this.drawSpeak();
    } catch (e) {
      this.speechBusy = null;
      await this.speaker.choose("builtin");
      this.drawSpeak(`Phonon did not start: ${e.message || e}`);
    }
  }

  drawApp() {
    const row = (k, v) => h("div", { className: "set-browser-row" }, h("span", { className: "set-browser-k", textContent: k }), v);
    const { canPrompt, installed } = installState();
    let app;
    if (installed) app = h("span", { className: "set-app-v", textContent: "installed" });
    else if (canPrompt) {
      app = button("Install", null, openInstallDialog);
      app.dataset.act = "install";
    } else app = h("span", { className: "set-app-v", textContent: "your browser's menu installs it" });
    const copy = h("span", { className: "set-app-v", textContent: navigator.serviceWorker ? "downloading" : "not kept: needs https" });
    const push = pushState();
    const note = !push.supported
      ? "not here: push needs https"
      : push.subscribed
        ? `push on · ${push.sources.length} sources · the bell chooses them`
        : "push off · the bell turns it on";
    this.appNode.replaceChildren(row("App", app), row("Offline copy", copy), row("Notifications", h("span", { className: "set-app-v", textContent: note })));
    deviceCopy().then((c) => {
      if (c?.complete) copy.textContent = `build ${c.build} · ${Math.round(c.bytes / 1024).toLocaleString()} KB · ready`;
    });
  }

  // Every device this token gets push on (push.py), so a lost phone can be
  // cut off from another device. Always this server's: push is per server,
  // whichever server the form below edits.
  loadPush() {
    this.conn
      .call("push.list")
      .then(({ subscriptions }) => {
        this.pushRows = subscriptions.filter((r) => r.owner === who.name);
        this.drawPush();
      })
      .catch(() => {}); // offline: the list waits for the next change
  }

  drawPush() {
    const here = pushState().id;
    const when = (t) => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }) : "never");
    const rows = (this.pushRows || []).map((r) => {
      const remove = button("Remove", null, async () => {
        remove.disabled = true;
        if (r.id === here) await unsubscribePush().catch(() => {});
        else await this.conn.call("push.remove", { id: r.id }).catch(() => {});
        this.loadPush();
      });
      const facts = [r.sources.join(", ") || "no sources", `last delivered ${when(r.last_ok_at)}`, r.failures ? `${r.failures} failed` : null].filter(Boolean);
      const el = h(
        "div",
        { className: "set-push-row" },
        h("span", { className: "set-push-dev" }, h("b", { textContent: r.device }), r.id === here && h("span", { className: "set-push-here", textContent: " · this device" })),
        h("span", { className: "set-push-facts", textContent: facts.join(" · ") }),
        remove,
      );
      el.dataset.id = r.id;
      return el;
    });
    this.pushNode.replaceChildren(...(rows.length ? [h("div", { className: "set-browser-k", textContent: "Push to" }), ...rows] : []));
  }

  // What aegis starts from now on reaches the internet from this link (exit.py).
  async exitVia(name) {
    if (this.dirty) {
      this.status = "Save or discard your edits first";
      this.draw();
      return;
    }
    try {
      await this.call("link.exit", { name });
    } catch (e) {
      this.status = e.message;
      this.draw();
    }
  }

  // The servers: this one, each link with its state, and a form to add one.
  servers() {
    const exitButton = (...args) => {
      const b = button(...args);
      b.dataset.key = "exit_via";
      return b;
    };
    const since = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const trust = (l) => (!l.trusted || l.state !== "linked" ? "" : l.granted ? " · trusted, both ways" : l.trust_error ? ` · trust refused: ${l.trust_error}` : "");
    const state = (l) =>
      l.state === "linked"
        ? `linked${l.rtt_ms != null ? ` · ${l.rtt_ms} ms` : ""}${trust(l)}`
        : l.state === "offline"
          ? `offline since ${since(l.since)}`
          : `${l.state}${l.error ? `: ${l.error}` : ""}`;
    // The servers that linked this one with trust (links.py, Backlink).
    if (this.incoming === undefined) {
      this.incoming = [];
      this.conn
        .call("link.incoming")
        .then((rows) => {
          this.incoming = rows;
          if (rows.length && !this.dirty) this.editing() ? this.later() : this.draw();
        })
        .catch(() => {});
    }
    const rows = [
      h("tr", {}, h("td", {}, h("b", { textContent: this.home })), h("td", { className: "m", textContent: "this server" }), h("td", {}, h("span", { className: "set-pill ok", textContent: "serving" })), h("td")),
      ...this.links.map((l) =>
        h(
          "tr",
          { className: `set-link link-${l.name}` },
          h("td", {}, h("b", { textContent: l.name })),
          h("td", { className: "m", textContent: l.url }),
          h("td", {}, h("span", { className: `set-pill ${l.state === "linked" ? "ok" : "bad"}`, textContent: state(l) })),
          h("td", {}, !this.server && l.trusted && l.granted ? exitButton(l.name === this.doc?.exit_via ? "Stop exit" : "Exit here", null, () => this.exitVia(l.name === this.doc?.exit_via ? null : l.name)) : null, button("Remove", null, () => this.unlink(l.name))),
        ),
      ),
      ...this.incoming.map((l) =>
        h(
          "tr",
          { className: `set-link set-incoming link-${l.name}` },
          h("td", {}, h("b", { textContent: l.name })),
          h("td", { className: "m", textContent: "links here" }),
          h("td", {}, h("span", { className: `set-pill ${l.state === "linked" ? "ok" : "bad"}`, textContent: l.exit_bytes ? `exit · ${Math.round(l.exit_bytes / 1e6)} MB` : l.state === "linked" ? "trusted · both ways" : l.state })),
          h("td"),
        ),
      ),
    ];
    const url = h("input", { className: "set-in", id: "set-link-url", placeholder: "https://dev.example", spellcheck: false });
    const token = h("input", { className: "set-in", id: "set-link-token", placeholder: "its token; kept in links.json, never in .aegis.yaml", type: "password" });
    const trusted = h("input", { type: "checkbox", id: "set-link-trusted" });
    const trustBox = h(
      "label",
      { className: "set-link-trust", title: "Only for a server you alone use. Its agents will be able to start sessions and read here." },
      trusted,
      h("span", { textContent: "Trusted (both ways)" }),
    );
    const add = button("Link", "set-link-add", () => this.link(url.value.trim(), token.value.trim(), trusted.checked), "btn primary");
    const via = this.doc?.exit_via;
    const exitLine = h("p", { className: "set-none", id: "set-exit", textContent: via ? `What aegis starts reaches the internet through ${via}.` : "What aegis starts uses this machine's route. A trusted link's row can make it the exit." });
    exitLine.dataset.key = "exit_via";
    const picker =
      this.links.length > 0 &&
      h(
        "div",
        { className: "set-srvpick", id: "set-srvpick" },
        h("span", { textContent: "Editing the config of" }),
        ...[null, ...this.links.map((l) => l.name)].map((name) => {
          const b = button(name ?? this.home, null, () => this.pick(name), `btn${name === this.server ? " primary" : ""}`);
          b.dataset.server = name ?? "";
          return b;
        }),
      );
    return h(
      "section",
      { className: "set-servers", id: "set-servers" },
      h("h3", { textContent: "Servers" }),
      h("p", { className: "set-none", textContent: "A linked server's sessions show in the Fleet and the tab bar. Nothing on it can reach this machine, unless the link is trusted: only for a server you alone use." }),
      h("table", { className: "tbl" }, ...rows),
      exitLine,
      h("div", { className: "set-linkrow" }, url, token, trustBox, add),
      this.linkError && h("p", { className: "err-text", id: "set-link-error", textContent: this.linkError }),
      picker,
    );
  }

  // Every session paused at once (pause.py): their processes end and nothing
  // new starts until Resume all, or a message to one session resumes it.
  pauseView() {
    if (this.paused === undefined) {
      this.paused = null;
      this.conn
        .call("server.paused")
        .then((r) => {
          this.paused = r.paused;
          if (r.paused && !this.dirty) this.editing() ? this.later() : this.draw();
        })
        .catch(() => {});
    }
    const act = async (op) => {
      this.pauseBusy = true;
      this.draw();
      try {
        this.paused = (await this.conn.call(op)).paused;
      } finally {
        this.pauseBusy = false;
        this.draw();
      }
    };
    const on = this.paused;
    const since = on && new Date(on.at * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" });
    const b = on ? button("Resume all", "set-resume-all", () => act("server.resume"), "btn primary") : button("Pause all", "set-pause-all", () => act("server.pause"));
    b.disabled = !!this.pauseBusy;
    return h(
      "section",
      { className: "set-pause", id: "set-pause" },
      h("h3", { textContent: "Sessions" }),
      h("p", {
        className: "set-none",
        textContent: on
          ? `Every session is paused, since ${since}${on.by.startsWith("aegis ") ? `, by ${on.by}` : ""}. Nothing runs until you resume it: here for all, or by writing to one.`
          : "Pause all ends every session's process, with what its agent left running, and holds new work, messages and spawns until you resume. It lasts across a restart.",
      }),
      h("div", { className: "set-linkrow" }, b),
    );
  }

  // Who may use this server (tokens.py) and how much of each pool its members
  // may spend (quota_gate.py): the pools now, the default policy, a card per
  // token, and a form to mint one, shown once. Policy edits go out with the
  // save bar; adding and revoking act at once, because each shows or kills a
  // secret. The chips are the server's verdict on the saved policies.
  tokens() {
    // In multi-user mode a token names the Linux user it runs as: the name
    // until the field is edited (tokens.py).
    const user =
      this.multiUser &&
      h("input", {
        className: "set-in",
        id: "set-token-user",
        placeholder: "Linux user",
        spellcheck: false,
        value: this.tokenUser ?? this.tokenName ?? "",
        oninput: (e) => (this.tokenUser = e.target.value),
      });
    const name = h("input", {
      className: "set-in",
      id: "set-token-name",
      placeholder: "name, e.g. ben or laptop",
      spellcheck: false,
      value: this.tokenName || "",
      oninput: (e) => {
        this.tokenName = e.target.value;
        if (user && this.tokenUser == null) user.value = e.target.value;
      },
    });
    const role = select("role", ["member", "admin"], this.tokenRole || "member");
    role.id = "set-token-role";
    role.addEventListener("change", () => (this.tokenRole = role.value));
    const add = button("Add token", "set-token-add", () => this.addToken(name.value.trim(), role.value, user ? user.value.trim() : null), "btn primary");
    const once = this.tokenOnce;
    const section = h(
      "section",
      { className: "set-tokens", id: "set-tokens" },
      h("h3", { textContent: "Tokens" }),
      h("p", { className: "set-none", textContent: "Who may use this server, and how much of each pool a member may spend. A member reads every session and changes only its own; an admin changes anything and is never gated." }),
      this.pools && this.poolNow(),
      this.pools && h("div", { className: "set-agents", id: "set-tokcards" }, this.tokenCard(null), ...(this.tokenRows || []).map((t) => this.tokenCard(t))),
      h("div", { className: "set-linkrow" }, name, user, role, add),
      this.tokenError && h("p", { className: "err-text", id: "set-token-error", textContent: this.tokenError }),
      once &&
        h(
          "div",
          { className: "notice set-once", id: "set-token-once" },
          h("span", { textContent: once.text }),
          h("code", { textContent: once.token }),
          button("Copy", null, () => navigator.clipboard?.writeText(once.token)),
        ),
    );
    this.handles(section);
    return section;
  }

  // Each pool's gauges now, so lines are set against the real readings.
  poolNow() {
    const now = Date.now() / 1000;
    return h(
      "div",
      { className: "poolnow", id: "set-poolnow" },
      ...this.pools.map((pool) => {
        const p = this.reading(pool.name);
        const rows = !pool.windows.length
          ? [h("div", { className: "kv dim" }, h("span", { textContent: "no gauge" }))]
          : !p
            ? [h("div", { className: "kv dim" }, h("span", { textContent: "no reading" }))]
            : p.windows.flatMap((w) => [
                h("div", { className: "kv" }, h("span", { textContent: w.label }), h("span", { textContent: `${Math.round(w.percent)}%` })),
                this.fill("bar", w.percent, elapsed(w, now)),
              ]);
        return h("div", { className: `pool-${pool.name}` }, h("h5", { textContent: pool.label }), ...rows);
      }),
    );
  }

  reading(pool) {
    return (this.quota?.providers || []).find((p) => p.name === pool && p.state !== "failed") || null;
  }

  // A gauge's track: the fill, and the share of the window gone.
  fill(cls, pct, gone) {
    const b = h("div", { className: cls }, h("i", { style: `width:${Math.min(100, Math.max(0, pct))}%` }));
    if (gone != null) b.append(h("span", { className: "tick", style: `left:${gone * 100}%` }));
    return b;
  }

  // One card: the default policy (t null), an admin, or a member with its own
  // policy or the default's.
  tokenCard(t) {
    const key = t ? t.name : "*";
    const card = h("div", { className: t ? `set-agent set-tok tok-${t.name}` : "set-agent set-tok is-default", id: t ? "" : "set-tok-default" });
    const chips = (gates) =>
      this.pools.map((pool) => {
        const st = gates?.[pool.name] || "closed";
        return h("span", { className: `set-pill ${st === "open" ? "ok" : st === "stopped" ? "bad" : "warn"}`, textContent: `${pool.label} ${st}` });
      });
    const used = (t) => (t.used_at ? `used ${new Date(t.used_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "not used since start");
    if (!t) {
      card.append(
        h("span", { className: "set-name", textContent: "default" }),
        h("div", { className: "picks" }, h("span", { className: "set-none", textContent: "every member without a policy of its own" }), ...chips(this.defaultGates)),
        this.editor(key),
      );
      return card;
    }
    const member = t.role !== "admin";
    const own = member && this.pol[key] != null;
    const policyLink = member && button(own ? "Reset to default" : "Customize", null, () => this.customize(key, !own), "link");
    const revoke = button(this.armed === t.name ? "Click again to confirm" : t.owner ? "Revoke and regenerate" : "Revoke", null, () => this.revoke(t.name), "link danger");
    card.append(
      h("span", { className: "set-name", textContent: t.name }),
      h(
        "div",
        { className: "picks" },
        h("span", { className: "set-pill", textContent: t.role }),
        this.multiUser && h("span", { className: "set-pill set-uid", textContent: t.linux_user ? `uid ${t.linux_user}` : "server's user" }),
        ...(member ? chips(t.gates) : [h("span", { className: "set-pill", textContent: "never gated" })]),
        h("span", { className: "set-none set-used", textContent: used(t) }),
        h("span", { className: "set-gap" }),
        policyLink,
        revoke,
      ),
    );
    if (member) card.append(own ? this.editor(key) : h("div", { className: "pol" }, h("span", { className: "set-none", textContent: "uses the default" })));
    return card;
  }

  // A policy per pool: closed, open without lines, or lines on its windows.
  editor(key) {
    const pol = this.pol[key];
    return h(
      "div",
      { className: "pol" },
      ...this.pools.map((pool) => {
        const pp = pol[pool.name] || { mode: "closed" };
        const modes = pool.windows.length ? ["closed", "open", "lines"] : ["closed", "open"];
        const mode = select("mode", modes, pp.mode, (m) => (m === "open" ? "open, no lines" : m));
        mode.dataset.pool = pool.name;
        mode.setAttribute("aria-label", `${key === "*" ? "default" : key} ${pool.label} mode`);
        mode.addEventListener("change", () => {
          const cur = pol[pool.name] || {};
          if (mode.value === "lines") {
            const lines = cur.lines && Object.keys(cur.lines).length ? cur.lines : { [pool.windows[0].kind]: { launch: 50, stop: 90 } };
            pol[pool.name] = { mode: "lines", lines };
          } else pol[pool.name] = { mode: mode.value };
          this.touchPolicy(true);
        });
        const note =
          pp.mode !== "lines" &&
          h("span", { className: "wv", textContent: (pp.mode === "open" ? "may start at any reading" : "may not start harnesses here") + (pool.windows.length ? "" : "; no gauge, so no lines") });
        const rows = pp.mode === "lines" ? pool.windows.map((w) => this.winrow(key, pool, w, pp)) : [];
        return h("div", { className: `poolrow pool-${pool.name}` }, h("span", { className: "pl", textContent: pool.label }), h("div", {}, h("div", { className: "modeline" }, mode, note), ...rows));
      }),
    );
  }

  winrow(key, pool, w, pp) {
    pp.lines ||= {}; // a hand-edited "lines" with none: every window unused
    const l = pp.lines[w.kind];
    const rw = this.reading(pool.name)?.windows.find((x) => x.kind === w.kind);
    const pct = rw ? rw.percent : 0;
    const gone = rw ? elapsed(rw, Date.now() / 1000) : null;
    const check = (act, checked, disabled, onchange) => h("input", { type: "checkbox", checked, disabled, onchange, className: `set-${act}` });
    const use = check("use", !!l, false, (e) => {
      if (e.target.checked) pp.lines[w.kind] = { launch: 50, stop: 90 };
      else delete pp.lines[w.kind];
      this.touchPolicy(true);
    });
    const pace =
      w.pace &&
      h(
        "label",
        {},
        check("pace", l?.launch === "pace", !l, (e) => {
          l.launch = e.target.checked ? "pace" : Math.min(50, l.stop - 1);
          this.touchPolicy(true);
        }),
        "pace",
      );
    const name = h("div", { className: "wl" }, h("label", {}, use, w.label), pace);
    const track = this.fill("track", pct, null);
    Object.assign(track.dataset, { key, pool: pool.name, win: w.kind });
    if (l) {
      const handle = (which, at) => {
        const b = h("button", { type: "button", className: `h ${which === "launch" ? "l" : "s"}`, style: `left:${at}%` }, h("span", { textContent: at }));
        b.dataset.h = which;
        b.setAttribute("aria-label", `${w.label} ${which} line`);
        return b;
      };
      const lpos = l.launch === "pace" ? (gone ?? 0) * 100 : l.launch;
      track.append(h("span", { className: "zone closed", style: `left:${lpos}%;width:${Math.max(0, l.stop - lpos)}%` }), h("span", { className: "zone stop", style: `left:${l.stop}%;right:0` }));
      track.append(l.launch === "pace" ? h("span", { className: "pace", style: `left:${lpos}%` }, h("span", { textContent: "pace" })) : handle("launch", l.launch), handle("stop", l.stop));
    }
    return h("div", { className: `winrow win-${w.kind}${l ? "" : " off"}` }, name, track, h("span", { className: "wv" }, h("span", { className: "rd", textContent: l ? readout(l) : "not used" })));
  }

  // Drag a handle, or move it with the arrows (Shift: five points). The launch
  // line never reaches the stop line. Moves the handle in place, so a drag
  // and the focus survive; the save bar says the page is dirty.
  handles(root) {
    let drag = null;
    const at = (b) => ({ ...b.parentElement.dataset, which: b.dataset.h, track: b.parentElement });
    root.addEventListener("pointerdown", (e) => {
      const b = e.target.closest(".h");
      if (!b) return;
      drag = at(b);
      b.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    root.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const r = drag.track.getBoundingClientRect();
      this.setLine(drag, Math.round(((e.clientX - r.left) / r.width) * 100));
    });
    const end = () => {
      if (!drag) return;
      drag = null;
      this.touchPolicy(false);
    };
    root.addEventListener("pointerup", end);
    root.addEventListener("pointercancel", end);
    root.addEventListener("keydown", (e) => {
      const b = e.target.closest(".h");
      if (!b || !["ArrowLeft", "ArrowRight"].includes(e.key)) return;
      e.preventDefault();
      const d = at(b);
      const l = this.pol[d.key][d.pool].lines[d.win];
      this.setLine(d, l[d.which] + (e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 5 : 1));
      this.touchPolicy(false);
    });
  }

  setLine(d, v) {
    const l = this.pol[d.key][d.pool].lines[d.win];
    if (d.which === "launch") l.launch = Math.max(0, Math.min(v, l.stop - 1));
    else l.stop = Math.min(100, Math.max(v, l.launch === "pace" ? 1 : l.launch + 1));
    const tr = d.track;
    const pace = tr.querySelector(".pace");
    const lpos = l.launch === "pace" ? parseFloat(pace?.style.left) || 0 : l.launch;
    for (const [sel, val] of [[".h.l", l.launch], [".h.s", l.stop]]) {
      const b = tr.querySelector(sel);
      if (!b) continue;
      b.style.left = `${val}%`;
      b.firstChild.textContent = val;
    }
    const zc = tr.querySelector(".zone.closed");
    zc.style.left = `${lpos}%`;
    zc.style.width = `${Math.max(0, l.stop - lpos)}%`;
    tr.querySelector(".zone.stop").style.left = `${l.stop}%`;
    tr.closest(".winrow").querySelector(".rd").textContent = readout(l);
  }

  // Customize copies the default into the token; Reset drops its own.
  customize(key, on) {
    this.pol[key] = on ? structuredClone(this.pol["*"]) : null;
    this.touchPolicy(true);
  }

  touchPolicy(redraw) {
    this.status = "";
    if (redraw) this.drawTokens();
    this.drawBar();
  }

  // The policies whose edits are not saved: "*" is the default.
  policyChanges() {
    return Object.keys(this.pol || {}).filter((k) => JSON.stringify(this.pol[k]) !== this.polSaved[k]);
  }

  // Each change on its own, so one refused (a token another admin revoked)
  // keeps no other from saving; then the list as the server holds it.
  async savePolicies() {
    const failed = [];
    for (const name of this.policyChanges()) {
      try {
        await this.conn.call("token.policy", { name, policy: this.pol[name] });
      } catch (e) {
        failed.push(`${name === "*" ? "default" : name}: ${e.message}`);
      }
    }
    await this.loadTokens();
    if (failed.length) throw new Error(`Not saved: ${failed.join("; ")}`);
  }

  // The live quota, for the pools strip: redrawn alone, never under a drag.
  onQuota(q) {
    this.quota = q;
    const old = this.box.querySelector("#set-poolnow");
    if (old && this.pools) old.replaceWith(this.poolNow());
  }

  async loadTokens() {
    try {
      const r = await this.conn.call("token.list");
      this.tokenRows = r.tokens;
      this.multiUser = !!r.multi_user;
      this.pools = r.pools;
      this.defaultGates = r.default_gates;
      // What the server holds now; an unsaved edit survives an add or a revoke.
      const fresh = { "*": r.default_policy ?? {} };
      for (const t of r.tokens) if (t.role !== "admin") fresh[t.name] = t.policy;
      const keep = new Set(this.policyChanges());
      this.polSaved = Object.fromEntries(Object.entries(fresh).map(([k, v]) => [k, JSON.stringify(v)]));
      this.pol = Object.fromEntries(Object.entries(fresh).map(([k, v]) => [k, keep.has(k) ? this.pol[k] : structuredClone(v)]));
    } catch (e) {
      this.tokenError = e.message;
    }
    this.drawTokens();
    this.drawBar();
  }

  // Only the Tokens section: a whole-page draw would drop what is being typed
  // in the agents and queues (draw() waits for that; this need not).
  drawTokens() {
    const old = this.box.querySelector("#set-tokens");
    if (old) old.replaceWith(this.tokens());
  }

  async addToken(name, role, linuxUser) {
    this.tokenError = "";
    try {
      const r = await this.conn.call("token.add", { name, role, ...(linuxUser ? { linux_user: linuxUser } : {}) });
      this.tokenOnce = { text: `The token for ${r.name}, shown once: `, token: r.token };
      this.tokenName = "";
      this.tokenUser = null;
    } catch (e) {
      this.tokenError = e.message;
    }
    await this.loadTokens();
  }

  // The first click arms, the second revokes. Revoking the admin's own token
  // signs this browser in with the new one before the socket is dropped.
  async revoke(name) {
    if (this.armed !== name) {
      this.armed = name;
      return this.drawTokens();
    }
    this.armed = null;
    this.tokenError = "";
    try {
      const r = await this.conn.call("token.revoke", { name });
      if (r.token) {
        await fetch("/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ token: r.token }) });
        this.tokenOnce = { text: "The new admin token, shown once: ", token: r.token };
      }
    } catch (e) {
      this.tokenError = e.message;
    }
    await this.loadTokens();
  }

  async link(url, token, trusted) {
    this.linkError = "";
    try {
      await this.conn.call("link.add", { url, token, ...(trusted ? { trusted } : {}) });
    } catch (e) {
      this.linkError = e.message;
    }
    this.draw();
  }

  async unlink(name) {
    try {
      await this.conn.call("link.remove", { name });
    } catch (e) {
      this.linkError = e.message;
    }
    this.draw();
  }

  head() {
    const harnesses = h(
      "div",
      { className: "set-harnesses" },
      ...this.found.map((f) =>
        h(
          "span",
          { className: f.bin && !f.error ? "set-harness" : "set-harness off" },
          h("b", { textContent: f.label }),
          " ",
          f.bin ? (f.error ? "cannot run" : `${f.version.replace(/\s*\(.*\)$/, "")}, ${plural(f.models.length, "model")}`) : "not installed",
          this.versionTag(f),
        ),
      ),
    );
    return h(
      "header",
      { className: "set-head" },
      h("div", {}, h("h2", { textContent: "Settings" }), h("code", { id: "set-path", textContent: this.wire.path })),
      harnesses,
    );
  }

  // A harness outside the versions this release was tested with
  // (harness_versions.py): a tag in the warning colour, or "accepted" when the
  // form's accepted list has it; opening it shows the binary and the command.
  versionTag(f) {
    const v = f.verdict;
    if (!v || v.state === "in_range") return null;
    const ok = v.version && (this.doc.harness_versions?.accepted?.[v.harness] || []).includes(v.version);
    const word = ok ? "accepted" : { newer: "newer than tested", older: "older than tested", unknown: "version unknown" }[v.state] || "accepted";
    const range = v.min === v.max ? v.max : `${v.min} – ${v.max}`;
    const d = h("details", { className: ok ? "set-vtag" : "set-vtag warn" });
    d.dataset.harness = v.harness;
    d.append(
      h("summary", { textContent: word, title: `tested ${range}` }),
      h("div", { className: "set-vline" }, h("code", { textContent: v.path || "" }), " · ", h("code", { textContent: v.install })),
    );
    return d;
  }

  // What a launch on an untested harness version does (harness_versions:),
  // and the versions a person allowed for good.
  untested() {
    const hv = (this.doc.harness_versions ||= { person: "ask", agent: "warn", startup: "warn", accepted: {} });
    hv.accepted ||= {};
    const pickRow = (key, values, words, path) => {
      // Named hv-*: a bare "agent" would match the queues' agent pickers.
      const sel = select(`hv-${key}`, values, hv[key]);
      sel.setAttribute("aria-label", `Untested versions ${words}`);
      sel.dataset.key = path;
      sel.addEventListener("input", () => {
        hv[key] = sel.value;
        this.touch();
      });
      return h("div", { className: "set-queue-line" }, h("span", { className: "set-name", textContent: words }), sel);
    };
    const rows = [
      pickRow("person", ["ask", "warn", "off"], "when a person starts one", "harness_versions.person"),
      pickRow("agent", ["warn", "off"], "when an agent starts one", "harness_versions.agent"),
      pickRow("startup", ["warn", "off"], "at startup", "harness_versions.startup"),
    ];
    const accepted = Object.entries(hv.accepted).flatMap(([harness, versions]) =>
      versions.map((v) =>
        h(
          "div",
          { className: "set-queue-line set-accepted" },
          h("span", { className: "set-name", textContent: `${harness} ${v}` }),
          button("Remove", null, () => {
            hv.accepted[harness] = hv.accepted[harness].filter((x) => x !== v);
            if (!hv.accepted[harness].length) delete hv.accepted[harness];
            this.touch();
            this.draw();
          }, "link danger"),
        ),
      ),
    );
    const box = h("div", { className: "set-queue" }, ...rows);
    box.dataset.row = "harness_versions";
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Untested versions" }));
    const note = h("p", { className: "set-none", textContent: "A harness version outside the ones this aegis release was tested with: ask asks the person first, warn starts it with a notice in the transcript, off says nothing." });
    const list = accepted.length
      ? h("div", { className: "set-queues", id: "set-accepted" }, h("p", { className: "set-none", textContent: "Accepted for good:" }), ...accepted)
      : h("p", { className: "set-none", id: "set-accepted", textContent: "No version accepted for good." });
    list.dataset.key = "harness_versions.accepted";
    return h("section", { id: "set-untested" }, head, note, h("div", { className: "set-queues" }, box), list);
  }

  // The doctor: one line until it finds something, the full list on demand.
  checks() {
    const f = this.findings;
    const line = h("div", { className: "set-summary", id: "set-summary" });
    if (f === null) line.append(h("span", { textContent: "Not checked since you opened this page." }));
    else {
      const errors = f.filter((x) => x.level === "error").length;
      const warns = f.filter((x) => x.level === "warn").length;
      const what = !this.checked ? `Not saved: ${plural(errors, "problem")}` : errors || warns ? [errors && plural(errors, "error"), warns && plural(warns, "warning")].filter(Boolean).join(", ") : `All ${f.length} checks pass`;
      line.append(h("span", { className: errors ? "err" : warns ? "warn" : "ok", textContent: what }));
      if (this.checked)
        line.append(
          button(this.allChecks ? "Hide checks" : `Show all ${f.length} checks`, "set-all", () => {
            this.allChecks = !this.allChecks;
            this.draw();
          }, "link"),
        );
    }
    line.append(button("Run doctor", "set-doctor", () => this.runDoctor(), "btn"));
    // Every check when asked; otherwise mark() lists the problems that have
    // no card or row to sit on.
    const list = h("ul", { id: "set-findings", className: "set-findings" });
    if (this.allChecks && this.checked) list.append(...f.map((x) => this.findingItem(x)));
    return h("section", { className: "set-checks" }, line, list);
  }

  findingItem(x) {
    return h("li", { className: x.level }, h("code", { textContent: x.where }), h("span", { textContent: x.message }));
  }

  harnessLabel(x) {
    const f = this.found.find((f) => f.harness === x);
    const name = f ? f.label : x;
    return f && !f.bin ? `${name} (not installed)` : name;
  }

  modelOptions(harness) {
    const f = this.found.find((f) => f.harness === harness);
    return (f ? f.models : []).map((m) => ({ value: m.value, label: m.label, meta: modelMeta(m) }));
  }

  async loadModels() {
    const server = this.server;
    try {
      const m = await this.call("models.list");
      if (server !== this.server) return;
      this.models = m;
    } catch (e) {
      this.models = { error: e.message };
    }
    this.box.querySelector("#set-models")?.replaceWith(this.modelsView());
    for (const c of this.box.querySelectorAll(".set-casc")) c.draw(); // scores and cooling
  }

  // A model of a harness as models.list tells it (with when it stops
  // cooling), or as its catalog does until that answers.
  modelInfo(harness, value) {
    const same = (x) => x.value === value || x.resolved === value;
    return this.models?.models?.find((x) => x.harness === harness && same(x)) || this.found.find((f) => f.harness === harness)?.models.find(same);
  }

  // An agent's models in the order it tries them (its cascade): the first
  // at spawn, the next when one runs out of quota or its provider fails.
  // One model is written model:, two or more models: (config.py).
  cascade(a) {
    let list = a.models?.length ? [...a.models] : a.model ? [a.model] : [];
    const box = h("div", { className: "set-casc" });
    const rows = h("ul");
    const add = h("pick-chip", { name: "add-model", className: "pick" });
    add.setAttribute("aria-label", "Agent add model");
    add.setAttribute("placeholder", "Add model");
    add.addEventListener("input", (ev) => {
      ev.stopPropagation();
      if (add.value) set([...list, add.value]);
    });
    const info = (m) => this.modelInfo(a.harness, m);
    // Ranked before unranked, then Intelligence Index down, Coding Index
    // breaking ties: negative when p goes first.
    const key = (m) => ((x) => (x?.intelligence == null ? [0, 0, 0] : [1, x.intelligence, x.coding ?? 0]))(info(m));
    const order = (p, q) => {
      const [x, y] = [key(p), key(q)];
      return y[0] - x[0] || y[1] - x[1] || y[2] - x[2];
    };
    const sort = button("Sort by ranking", null, () => set([...list].sort(order)), "link set-casc-sort");
    const warn = h("p", { className: "set-casc-warn" });
    const attr = h("p", { className: "set-attr" });
    const set = (next, focus) => {
      list = next;
      a.models = list.length > 1 ? [...list] : [];
      a.model = list[0] ?? "";
      this.touch();
      box.draw();
      // The button pressed is gone with its row: the focus follows the model.
      if (focus) box.querySelector(`li[data-model="${CSS.escape(focus[0])}"] button[data-a=${focus[1]}]:not(:disabled)`)?.focus();
    };
    box.clear = () => {
      list = [];
      a.model = "";
      a.models = [];
      box.draw();
    };
    box.draw = () => {
      const score = (x) => (x?.intelligence == null ? "unranked" : x.intelligence.toFixed(1));
      const now = Date.now() / 1000;
      rows.replaceChildren(
        ...list.map((m, i) => {
          const x = info(m);
          const until = x?.cooling_until > now ? new Date(x.cooling_until * 1000).toTimeString().slice(0, 5) : "";
          const act = (kind, text, words, to) => {
            const b = button(text, null, () => set(to(), [m, kind]), "set-casc-btn");
            b.dataset.a = kind;
            b.setAttribute("aria-label", `${words} ${m}`);
            return b;
          };
          const swap = (j) => () => {
            const next = [...list];
            [next[i], next[j]] = [next[j], next[i]];
            return next;
          };
          const up = act("up", "↑", "Move up", swap(i - 1));
          const down = act("down", "↓", "Move down", swap(i + 1));
          const rm = act("rm", "×", "Remove", () => list.filter((_, k) => k !== i));
          up.disabled = i === 0;
          down.disabled = i === list.length - 1;
          rm.disabled = list.length === 1; // an agent runs at least one model
          const li = h(
            "li",
            { className: until ? "cool" : "", title: x?.label || m },
            h("span", { className: "i", textContent: i + 1 }),
            h("span", { className: "m", textContent: until ? `${m} · cooling until ${until}` : m }),
            h("span", { className: x?.intelligence == null ? "sc none" : "sc", textContent: score(x) }),
            h("span", { className: "mv" }, up, down, rm),
          );
          li.dataset.model = m;
          return li;
        }),
      );
      add.options = this.modelOptions(a.harness).filter((o) => !list.includes(o.value));
      add.value = "";
      sort.hidden = list.length < 2;
      // The first pair out of ranking order, with both scores.
      let w = "";
      for (let i = 0; i < list.length && !w; i++)
        for (let j = i + 1; j < list.length && !w; j++)
          if (order(list[j], list[i]) < 0) w = `${list[i]} (${score(info(list[i]))}) is above ${list[j]} (${score(info(list[j]))})`;
      warn.textContent = w;
      warn.hidden = !w;
      const m = this.models;
      attr.hidden = !m || !!m.error;
      if (m && !m.error) {
        const link = h("a", { href: m.source_url, textContent: "Artificial Analysis", target: "_blank", rel: "noopener" });
        attr.replaceChildren("Scores by ", link, m.refreshed ? `, refreshed ${m.refreshed}` : ", not refreshed yet");
      }
    };
    box.append(rows, h("div", { className: "set-casc-tools" }, add, sort), warn, attr);
    box.draw();
    return box;
  }

  // Every model the installed harnesses run, the ones agents use first:
  // filtered by harness and price, sorted by any column (models.list).
  modelsView() {
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Models" }));
    const box = h("section", { id: "set-models" }, head);
    const m = this.models;
    if (!m || m.error) {
      box.append(h("p", { className: "set-none", textContent: m ? m.error : "Loading…" }));
      return box;
    }
    const view = this.modelView;
    const label = (x) => this.found.find((f) => f.harness === x)?.label || x;
    const harnesses = [...new Set(m.models.map((x) => x.harness))];
    const chip = (name, words, options, key) => {
      const c = select(name, options.map((o) => o[0]), view[key], (v) => options.find((o) => o[0] === v)[1]);
      c.setAttribute("aria-label", words);
      c.addEventListener("input", () => {
        view[key] = c.value;
        rows.replaceWith((rows = this.modelRows(m.models, label)));
      });
      return c;
    };
    const tools = h(
      "div",
      { className: "set-mtools" },
      chip("models-harness", "Models harness", [["", "all harnesses"], ...harnesses.map((x) => [x, label(x)])], "harness"),
      chip("models-price", "Models price", [["", "any price"], ["free", "free only"]], "price"),
      h("span", { className: "set-none", textContent: "sort" }),
      chip("models-sort", "Models sort", [["use", "in use, then rank"], ["name", "name"], ["harness", "harness"], ["rank", "rank"], ["coding", "Coding Index"], ["intelligence", "Intelligence Index"], ["price", "price"]], "sort"),
    );
    let rows = this.modelRows(m.models, label);
    const link = h("a", { href: m.source_url, textContent: "Artificial Analysis", target: "_blank", rel: "noopener" });
    const attr = h("p", { className: "set-attr" }, "Scores by ", link, m.refreshed ? `, refreshed ${m.refreshed}` : ", not refreshed yet");
    box.append(tools, rows, attr);
    return box;
  }

  modelRows(models, label) {
    const { harness, price, sort } = this.modelView;
    // Unscored and unpriced models go last: scores are 0 to 100.
    const cost = (x) => (modelPrice(x) === "free" ? 0 : (x.price_out ?? x.price_in ?? 1e9));
    const down = (k) => (a, b) => (b[k] ?? -1) - (a[k] ?? -1);
    const by = {
      name: (a, b) => a.value.localeCompare(b.value),
      harness: (a, b) => label(a.harness).localeCompare(label(b.harness)),
      rank: (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9),
      coding: down("coding"),
      intelligence: down("intelligence"),
      price: (a, b) => cost(a) - cost(b),
    }[sort];
    // models.list's order is "in use, then rank"; every other sort keeps it for ties.
    const list = models.filter((x) => (!harness || x.harness === harness) && (!price || modelPrice(x) === "free"));
    if (by) list.sort(by);
    const cell = (cls, text) => h("span", { className: cls, textContent: text });
    const hd = h("div", { className: "set-mrow hd" }, ...["model", "harness", "rank", "coding", "intel.", "price in / out", "used by"].map((t, i) => cell(["nm", "h", "rk", "ci", "ii", "pr", "by"][i], t)));
    const row = (x) => {
      const used = x.used_by.map((u) => `${u.agent} #${u.at + 1}`).join(", ");
      const cool = x.cooling_until ? `cooling until ${new Date(x.cooling_until * 1000).toTimeString().slice(0, 5)}` : "";
      const r = h(
        "div",
        { className: x.used_by.length ? "set-mrow use" : "set-mrow", title: x.label },
        cell("nm", x.value),
        cell("h", label(x.harness)),
        cell("rk", x.rank ? `#${x.rank}` : "—"),
        cell("ci", x.coding != null ? x.coding.toFixed(1) : "—"),
        cell("ii", x.intelligence != null ? x.intelligence.toFixed(1) : "unranked"),
        cell("pr", modelPrice(x) || "—"),
        cell("by", [used, cool].filter(Boolean).join(" · ")),
      );
      r.dataset.model = x.value;
      return r;
    };
    const body = list.length ? list.map(row) : [h("p", { className: "set-none", textContent: "No model matches." })];
    return h("div", { className: "set-mrows" }, hd, ...body);
  }

  agents() {
    const v = this.wire.vocab;
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Agents" }), button("Add agent", "set-add-agent", () => {
      this.doc.agents.push({ name: "", harness: v.harnesses[0], model: "", models: [], effort: "", permission: "", priming: null, keep_harness_tools: [] });
      this.touch();
      this.draw();
      this.box.querySelector(".set-agent:last-of-type input[name=name]")?.focus();
    }, "link"));
    head.dataset.row = "default_agent";
    const cards = this.doc.agents.map((a, i) => this.agentCard(a, i, v));
    return h("section", {}, head, h("div", { className: "set-agents", id: "set-agents" }, ...cards));
  }

  agentCard(a, i, v) {
    const card = h("div", { className: "set-agent" });
    card.dataset.row = `agents.${a.name}`;
    const isDefault = this.doc.default_agent === a.name && a.name !== "";
    const name = h("input", { name: "name", value: a.name, spellcheck: false, placeholder: "name", className: "set-name" });
    const harness = select("harness", v.harnesses, a.harness, (x) => this.harnessLabel(x));
    const casc = this.cascade(a);
    const effort = select("effort", ["", ...v.efforts], a.effort, (x) => (x ? `effort ${x}` : "effort?"));
    const permission = select("permission", ["", ...v.permissions], a.permission, (x) => (x ? `perm ${x}` : "perm?"));
    const priming = h("textarea", { name: "priming", value: a.priming || "", rows: 4, placeholder: "A system prompt every session of this agent starts with." });
    // The key each control edits, which the coverage test looks for (#527).
    harness.dataset.key = "agents.*.harness";
    casc.dataset.key = "agents.*.model agents.*.models";
    effort.dataset.key = "agents.*.effort";
    permission.dataset.key = "agents.*.permission";
    priming.dataset.key = "agents.*.priming";
    for (const el of [name, harness, effort, permission, priming]) {
      el.setAttribute("aria-label", `Agent ${el.name}`);
      el.addEventListener("input", () => {
        const was = a.name;
        a[el.name] = el.name === "priming" ? el.value || null : el.value;
        if (el.name === "name") {
          card.dataset.row = `agents.${el.value}`;
          // A rename carries to the default and the queues that run it; its
          // place in the workflow pool moves once the name is changed.
          if (this.doc.default_agent === was) this.doc.default_agent = el.value;
          for (const q of this.doc.queues) if (q.agent === was) q.agent = el.value;
          this.refreshNames();
        }
        if (el.name === "harness") casc.clear();
        this.touch();
      });
    }
    // Moved on change, not on every key: a name typed through "total" or
    // another agent's name on its way would take that number over. The row
    // is renamed where it stands, so a click into it that ends the rename
    // still lands; a refused move redraws the rows, the old name a stray.
    let pooled = a.name;
    name.addEventListener("change", () => {
      const m = (this.doc.max_agents ||= {});
      const to = name.value;
      if (to === pooled) return;
      const row = this.box.querySelector(`#set-wf-pool [data-row="${CSS.escape(`workflows.${pooled}`)}"]`);
      if (row && to && to !== "total" && !(to in m)) {
        if (pooled in m) {
          m[to] = m[pooled];
          delete m[pooled];
        }
        row.dataset.row = `workflows.${to}`;
        row.querySelector(".set-name").textContent = to;
        row.querySelector("input").setAttribute("aria-label", `Workflow node sessions of ${to} at once`);
      } else this.box.querySelector("#set-wf-pool")?.replaceChildren(...this.poolRows());
      pooled = to;
    });
    const star = button(isDefault ? "default" : "make default", null, () => {
      this.doc.default_agent = a.name;
      this.touch();
      this.draw();
    }, isDefault ? "set-default on" : "set-default");
    star.disabled = isDefault || !a.name;
    star.dataset.key = "default_agent";
    const del = button("Remove", null, () => {
      this.doc.agents.splice(i, 1);
      if (this.doc.default_agent === a.name) this.doc.default_agent = null;
      if (this.doc.max_agents) delete this.doc.max_agents[a.name];
      this.touch();
      this.draw();
    }, "link danger");
    const fold = h("details", { className: "set-priming", open: !!a.priming }, h("summary", { textContent: a.priming ? "Priming" : "Add a priming" }), priming);
    card.append(name, h("div", { className: "picks" }, harness, effort, permission), casc, h("div", { className: "set-card-foot" }, fold, this.keepFold(a), this.exitFold(a), star, del));
    if (isDefault) card.classList.add("is-default");
    return card;
  }

  // The Claude Code tools aegis replaces that this agent keeps (agents.py
  // _keep): one toggle each.
  keepFold(a) {
    a.keep_harness_tools ||= [];
    const keep = a.keep_harness_tools;
    const tools = [...new Set([...(this.wire.vocab.tools || []), ...keep])];
    const summary = h("summary");
    const say = () => (summary.textContent = keep.length ? `Keeps ${keep.join(", ")}` : "Keep a Claude Code tool");
    say();
    const toggles = tools.map((t) => {
      const b = button(t, null, () => {
        const i = keep.indexOf(t);
        if (i < 0) keep.push(t);
        else keep.splice(i, 1);
        b.setAttribute("aria-pressed", String(i < 0));
        say();
        this.touch();
      }, "set-tool");
      b.setAttribute("aria-pressed", String(keep.includes(t)));
      return b;
    });
    const fold = h("details", { className: "set-priming set-keep", open: keep.length > 0 }, summary, h("p", { className: "set-none", textContent: "Claude Code sessions start without these, since aegis has its own; an agent whose work needs one keeps it." }), h("div", { className: "set-tools" }, ...toggles));
    fold.dataset.key = "agents.*.keep_harness_tools";
    return fold;
  }

  // The link this agent exits through (exit.py), or "off"; blank is the server's.
  exitFold(a) {
    const input = h("input", { className: "set-in", value: a.exit || "", spellcheck: false, placeholder: "the server's exit, or a link's name, or off" });
    input.addEventListener("input", () => {
      a.exit = input.value.trim();
      this.touch();
    });
    const fold = h("details", { className: "set-priming set-exit", open: !!a.exit }, h("summary", { textContent: a.exit ? `Exits through ${a.exit}` : "Exit through a link" }), input);
    fold.dataset.key = "agents.*.exit";
    return fold;
  }

  // The queues' agent pickers follow a rename in place. Redrawing the form
  // here would replace the Save button under a click that blurred the name.
  refreshNames() {
    const names = this.doc.agents.map((a) => a.name);
    this.box.querySelectorAll(".set-queue pick-chip[name=agent]").forEach((sel, i) => {
      sel.options = names;
      sel.value = this.doc.queues[i].agent;
    });
  }

  queues() {
    const names = this.doc.agents.map((a) => a.name);
    const rows = this.doc.queues.map((q, i) => {
      const row = h("div", { className: "set-queue" });
      row.dataset.row = `queues.${q.name}`;
      const name = h("input", { name: "name", value: q.name, spellcheck: false, placeholder: "queue", className: "set-name" });
      const agent = select("agent", names, q.agent);
      const limit = h("input", { name: "max_parallel", type: "number", min: 1, value: q.max_parallel ?? "", className: "pick set-limit" });
      // Left out reads as quiet: "" keeps a quiet: true the file spells out.
      const quiet = select("quiet", ["", "false"], q.quiet === false ? "false" : "", (x) => (x ? "with a tab" : "quiet"));
      quiet.setAttribute("aria-label", "Queue quiet");
      quiet.addEventListener("input", () => {
        q.quiet = quiet.value === "false" ? false : q.quiet === true ? true : null;
        this.touch();
      });
      agent.dataset.key = "queues.*.agent";
      limit.dataset.key = "queues.*.max_parallel";
      quiet.dataset.key = "queues.*.quiet";
      for (const el of [name, agent, limit]) {
        el.setAttribute("aria-label", `Queue ${el.name}`);
        el.addEventListener("input", () => {
          q[el.name] = el.name === "max_parallel" ? (el.value === "" ? null : Number(el.value)) : el.value;
          if (el.name === "name") row.dataset.row = `queues.${el.value}`;
          this.touch();
        });
      }
      const del = button("Remove", null, () => {
        this.doc.queues.splice(i, 1);
        this.touch();
        this.draw();
      }, "link danger");
      row.append(h("div", { className: "set-queue-line" }, name, h("span", { textContent: "runs" }), agent, limit, h("span", { textContent: "at a time" }), quiet, del));
      return row;
    });
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Queues" }), button("Add queue", "set-add-queue", () => {
      this.doc.queues.push({ name: "", agent: this.doc.default_agent || names[0] || "", max_parallel: null });
      this.touch();
      this.draw();
    }, "link"));
    const body = rows.length
      ? h("div", { className: "set-queues", id: "set-queues" }, ...rows)
      : h("p", { className: "set-none", id: "set-queues", textContent: "No queues. A queue hands tasks to fresh worker sessions of one agent." });
    return h("section", {}, head, body);
  }

  // Terminal profiles (term_profiles.py): a command, where it runs, when it
  // restarts, and its env and access maps as NAME=value lines.
  terminals() {
    this.doc.terminals ||= [];
    const restarts = this.wire.vocab?.restarts || ["no", "on-failure", "always"];
    const lines = (m) => Object.entries(m || {}).map(([k, v]) => `${k}=${v}`).join("\n");
    const parse = (text) =>
      Object.fromEntries(
        text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
          const i = l.indexOf("=");
          return i < 0 ? [l, ""] : [l.slice(0, i).trim(), l.slice(i + 1).trim()];
        }),
      );
    const keys = { cmd: "terminals.*.cmd", cwd: "terminals.*.cwd", restart: "terminals.*.restart", env: "terminals.*.env", access: "terminals.*.access" };
    const rows = this.doc.terminals.map((t, i) => {
      const row = h("div", { className: "set-queue" });
      row.dataset.row = `terminals.${t.name}`;
      const name = h("input", { name: "name", value: t.name, spellcheck: false, placeholder: "profile", className: "set-name" });
      const cmd = h("input", { name: "cmd", value: t.cmd, spellcheck: false, placeholder: "command", className: "pick set-cmd" });
      const cwd = h("input", { name: "cwd", value: t.cwd, spellcheck: false, placeholder: "cwd", className: "pick set-cwd" });
      const restart = select("restart", ["", ...restarts.filter((r) => r !== "no")], t.restart === "no" ? "" : t.restart, (x) => (x ? `restart ${x}` : "no restart"));
      const env = h("textarea", { name: "env", value: lines(t.env), rows: 2, spellcheck: false, placeholder: "NAME=value, one a line" });
      const access = h("textarea", { name: "access", value: lines(t.access), rows: 2, spellcheck: false, placeholder: "name=level, one a line" });
      for (const el of [name, cmd, cwd, restart, env, access]) {
        el.setAttribute("aria-label", `Terminal ${el.name}`);
        if (keys[el.name]) el.dataset.key = keys[el.name];
        el.addEventListener("input", () => {
          t[el.name] = el.name === "env" || el.name === "access" ? parse(el.value) : el.value;
          if (el.name === "name") row.dataset.row = `terminals.${el.value}`;
          this.touch();
        });
      }
      const del = button("Remove", null, () => {
        this.doc.terminals.splice(i, 1);
        this.touch();
        this.draw();
      }, "link danger");
      const more = h("details", { className: "set-priming", open: !!(Object.keys(t.env).length || Object.keys(t.access).length) }, h("summary", { textContent: "Environment and access" }), env, access);
      row.append(h("div", { className: "set-queue-line" }, name, cmd, h("span", { textContent: "in" }), cwd, restart, del), more);
      return row;
    });
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Terminals" }), button("Add terminal", "set-add-terminal", () => {
      this.doc.terminals.push({ name: "", cmd: "", cwd: ".", restart: "", env: {}, access: {} });
      this.touch();
      this.draw();
    }, "link"));
    const body = rows.length
      ? h("div", { className: "set-queues", id: "set-terminals" }, ...rows)
      : h("p", { className: "set-none", id: "set-terminals", textContent: "No terminal profiles. The + view still offers a login shell." });
    return h("section", {}, head, body);
  }

  // A section drawn from the wire's schema (config.py fields()): a row per
  // key, blank for its default, which the placeholder shows. Each block is
  // one box, which a finding on it marks.
  fieldSection(id) {
    const sec = this.wire.sections?.[id];
    const fields = (this.wire.schema || []).filter((f) => f.section === id);
    if (!sec || !fields.length) return null;
    const s = (this.doc.settings ||= {});
    const boxes = new Map();
    // A key under a switch that is off has no row: it is not read.
    const off = (k) => k.split(".").some((_, i, parts) => i > 0 && s[parts.slice(0, i).join(".")] === false);
    for (const f of fields.filter((f) => !off(f.key))) {
      const block = f.key.split(".")[0];
      if (!boxes.has(block)) {
        const b = h("div", { className: "set-queue" });
        b.dataset.row = block;
        boxes.set(block, b);
      }
      boxes.get(block).append(this.fieldRow(f, s, sec.title, () => this.box.querySelector(`#set-${id}`)?.replaceWith(this.fieldSection(id))));
    }
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: sec.title }));
    return h("section", { id: `set-${id}` }, head, h("p", { className: "set-none", textContent: sec.note }), h("div", { className: "set-queues" }, ...boxes.values()));
  }

  fieldRow(f, s, title, redraw) {
    const k = f.key;
    const words = (k.includes(".") ? k.slice(k.indexOf(".") + 1) : k).replaceAll(/[._]/g, " ");
    const shown = (v) => (Array.isArray(v) ? v.join(", ") : v === false ? "off" : v == null ? "" : String(v));
    const set = (v) => {
      if (v === undefined) delete s[k];
      else s[k] = v;
      this.touch();
    };
    let el;
    if (f.kind === "agent") {
      const names = this.doc.agents.filter((a) => a.harness === "claude-code").map((a) => a.name);
      el = select(k, ["", ...names], s[k] || "", (x) => x || "off");
      el.addEventListener("input", () => set(el.value || undefined));
    } else if (f.kind === "switch") {
      el = select(k, ["", "off"], s[k] === false ? "off" : "", (x) => x || "on");
      el.addEventListener("input", () => {
        set(el.value === "off" ? false : undefined);
        redraw(); // the keys under it turn on or off
      });
    } else if (f.kind === "bool") {
      el = select(k, ["", "true", "false"], k in s ? String(s[k]) : "", (x) => x || `default, ${f.default}`);
      el.addEventListener("input", () => set(el.value ? el.value === "true" : undefined));
    } else {
      const number = (f.kind === "int" || f.kind === "number") && !f.off;
      el = h("input", { type: number ? "number" : "text", step: "any", value: shown(s[k]), placeholder: shown(f.default), spellcheck: false, className: f.kind === "list" || f.kind === "text" ? "pick set-cmd" : "pick set-num" });
      if (f.off) el.title = "a number, or off";
      el.addEventListener("input", () => {
        const v = el.value.trim();
        if (v === "") return set(undefined);
        if (f.off && (v === "off" || v === "false")) return set(false);
        if (f.kind === "list") return set(v.split(/[,\s]+/).filter(Boolean));
        // A value that is not a number goes as typed, for the server to name.
        if (f.kind === "int" || f.kind === "number") return set(Number.isFinite(Number(v)) ? Number(v) : v);
        set(v);
      });
    }
    el.setAttribute("aria-label", `${title} ${words}`);
    el.dataset.key = k;
    const row = h("div", { className: "set-queue-line set-field" }, h("span", { className: "set-name", textContent: words }), el);
    if (f.kind === "switch") row.classList.add("set-switch");
    return row;
  }

  // One number per harness: the minutes a session of it must be safe to end
  // before aegis ends its process (park.py). Blank is off.
  park() {
    const harnesses = this.wire.vocab?.harnesses || ["claude-code", "opencode", "codex"];
    this.doc.park ||= {};
    const rows = harnesses.map((name) => {
      const mins = this.doc.park[name] ? Math.round(this.doc.park[name] / 60) : "";
      const input = h("input", { name, type: "number", min: 1, value: mins, className: "pick set-limit", placeholder: "off" });
      input.setAttribute("aria-label", `Park ${name} sessions after minutes`);
      input.dataset.key = "park.*";
      input.addEventListener("input", () => {
        if (input.value === "") delete this.doc.park[name];
        else this.doc.park[name] = Math.round(Math.max(1, Number(input.value)) * 60);
        this.touch();
      });
      const row = h("div", { className: "set-queue" });
      row.dataset.row = `park.${name}`;
      row.append(h("div", { className: "set-queue-line" }, h("span", { className: "set-name", textContent: name }), h("span", { textContent: "parks after" }), input, h("span", { textContent: "min idle" })));
      return row;
    });
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Parking" }));
    const note = h("p", { className: "set-none", textContent: "An idle session that nothing needs gives its harness's memory back, and the next message resumes it. Blank keeps every session of that harness running." });
    return h("section", {}, head, note, h("div", { className: "set-queues", id: "set-park" }, ...rows));
  }

  // Workflows (workflows.py): the server's pool, which Save writes into
  // .aegis.yaml as it writes the rows above, and the saved workflows, whose
  // Import and Export act on their files at once (workflow_store.py).
  workflows() {
    const file = h("input", { type: "file", id: "set-wf-import", accept: ".yaml,.yml", hidden: true });
    file.addEventListener("change", () => file.files[0] && this.importWorkflow(file.files[0]).finally(() => (file.value = "")));
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Workflows" }), button("Import…", "set-wf-import-btn", () => file.click()), file);
    const note = h("p", { className: "set-none", textContent: "The pool bounds the node sessions every workflow on this server has open at once; blank is the default, 4 in all, and an agent left blank is bound by the total. Saved workflows are files in .aegis/workflows beside .aegis.yaml: Import and Export act on them at once, without Save changes." });
    if (this.savedWf === undefined) this.loadSaved();
    const pool = h("div", { className: "set-queues", id: "set-wf-pool" }, ...this.poolRows());
    pool.dataset.key = "workflows.max_agents";
    return h("section", { id: "set-workflows" }, head, note, pool, h("div", { id: "set-wf-status" }, ...this.wfStatusNodes()), h("div", { id: "set-wf-list" }, ...this.savedRows()));
  }

  // A row per agent, keyed by its data-row, which an agent's rename moves.
  poolRows() {
    this.doc.max_agents ||= {};
    const m = this.doc.max_agents;
    const limit = (key, label, placeholder) => {
      const input = h("input", { name: key, type: "number", min: 1, value: m[key] ?? "", className: "pick set-limit", placeholder });
      input.setAttribute("aria-label", label);
      const row = h("div", { className: "set-queue" });
      row.dataset.row = `workflows.${key}`;
      input.addEventListener("input", () => {
        const at = row.dataset.row.slice("workflows.".length); // renamed in place
        if (input.value === "") delete m[at];
        else m[at] = Math.max(1, Math.round(Number(input.value)));
        this.touch();
      });
      row.append(h("div", { className: "set-queue-line" }, h("span", { className: "set-name", textContent: key === "total" ? "all agents" : key }), h("span", { textContent: "at most" }), input, h("span", { textContent: "node sessions" })));
      return row;
    };
    // A number for a name that is no agent (a hand edit, or a rename made
    // elsewhere) would refuse every save: it gets a row to clear it.
    const names = new Set(this.doc.agents.map((a) => a.name));
    const stray = Object.keys(m).filter((k) => k !== "total" && !names.has(k)).map((k) => {
      const row = h("div", { className: "set-queue" });
      row.dataset.row = `workflows.${k}`;
      row.append(h("div", { className: "set-queue-line" }, h("span", { className: "set-name", textContent: k }), h("span", { textContent: `at most ${m[k]}: not an agent` }), button("Clear", null, () => {
        delete m[k];
        row.remove();
        this.touch();
      }, "link danger")));
      return row;
    });
    return [limit("total", "Workflow node sessions at once, all agents", "4"), ...this.doc.agents.map((a) => limit(a.name, `Workflow node sessions of ${a.name} at once`, "total")), ...stray];
  }

  // The usage: block (usage/owners.py): who owns a local repo, how an owner is
  // shown, and each subscription's name and monthly fee. Each map's rows write
  // the whole map back, so a row may be renamed in place.
  usage() {
    const u = (this.doc.usage ||= {});
    const map = (path, label, add, cols) => {
      const key = path.slice("usage.".length);
      u[key] ||= {};
      const list = Object.entries(u[key]).map(([k, v]) => ({ k, v }));
      const sync = () => {
        u[key] = Object.fromEntries(list.filter((r) => r.k).map((r) => [r.k, r.v]));
        this.touch();
      };
      const box = h("div", { className: "set-queues", id: `set-usage-${key}` });
      box.dataset.key = path;
      const rowOf = (r) => {
        const row = h("div", { className: "set-queue" });
        const name = h("input", { value: r.k, spellcheck: false, placeholder: cols[0], className: "set-name" });
        name.setAttribute("aria-label", `${label} ${cols[0]}`);
        name.addEventListener("input", () => {
          r.k = name.value.trim();
          sync();
        });
        const del = button("Remove", null, () => {
          list.splice(list.indexOf(r), 1);
          row.remove();
          sync();
        }, "link danger");
        row.append(h("div", { className: "set-queue-line" }, name, ...cols.slice(1).map((c) => this.usageValue(key, r, c, label, sync)), del));
        return row;
      };
      box.append(...list.map(rowOf));
      const more = button(add, `set-usage-add-${key}`, () => {
        const r = { k: "", v: key === "subscriptions" ? {} : "" };
        list.push(r);
        box.append(rowOf(r));
        box.lastChild.querySelector("input").focus();
      }, "link");
      return h("div", {}, h("div", { className: "set-section-head" }, h("h4", { textContent: label }), more), box);
    };
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Usage" }));
    const note = h("p", { className: "set-none", textContent: "How aegis usage and the usage page split the work. Owners name who owns each local repo, by its directory name; owner names are how each owner is shown. Subscriptions give an account, the part after the colon in aegis usage --split billing, a name and a monthly fee in USD, which is shared over the work that ran on it." });
    return h("section", { id: "set-usage" }, head, note,
      map("usage.owners", "Owners", "Add owner", ["repo", "owner"]),
      map("usage.owner_names", "Owner names", "Add owner name", ["owner", "shown as"]),
      map("usage.subscriptions", "Subscriptions", "Add subscription", ["account", "name", "fee"]));
  }

  // A value cell of a usage row: a name, or a subscription's name or fee.
  usageValue(key, r, col, label, sync) {
    const sub = key === "subscriptions";
    const fee = sub && col === "fee";
    const now = sub ? (fee ? r.v.fee : r.v.name) : r.v;
    const input = h("input", { type: fee ? "number" : "text", min: 0, step: "any", value: now ?? "", spellcheck: false, placeholder: fee ? "USD a month" : col, className: fee ? "pick set-limit" : "pick set-cmd" });
    input.setAttribute("aria-label", `${label} ${col}`);
    input.addEventListener("input", () => {
      const v = input.value.trim();
      if (!sub) r.v = v;
      else if (fee) {
        if (v === "") delete r.v.fee;
        else r.v.fee = Number(v);
      } else r.v.name = v;
      sync();
    });
    return input;
  }

  async loadSaved() {
    this.savedWf = null;
    this.savedWf = await this.call("workflow.list").catch(() => []);
    this.drawSaved();
  }

  // Only the list: a whole-page draw would drop what is being typed.
  drawSaved() {
    this.box.querySelector("#set-wf-list")?.replaceChildren(...this.savedRows());
  }

  savedRows() {
    return (this.savedWf || []).map((w) => {
      const by = w.saved_by;
      const who = w.source === "builtin" ? "built in" : by ? `saved by ${by.handle || by.principal} ${elapsed(w.saved_at)}` : "saved by hand";
      const row = h("div", { className: "set-wf" });
      row.dataset.wf = w.id;
      const steps = h("ul", { className: "set-wf-steps", hidden: true });
      const show = button("Steps", null, async () => {
        if (steps.hidden && !steps.childElementCount) {
          const r = await this.call("workflow.read", { id: w.id });
          steps.replaceChildren(...(r.steps || []).map((s) => h("li", { textContent: s.say, style: `padding-left:${s.depth * 14}px` })));
        }
        steps.hidden = !steps.hidden;
      });
      const exp = button("Export", null, () => this.exportWorkflow(w.id));
      row.append(
        h("div", { className: "set-wf-line" }, h("span", { className: "set-name", textContent: w.title || w.id }), h("code", { textContent: w.id }), h("span", { className: "set-none", textContent: who }), h("span", { className: "set-wf-acts" }, show, exp)),
        w.error ? h("p", { className: "set-why error", textContent: w.error }) : "",
        steps,
      );
      return row;
    });
  }

  async exportWorkflow(id) {
    const r = await this.call("workflow.read", { id });
    const a = h("a", { href: URL.createObjectURL(new Blob([r.text], { type: "application/yaml" })), download: `${id}.yaml` });
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 0);
  }

  // The last import's outcome, kept so a redraw of the page does not drop it.
  wfStatusNodes() {
    if (!this.wfStatus) return [];
    const { msg, lines } = this.wfStatus;
    return [h("p", { className: lines.length ? "set-why error" : "set-none", textContent: msg }), ...lines.map((l) => h("pre", { className: "set-wf-err", textContent: l }))];
  }

  // Linted first, so a file that does not lint writes nothing and shows its
  // errors; the save lints again on the server, as the person's own save.
  async importWorkflow(file) {
    const text = await file.text();
    const status = (msg, lines = []) => {
      this.wfStatus = { msg, lines };
      this.box.querySelector("#set-wf-status")?.replaceChildren(...this.wfStatusNodes());
    };
    try {
      const lint = await this.call("workflow.lint", { definition: text });
      if (lint.errors.length) return status(`${file.name} does not lint, so nothing was imported:`, lint.errors);
      const have = (this.savedWf || []).find((w) => w.id === lint.id);
      if (have?.source === "project" && !(await ask(`Replace the saved workflow ${lint.id}?`, { ok: "Replace" }))) return;
      await this.call("workflow.save", { definition: text });
      status(`Imported ${lint.id}.`);
    } catch (e) {
      status(`${file.name} was not imported:`, [e.message || String(e)]);
    }
    this.loadSaved();
  }

  bar() {
    const b = h("div", { className: "set-bar", id: "set-bar" });
    this.barEl = b;
    this.drawBar();
    return b;
  }

  drawBar() {
    const b = this.barEl;
    if (!b) return;
    const dirty = this.dirty || this.policyChanges().length > 0;
    const status = this.status || (dirty ? "Unsaved changes" : "");
    b.classList.toggle("dirty", dirty);
    const save = button("Save changes", "set-save", () => !offline() && this.save(), "btn primary needs-net");
    save.disabled = !dirty || this.saving;
    const discard = button("Discard", "set-discard", () => {
      this.load(this.wire);
      for (const k of this.policyChanges()) this.pol[k] = JSON.parse(this.polSaved[k]);
      this.findings = null;
      this.status = "";
      this.draw();
    }, "btn");
    discard.hidden = !dirty;
    b.replaceChildren(h("span", { id: "set-status", textContent: status }), discard, save);
    markNeedsNet(b);
  }

  // Each finding marks the card or row Python named; a problem with none to
  // sit on (the file, a harness, the state) goes in the list under the summary.
  mark() {
    if (!this.findings) return;
    const list = this.box.querySelector("#set-findings");
    for (const f of this.findings) {
      if (f.level === "ok") continue;
      const els = f.row ? this.box.querySelectorAll(`[data-row="${CSS.escape(f.row)}"]`) : [];
      for (const el of els) {
        if (!el.classList.contains("error")) el.classList.add(f.level);
        el.append(h("p", { className: `set-why ${f.level}`, textContent: f.message }));
      }
      if (!els.length && !(this.allChecks && this.checked)) list.append(this.findingItem(f));
    }
  }

  // The config first, then the policies (this server's, through token.policy),
  // which wait for a config the server accepted.
  async save() {
    this.saving = true;
    this.drawBar();
    try {
      const r = this.dirty ? await this.call("config.write", { doc: this.doc, stamp: this.stamp }) : { saved: true };
      if (r.saved) {
        if (r.config) {
          this.wire = r.config;
          this.load(r.config);
        }
        this.findings = null;
        if (this.policyChanges().length) await this.savePolicies();
        this.status = "Saved";
      } else {
        this.findings = r.problems;
        this.checked = false;
        this.status = `Not saved: ${plural(r.problems.length, "problem")}`;
      }
    } catch (e) {
      if (e.code === "stale") this.stale = true;
      this.status = e.message;
    } finally {
      this.saving = false;
    }
    this.draw();
  }

  async runDoctor() {
    const b = document.getElementById("set-doctor");
    if (b) {
      b.disabled = true;
      b.textContent = "Checking…";
    }
    try {
      this.findings = await this.call("config.doctor");
      this.checked = true;
    } catch (e) {
      this.status = e.message;
    }
    this.draw();
  }

  empty() {
    const usable = this.found.filter((f) => f.bin && !f.error);
    const found = this.found.length
      ? usable.length
        ? `aegis found ${usable.map((f) => `${f.label} ${f.version.replace(/\s*\(.*\)$/, "")}`).join(" and ")} on this machine.`
        : "aegis found no harness on this machine. Install Claude Code or OpenCode, then reload this page."
      : "Looking for the harnesses installed on this machine…";
    const setup = button("Set up from what is installed", "set-setup", () => this.setup(), "btn primary");
    setup.disabled = !usable.length;
    return h(
      "div",
      { className: "set-empty" },
      h("h2", { textContent: "No configuration yet" }),
      h("p", {}, "There is no ", h("code", { textContent: ".aegis.yaml" }), ` in ${this.wire.root}. ${found}`),
      setup,
      h("p", { className: "set-none", textContent: "You can change every value before it is saved." }),
    );
  }

  async setup() {
    try {
      this.doc = await this.call("config.propose");
    } catch (e) {
      this.status = e.message;
      return this.draw();
    }
    this.stamp = null;
    this.touch();
    this.draw();
  }
}
