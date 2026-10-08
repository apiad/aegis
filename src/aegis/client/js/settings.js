// Settings: .aegis.yaml as a form. The server holds the file's parsed copy and
// sends it on the `config` channel; Save writes the whole form back through
// `config.write`, which refuses a stale write and validates first. The server
// re-reads the file after any write, so the page never patches server state.
// Nothing here decides what is valid: every finding names the row it marks.

const h = (tag, props = {}, ...kids) => {
  const el = Object.assign(document.createElement(tag), props);
  el.append(...kids.filter((k) => k != null));
  return el;
};

const button = (text, id, onclick) => {
  const b = h("button", { type: "button", textContent: text, onclick });
  if (id) b.id = id;
  return b;
};

function select(name, values, value, label = (v) => v) {
  const s = h("select", { name });
  for (const v of values) s.append(new Option(label(v), v));
  if (value && !values.includes(value)) s.append(new Option(`${value} (unknown)`, value));
  s.value = value || "";
  return s;
}

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
    this.findings = []; // the last doctor run, or the last refused save
    this.status = "";
  }

  // A config from the channel. An untouched form follows the file; an edited
  // one keeps its edits and says the file moved.
  onConfig(wire) {
    this.wire = wire;
    if (this.saving) return;
    if (!this.dirty || this.doc === null) this.load(wire);
    else if (JSON.stringify(wire.stamp) !== JSON.stringify(this.stamp)) this.stale = true;
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
      this.found = await this.conn.call("config.detect");
    } catch (e) {
      this.status = e.message;
    }
    if (!this.dirty) this.draw();
  }

  touch() {
    this.dirty = true;
    this.status = "";
    const s = document.getElementById("set-status");
    if (s) s.textContent = "";
  }

  draw() {
    const w = this.wire;
    if (!w || !this.doc) return this.box.replaceChildren(h("p", { className: "notice", textContent: "Loading…" }));
    const parts = [h("div", { className: "set-head" }, h("h3", { textContent: "Settings" }), h("code", { id: "set-path", textContent: w.path }))];
    if (w.error)
      parts.push(
        h("p", {
          className: "set-band err",
          id: "set-error",
          textContent: `The file on disk does not parse; aegis is still using the last version that did. ${w.error}`,
        }),
      );
    if (this.stale)
      parts.push(
        h(
          "p",
          { className: "set-band", id: "set-stale", textContent: "The file changed on disk since you opened it. " },
          button("Reload from disk", "set-reload", () => {
            this.load(this.wire);
            this.findings = [];
            this.draw();
          }),
        ),
      );
    if (!w.exists && !this.dirty) {
      parts.push(
        h("div", { className: "set-empty" }, h("p", { textContent: `No .aegis.yaml at ${w.root}.` }), button("Set up", "set-setup", () => this.setup())),
      );
      return this.box.replaceChildren(...parts);
    }
    parts.push(this.findingsList(), this.agentsTable(), this.defaultPick(), this.queuesTable(), this.actions());
    this.box.replaceChildren(...parts);
    this.mark();
  }

  findingsList() {
    return h(
      "ul",
      { id: "set-findings" },
      ...this.findings.map((f) =>
        h("li", { className: f.level }, h("b", { textContent: f.level }), " ", h("code", { textContent: f.where }), " ", f.message),
      ),
    );
  }

  harnessLabel(x) {
    const f = this.found.find((f) => f.harness === x);
    return f && !f.bin ? `${x} (not installed)` : x;
  }

  modelOptions(harness) {
    const f = this.found.find((f) => f.harness === harness);
    return (f ? f.models : []).map((m) => new Option(m.label, m.value));
  }

  agentsTable() {
    const v = this.wire.vocab;
    const t = h("table", { className: "set-table", id: "set-agents" });
    t.append(h("tr", {}, ...["Agent", "Harness", "Model", "Effort", "Permission", ""].map((x) => h("th", { textContent: x }))));
    this.doc.agents.forEach((a, i) => {
      const tr = h("tr", { className: "set-agent" });
      tr.dataset.row = `agents.${a.name}`;
      const models = h("datalist", { id: `set-models-${i}` }, ...this.modelOptions(a.harness));
      const name = h("input", { name: "name", value: a.name, spellcheck: false });
      const harness = select("harness", v.harnesses, a.harness, (x) => this.harnessLabel(x));
      const model = h("input", { name: "model", value: a.model, spellcheck: false });
      model.setAttribute("list", models.id);
      const effort = select("effort", v.efforts, a.effort);
      const permission = select("permission", v.permissions, a.permission);
      const priming = h("textarea", { name: "priming", value: a.priming || "", rows: 3, placeholder: "The system prompt every session of this agent starts with" });
      for (const el of [name, harness, model, effort, permission, priming]) {
        el.setAttribute("aria-label", `Agent ${el.name}`);
        el.addEventListener("input", () => {
          a[el.name] = el.name === "priming" ? el.value || null : el.value;
          if (el.name === "name") tr.dataset.row = `agents.${el.value}`;
          if (el.name === "harness") models.replaceChildren(...this.modelOptions(el.value));
          this.touch();
        });
      }
      const fold = h("details", { className: "set-priming" }, h("summary", { textContent: a.priming ? "priming" : "no priming" }), priming);
      const del = button("Delete", null, () => {
        this.doc.agents.splice(i, 1);
        this.touch();
        this.draw();
      });
      tr.append(h("td", {}, name), h("td", {}, harness), h("td", {}, model, models), h("td", {}, effort), h("td", {}, permission), h("td", {}, fold, del));
      t.append(tr);
    });
    const add = button("Add agent", "set-add-agent", () => {
      this.doc.agents.push({ name: "", harness: v.harnesses[0], model: "", effort: "", permission: "", priming: null });
      this.touch();
      this.draw();
    });
    return h("section", {}, h("h4", { textContent: "Agents" }), t, add);
  }

  defaultPick() {
    const names = this.doc.agents.map((a) => a.name);
    const s = select("default_agent", ["", ...names], this.doc.default_agent || "", (x) => x || "(none)");
    s.id = "set-default";
    s.setAttribute("aria-label", "Default agent");
    s.addEventListener("input", () => {
      this.doc.default_agent = s.value || null;
      this.touch();
    });
    const wrap = h("section", { className: "set-default" }, h("h4", { textContent: "Default agent" }), s);
    wrap.dataset.row = "default_agent";
    return wrap;
  }

  queuesTable() {
    const names = this.doc.agents.map((a) => a.name);
    const t = h("table", { className: "set-table", id: "set-queues" });
    t.append(h("tr", {}, ...["Queue", "Agent", "Workers at a time", ""].map((x) => h("th", { textContent: x }))));
    this.doc.queues.forEach((q, i) => {
      const tr = h("tr", { className: "set-queue" });
      tr.dataset.row = `queues.${q.name}`;
      const name = h("input", { name: "name", value: q.name, spellcheck: false });
      const agent = select("agent", names, q.agent);
      const limit = h("input", { name: "max_parallel", type: "number", min: 1, value: q.max_parallel ?? "" });
      for (const el of [name, agent, limit]) {
        el.setAttribute("aria-label", `Queue ${el.name}`);
        el.addEventListener("input", () => {
          q[el.name] = el.name === "max_parallel" ? (el.value === "" ? null : Number(el.value)) : el.value;
          if (el.name === "name") tr.dataset.row = `queues.${el.value}`;
          this.touch();
        });
      }
      const del = button("Delete", null, () => {
        this.doc.queues.splice(i, 1);
        this.touch();
        this.draw();
      });
      tr.append(h("td", {}, name), h("td", {}, agent), h("td", {}, limit), h("td", {}, del));
      t.append(tr);
    });
    const add = button("Add queue", "set-add-queue", () => {
      this.doc.queues.push({ name: "", agent: this.doc.default_agent || names[0] || "", max_parallel: null });
      this.touch();
      this.draw();
    });
    return h("section", {}, h("h4", { textContent: "Queues" }), t, add);
  }

  actions() {
    return h(
      "div",
      { className: "set-actions" },
      button("Save", "set-save", () => this.save()),
      button("Run doctor", "set-doctor", () => this.runDoctor()),
      h("span", { id: "set-status", textContent: this.status }),
    );
  }

  // Each finding marks the row Python named; the rest stay in the list.
  mark() {
    for (const f of this.findings) {
      if (!f.row || f.level === "ok") continue;
      const el = this.box.querySelector(`[data-row="${CSS.escape(f.row)}"]`);
      if (!el) continue;
      if (f.level === "error" || !el.classList.contains("error")) el.classList.add(f.level);
      const why = h("div", { className: "set-why", textContent: f.message });
      (el.tagName === "TR" ? el.cells[0] : el).append(why);
    }
  }

  async save() {
    this.saving = true;
    try {
      const r = await this.conn.call("config.write", { doc: this.doc, stamp: this.stamp });
      this.findings = r.problems;
      if (r.saved) {
        this.wire = r.config;
        this.load(r.config);
        this.status = "Saved";
      } else this.status = `Not saved: ${r.problems.length} problem${r.problems.length === 1 ? "" : "s"}`;
    } catch (e) {
      if (e.code === "stale") this.stale = true;
      this.status = e.message;
    } finally {
      this.saving = false;
    }
    this.draw();
  }

  async runDoctor() {
    this.status = "Running the doctor…";
    document.getElementById("set-status").textContent = this.status;
    try {
      this.findings = await this.conn.call("config.doctor");
      const n = (l) => this.findings.filter((f) => f.level === l).length;
      this.status = `${n("error")} errors, ${n("warn")} warnings`;
    } catch (e) {
      this.status = e.message;
    }
    this.draw();
  }

  async setup() {
    try {
      this.doc = await this.conn.call("config.propose");
    } catch (e) {
      this.status = e.message;
      return this.draw();
    }
    this.stamp = null;
    this.touch();
    this.draw();
  }
}
