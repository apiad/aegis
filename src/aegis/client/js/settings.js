// Settings: .aegis.yaml as a form. The server holds the file's parsed copy and
// sends it on the `config` channel; Save writes the whole form back through
// `config.write`, which refuses a stale write and validates first. The server
// re-reads the file after any write, so the page never patches server state.
// Nothing here decides what is valid: every finding names the row it marks.
//
// An agent is drawn as a card whose chips are the new-tab composer's, so a
// preset looks like the session it starts.
//
// The Servers section lists the servers this one links (links.py), and a
// picker points the form at a linked server's .aegis.yaml: the same `config.*`
// operations and `config` channel, sent through the link.

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
  const s = h("select", { name, className: "pick" });
  for (const v of values) s.append(new Option(label(v), v));
  if (value && !values.includes(value)) s.append(new Option(`${value} (unknown)`, value));
  s.value = value || "";
  return s;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export class Settings {
  constructor(conn, box) {
    this.conn = conn;
    this.box = box;
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
    const w = this.wire;
    if (!w || !this.doc)
      return this.box.replaceChildren(this.servers(), h("p", { className: "notice", textContent: "Loading…" }));
    if (!w.exists && !this.dirty) return this.box.replaceChildren(this.servers(), this.empty());
    const page = h(
      "div",
      { className: "set-page" },
      this.servers(),
      this.head(),
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
    this.box.replaceChildren(page, this.bar());
    this.mark();
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
    return (f ? f.models : []).map((m) => new Option(m.free ? `${m.label} (free)` : m.label, m.value));
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
    const models = h("datalist", { id: `set-models-${i}` }, ...this.modelOptions(a.harness));
    const name = h("input", { name: "name", value: a.name, spellcheck: false, placeholder: "name", className: "set-name" });
    const harness = select("harness", v.harnesses, a.harness, (x) => this.harnessLabel(x));
    const model = h("input", { name: "model", value: a.model, spellcheck: false, placeholder: "model", className: "pick" });
    model.setAttribute("list", models.id);
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
        if (el.name === "harness") models.replaceChildren(...this.modelOptions(el.value));
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
    card.append(name, h("div", { className: "picks" }, harness, model, models, effort, permission), h("div", { className: "set-card-foot" }, fold, star, del));
    if (isDefault) card.classList.add("is-default");
    return card;
  }

  // The queues' agent pickers follow a rename in place. Redrawing the form
  // here would replace the Save button under a click that blurred the name.
  refreshNames() {
    const names = this.doc.agents.map((a) => a.name);
    this.box.querySelectorAll(".set-queue select[name=agent]").forEach((sel, i) => {
      sel.replaceChildren(...names.map((n) => new Option(n, n)));
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
    const status = this.status || (this.dirty ? "Unsaved changes" : "");
    b.classList.toggle("dirty", this.dirty);
    const save = button("Save changes", "set-save", () => this.save(), "btn primary");
    save.disabled = !this.dirty || this.saving;
    const discard = button("Discard", "set-discard", () => {
      this.load(this.wire);
      this.findings = null;
      this.status = "";
      this.draw();
    }, "btn");
    discard.hidden = !this.dirty;
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

  async save() {
    this.saving = true;
    this.drawBar();
    try {
      const r = await this.call("config.write", { doc: this.doc, stamp: this.stamp });
      if (r.saved) {
        this.wire = r.config;
        this.load(r.config);
        this.findings = null;
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
