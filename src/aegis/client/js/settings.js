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
// (localStorage), so it is not part of the form and Save does not write it.
//
// The Servers section lists the servers this one links (links.py), and a
// picker points the form at a linked server's .aegis.yaml: the same `config.*`
// operations and `config` channel, sent through the link.

import { elapsed } from "./gauges.js";
import "./pick.js";
import { who } from "./me.js";

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
  constructor(conn, box, { themes, setTheme }) {
    this.conn = conn;
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
    if (this.server && !links.some((l) => l.name === this.server)) this.pick(null);
    else if (this.box.isConnected && this.box.offsetParent !== null && !this.dirty) this.draw();
  }

  // Point the form at a server: its config replaces the form, edits and all.
  pick(server) {
    if (server === this.server) return;
    this.unsubFar?.();
    this.unsubFar = null;
    this.server = server;
    this.wire = null;
    this.doc = null;
    this.dirty = false;
    this.findings = null;
    this.found = [];
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
  // one keeps its edits and says the file moved.
  onConfig(wire) {
    this.wire = wire;
    if (this.saving) return;
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
    if (this.found.length) return;
    try {
      this.found = await this.call("config.detect");
    } catch (e) {
      this.status = e.message;
    }
    if (!this.dirty) this.draw();
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
          h("p", { className: "notice", id: "set-member", textContent: "Server settings (agents, queues, servers, tokens) are edited by an admin." }),
          this.browser(),
          this.checks(),
        ),
      );
    if (this.tokenRows === undefined) {
      this.tokenRows = null;
      this.loadTokens();
    }
    const w = this.wire;
    // The title first, then what this browser keeps, then the servers; the
    // loading and no-config pages keep the same width and order.
    const page = (...kids) => h("div", { className: "set-page" }, ...kids, this.browser(), this.servers(), this.tokens());
    if (!w || !this.doc) return this.box.replaceChildren(page(h("p", { className: "notice", textContent: "Loading…" })));
    if (!w.exists && !this.dirty) return this.box.replaceChildren(page(this.empty()));
    const full = h(
      "div",
      { className: "set-page" },
      this.head(),
      this.browser(),
      this.servers(),
      this.tokens(),
      w.error && h("p", { className: "set-alert err", id: "set-error", textContent: `The file on disk does not parse, so aegis is still using the last version that did. ${w.error}` }),
      this.stale &&
        h(
          "p",
          { className: "set-alert", id: "set-stale", textContent: "The file changed on disk since you opened it. " },
          button("Reload from disk", "set-reload", () => {
            this.load(this.wire);
            this.findings = null;
            this.draw();
          }),
        ),
      this.checks(),
      this.agents(),
      this.queues(),
    );
    this.box.replaceChildren(full, this.bar());
    this.mark();
  }

  // What this browser alone keeps. Built once and moved into each draw, with
  // the chip set to the theme in use.
  browser() {
    if (!this.browserNode) {
      this.themeChip = h("pick-chip", { id: "set-theme", name: "theme", className: "pick" });
      this.themeChip.setAttribute("aria-label", "Theme");
      this.themeChip.options = this.themes;
      this.themeChip.addEventListener("change", () => this.setTheme(this.themeChip.value));
      this.browserNode = h(
        "section",
        { className: "set-browser", id: "set-browser" },
        h("h3", { textContent: "This browser" }),
        h("div", { className: "set-browser-row" }, h("span", { className: "set-browser-k", textContent: "Theme" }), this.themeChip),
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
    return this.browserNode;
  }

  // The servers: this one, each link with its state, and a form to add one.
  servers() {
    const since = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const state = (l) =>
      l.state === "linked"
        ? `linked${l.rtt_ms != null ? ` · ${l.rtt_ms} ms` : ""}`
        : l.state === "offline"
          ? `offline since ${since(l.since)}`
          : `${l.state}${l.error ? `: ${l.error}` : ""}`;
    const rows = [
      h("tr", {}, h("td", {}, h("b", { textContent: this.home })), h("td", { className: "m", textContent: "this server" }), h("td", {}, h("span", { className: "set-pill ok", textContent: "serving" })), h("td")),
      ...this.links.map((l) =>
        h(
          "tr",
          { className: `set-link link-${l.name}` },
          h("td", {}, h("b", { textContent: l.name })),
          h("td", { className: "m", textContent: l.url }),
          h("td", {}, h("span", { className: `set-pill ${l.state === "linked" ? "ok" : "bad"}`, textContent: state(l) })),
          h("td", {}, button("Remove", null, () => this.unlink(l.name))),
        ),
      ),
    ];
    const url = h("input", { className: "set-in", id: "set-link-url", placeholder: "https://dev.example", spellcheck: false });
    const token = h("input", { className: "set-in", id: "set-link-token", placeholder: "its token; kept in links.json, never in .aegis.yaml", type: "password" });
    const add = button("Link", "set-link-add", () => this.link(url.value.trim(), token.value.trim()), "btn primary");
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
      h("p", { className: "set-none", textContent: "A linked server's sessions show in the Fleet and the tab bar. Nothing on it can reach this machine." }),
      h("table", { className: "tbl" }, ...rows),
      h("div", { className: "set-linkrow" }, url, token, add),
      this.linkError && h("p", { className: "err-text", id: "set-link-error", textContent: this.linkError }),
      picker,
    );
  }

  // Who may use this server (tokens.py) and how much of each pool its members
  // may spend (quota_gate.py): the pools now, the default policy, a card per
  // token, and a form to mint one, shown once. Policy edits go out with the
  // save bar; adding and revoking act at once, because each shows or kills a
  // secret. The chips are the server's verdict on the saved policies.
  tokens() {
    const name = h("input", { className: "set-in", id: "set-token-name", placeholder: "name, e.g. ben or laptop", spellcheck: false, value: this.tokenName || "", oninput: (e) => (this.tokenName = e.target.value) });
    const role = select("role", ["member", "admin"], this.tokenRole || "member");
    role.id = "set-token-role";
    role.addEventListener("change", () => (this.tokenRole = role.value));
    const add = button("Add token", "set-token-add", () => this.addToken(name.value.trim(), role.value), "btn primary");
    const once = this.tokenOnce;
    const section = h(
      "section",
      { className: "set-tokens", id: "set-tokens" },
      h("h3", { textContent: "Tokens" }),
      h("p", { className: "set-none", textContent: "Who may use this server, and how much of each pool a member may spend. A member reads every session and changes only its own; an admin changes anything and is never gated." }),
      this.pools && this.poolNow(),
      this.pools && h("div", { className: "set-agents", id: "set-tokcards" }, this.tokenCard(null), ...(this.tokenRows || []).map((t) => this.tokenCard(t))),
      h("div", { className: "set-linkrow" }, name, role, add),
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

  async savePolicies() {
    for (const name of this.policyChanges()) await this.conn.call("token.policy", { name, policy: this.pol[name] });
    await this.loadTokens();
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

  async addToken(name, role) {
    this.tokenError = "";
    try {
      const r = await this.conn.call("token.add", { name, role });
      this.tokenOnce = { text: `The token for ${r.name}, shown once: `, token: r.token };
      this.tokenName = "";
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

  async link(url, token) {
    this.linkError = "";
    try {
      await this.conn.call("link.add", { url, token });
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
    return (f ? f.models : []).map((m) => ({ value: m.value, label: m.free ? `${m.label} (free)` : m.label }));
  }

  agents() {
    const v = this.wire.vocab;
    const head = h("div", { className: "set-section-head" }, h("h3", { textContent: "Agents" }), button("Add agent", "set-add-agent", () => {
      this.doc.agents.push({ name: "", harness: v.harnesses[0], model: "", effort: "", permission: "", priming: null });
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
    const model = h("pick-chip", { name: "model", className: "pick" });
    model.setAttribute("free", "");
    model.options = this.modelOptions(a.harness);
    model.value = a.model;
    const effort = select("effort", ["", ...v.efforts], a.effort, (x) => (x ? `effort ${x}` : "effort?"));
    const permission = select("permission", ["", ...v.permissions], a.permission, (x) => (x ? `perm ${x}` : "perm?"));
    const priming = h("textarea", { name: "priming", value: a.priming || "", rows: 4, placeholder: "A system prompt every session of this agent starts with." });
    for (const el of [name, harness, model, effort, permission, priming]) {
      el.setAttribute("aria-label", `Agent ${el.name}`);
      el.addEventListener("input", () => {
        const was = a.name;
        a[el.name] = el.name === "priming" ? el.value || null : el.value;
        if (el.name === "name") {
          card.dataset.row = `agents.${el.value}`;
          // A rename carries to the default and the queues that run it.
          if (this.doc.default_agent === was) this.doc.default_agent = el.value;
          for (const q of this.doc.queues) if (q.agent === was) q.agent = el.value;
          this.refreshNames();
        }
        if (el.name === "harness") model.options = this.modelOptions(el.value);
        this.touch();
      });
    }
    const star = button(isDefault ? "default" : "make default", null, () => {
      this.doc.default_agent = a.name;
      this.touch();
      this.draw();
    }, isDefault ? "set-default on" : "set-default");
    star.disabled = isDefault || !a.name;
    const del = button("Remove", null, () => {
      this.doc.agents.splice(i, 1);
      if (this.doc.default_agent === a.name) this.doc.default_agent = null;
      this.touch();
      this.draw();
    }, "link danger");
    const fold = h("details", { className: "set-priming", open: !!a.priming }, h("summary", { textContent: a.priming ? "Priming" : "Add a priming" }), priming);
    card.append(name, h("div", { className: "picks" }, harness, model, effort, permission), h("div", { className: "set-card-foot" }, fold, star, del));
    if (isDefault) card.classList.add("is-default");
    return card;
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
      row.append(h("div", { className: "set-queue-line" }, name, h("span", { textContent: "runs" }), agent, limit, h("span", { textContent: "at a time" }), del));
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
    const save = button("Save changes", "set-save", () => this.save(), "btn primary");
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
