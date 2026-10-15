// The Schedules view (#schedules): every schedule on the left, the selected
// one's layers and recent runs on the right, and the editor in the same
// panel, laid out as the same four layers. The server decides every fact
// (schedules.py) and sends it on the `schedules` channel; this module draws,
// asks and sends. A prompt, a check and its stdout are people's and agents'
// text: they are set as text, never as markup.

const CLOCKS = ["every", "cron", "at"];
const HINTS = { every: "5m", cron: "0 6 * * 1-5", at: "2026-10-15 09:00" };
const OVERLAP = {
  wait: "a tick during a live run waits for it to finish; at most one waits",
  skip: "a tick during a live run is dropped",
  parallel: "fires even while a run is live",
};
const CLOSE = {
  done: "archive the run when its last turn_end said done; keep it when it asks you or has something to read",
  always: "archive every run",
  never: "leave every run open",
};
const NAME = /^[a-z][a-z0-9]*(-[a-z0-9]+){1,2}$/;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function button(text, onclick, cls = "") {
  const b = el("button", `btn ${cls}`.trim(), text);
  b.type = "button";
  b.onclick = onclick;
  return b;
}

function input(value, placeholder, oninput, tag = "input") {
  const i = el(tag, "sch-in");
  if (tag === "input") i.type = "text";
  i.value = value || "";
  i.placeholder = placeholder;
  i.spellcheck = false;
  i.oninput = () => oninput(i.value);
  return i;
}

// One choice of a few, as the Settings view's segmented buttons.
function seg(options, on, pick, off = []) {
  const s = el("div", "seg");
  for (const o of options) {
    const b = el("button", o === on ? "on" : "", o);
    b.type = "button";
    b.dataset.v = o;
    b.disabled = off.includes(o);
    b.onclick = () => pick(o);
    s.append(b);
  }
  return s;
}

function layer(name, ...body) {
  const d = el("div", "layer");
  const b = el("div", "b");
  b.append(...body);
  d.append(el("span", "k", name), b);
  return d;
}

const arrow = (t) => el("div", "arrow", t);
const clockText = (c) => Object.entries(c || {}).map(([k, v]) => `${k} ${v}`)[0] || "";

// A server time as the view shows it: the time alone today, else the date too.
export function short(iso) {
  if (!iso) return "";
  const [d, t] = iso.split("T");
  return d === new Date().toLocaleDateString("sv") ? t.slice(0, 5) : `${d} ${t.slice(0, 5)}`;
}

function duration(s) {
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
}

function why(r) {
  if (r.kind === "quiet") return r.rc === 1 ? "exit 1: not yet" : "the same stdout as the last fire";
  if (r.kind === "held") return r.why === "quota" ? "held: the quota gate is closed" : "held: a run is live";
  if (r.kind === "skipped") return "dropped: a run was live";
  if (r.kind === "error") return [r.rc != null ? `exit ${r.rc}` : "", r.err || ""].filter(Boolean).join(" · ");
  if (r.kind === "missed") return `${r.n} ticks missed while aegis was down or the machine slept; they fire once`;
  if (r.kind === "paused") return r.by === "errors" ? "paused after errors" : "paused";
  return r.kind;
}

function blank(agents) {
  return {
    isNew: true, name: "", clock: "every", every: "30m", cron: "0 6 * * 1-5", at: "",
    overlap: "wait", check: false, bash: "", timeout: 30, target: "new", session: "",
    agent: agents[0] || "", close: "done", spawn: false, cwd: null, prompt: "",
    preview: null, tested: null, err: "",
  };
}

function fromSchedule(s, agents) {
  const [k, v] = Object.entries(s.clock)[0];
  return {
    ...blank(agents), isNew: false, name: s.name, clock: k, [k]: v, overlap: s.overlap,
    check: !!s.check, bash: s.check?.bash || "", timeout: s.check?.timeout_s || 30,
    target: s.fire.target, session: s.fire.session || "", agent: s.fire.agent || "",
    close: s.fire.close || "done", spawn: !!s.fire.spawn, cwd: s.fire.cwd || null, prompt: s.prompt,
  };
}

// The definition schedule.create and schedule.update take.
function definition(E) {
  const fire = E.target === "existing"
    ? { target: "existing", session: E.session }
    : { target: "new", agent: E.agent || null, close: E.close, spawn: E.spawn, ...(E.cwd ? { cwd: E.cwd } : {}) };
  return {
    name: E.name,
    clock: { [E.clock]: E[E.clock].trim() },
    overlap: E.overlap,
    ...(E.check ? { check: { bash: E.bash, timeout_s: Number(E.timeout) || 30 } } : {}),
    fire,
    prompt: E.prompt,
  };
}

export class Schedules {
  // sessions: () => the open sessions' cards; go: navigate to a hash;
  // onCount: (n, red) for the top bar's button.
  constructor(conn, root, { go, sessions, onCount }) {
    Object.assign(this, { conn, root, go, sessions, onCount });
    this.items = new Map();
    this.cur = null; // the selected schedule
    this.form = null; // the editor's state, or null
    this.quiet = false;
    this.runs = null; // the selected schedule's page of schedule.runs
    this.agents = [];
    this.visible = false;
    this.err = "";
    this._preview = null;
  }

  onSnapshot(list) {
    this.items = new Map((list || []).map((s) => [s.name, s]));
    this.changed([...this.items.keys()]);
  }

  onPatch(ops) {
    const names = [];
    for (const op of ops) {
      if (op.upsert) {
        this.items.set(op.upsert.name, op.upsert);
        names.push(op.upsert.name);
      }
      if (op.remove) {
        this.items.delete(op.remove);
        names.push(op.remove);
      }
    }
    this.changed(names);
  }

  changed(names) {
    const all = [...this.items.values()];
    this.onCount(all.length, all.some((s) => s.paused?.by === "errors"));
    if (!this.visible || this.form) return;
    if (this.cur && !this.items.has(this.cur)) this.cur = null;
    if (this.cur && names.includes(this.cur)) this.loadRuns();
    else this.draw();
  }

  async open() {
    this.visible = true;
    try {
      this.agents = (await this.conn.call("agents.list", {})).agents.map((a) => a.name);
    } catch {
      this.agents = [];
    }
    if (!this.cur || !this.items.has(this.cur)) this.cur = [...this.items.keys()].sort()[0] || null;
    if (this.cur) await this.loadRuns();
    else this.draw();
  }

  close() {
    this.visible = false;
  }

  async loadRuns() {
    const name = this.cur;
    try {
      this.runs = await this.conn.call("schedule.runs", { name, quiet: this.quiet, limit: 50 });
    } catch {
      this.runs = null;
    }
    if (name === this.cur && !this.form) this.draw();
  }

  select(name) {
    this.cur = name;
    this.form = null;
    this.runs = null;
    this.err = "";
    this.draw();
    this.loadRuns();
  }

  async act(op, name) {
    this.err = "";
    try {
      await this.conn.call(op, { name });
    } catch (e) {
      this.err = e.message;
      this.draw();
    }
  }

  // -- drawing ------------------------------------------------------------------
  draw() {
    const side = el("div", "sch-side");
    const list = el("div", "sch-list");
    const names = [...this.items.keys()].sort();
    for (const n of names) list.append(this.row(this.items.get(n)));
    if (!names.length) list.append(el("p", "cnote", "No schedules yet."));
    const add = button("+ New schedule", () => {
      this.form = blank(this.agents);
      this.draw();
    }, "sch-new");
    side.append(list, add);
    const main = el("div", "sch-main");
    if (this.form) main.append(this.editor());
    else if (this.cur && this.items.has(this.cur)) main.append(this.detail(this.items.get(this.cur)));
    else main.append(el("p", "cnote", "A schedule sends a prompt to an agent when a clock says so."));
    const grid = el("div", "sch-grid");
    grid.append(side, main);
    const tz = [...this.items.values()][0]?.tz;
    const head = el("div", "sch-top");
    head.append(el("h2", null, "Schedules"), el("span", "cnote", tz ? `times are the server's, ${tz}` : ""));
    this.root.replaceChildren(head, grid);
  }

  owner(s) {
    if (s.owner.kind !== "agent") return s.owner.principal;
    return `agent ${s.owner_handle || "(closed)"}`;
  }

  sessionName(logId) {
    return this.sessions().find((m) => m.log_id === logId)?.handle || logId;
  }

  row(s) {
    const r = el("div", `sch-row${s.name === this.cur ? " on" : ""}`);
    r.tabIndex = 0;
    r.setAttribute("role", "button");
    r.dataset.name = s.name;
    const state = s.paused?.by === "errors" ? "err" : s.paused || s.finished_at ? "idle" : s.live.length ? "work" : "ok";
    const nm = el("div", "nm");
    let tag;
    if (s.paused) tag = el("span", "chip e", s.paused.by === "errors" ? "paused · errors" : "paused");
    else if (s.finished_at) tag = el("span", "chip", "finished");
    else tag = el("span", "chip", s.live.length ? "running" : short(s.next));
    nm.append(el("span", `dot s-${state}`), el("span", "n", s.name), el("span", "sp"), tag);
    const fire = s.fire.target === "existing" ? "existing session" : s.fire.agent || "default agent";
    const m = el("div", "m", [clockText(s.clock), s.check ? "bash" : null, fire, this.owner(s)].filter(Boolean).join(" · "));
    r.append(nm, m);
    r.onclick = () => this.select(s.name);
    r.onkeydown = (e) => {
      if (e.key === "Enter") this.select(s.name);
    };
    return r;
  }

  detail(s) {
    const box = el("div", "sch-detail");
    const head = el("div", "sch-dhead");
    const del = button("Delete", () => {
      // Two clicks, no dialog: the first arms it for three seconds.
      if (del.dataset.armed) return this.act("schedule.delete", s.name);
      del.dataset.armed = "1";
      del.textContent = "Delete? click again";
      setTimeout(() => {
        delete del.dataset.armed;
        del.textContent = "Delete";
      }, 3000);
    });
    head.append(
      el("h3", null, s.name),
      el("span", "chip", this.owner(s)),
      el("span", "sp"),
      button(s.paused ? "Resume" : "Pause", () => this.act(s.paused ? "schedule.resume" : "schedule.pause", s.name)),
      button("Fire now", () => this.act("schedule.fire", s.name)),
      button("Edit", () => {
        this.form = fromSchedule(s, this.agents);
        this.draw();
        this.preview();
      }),
      del,
    );
    let sub;
    if (s.paused?.by === "errors") sub = "Paused after errors. Resume clears them and starts the clock from now.";
    else if (s.paused) sub = "Paused. Resume starts the clock from now.";
    else if (s.finished_at) sub = "Fired once; kept with its runs for seven days.";
    else sub = `Next tick ${short(s.next)}.${s.held ? ` One tick is held (${s.held}).` : ""} A tick fires only if it passes every layer.`;
    box.append(head, el("p", "cnote", sub));
    if (this.err) box.append(el("p", "cnote err", this.err));
    box.append(this.layers(s), this.table());
    return box;
  }

  layers(s) {
    const L = el("div", "sch-layers");
    const fire = s.fire.target === "existing"
      ? `existing session ${this.sessionName(s.fire.session)}`
      : `new session · agent ${s.fire.agent || "default"} · close ${s.fire.close} · spawn ${s.fire.spawn}`;
    const bash = s.check
      ? layer("Bash", el("span", "cnote", "fires on exit 0 with a new stdout · 1 is not yet · anything else is an error"), el("pre", null, s.check.bash))
      : layer("Bash", el("span", "cnote", "none: every tick that passes overlap fires"));
    if (!s.check) bash.classList.add("off");
    L.append(
      layer("Clock", el("code", null, clockText(s.clock))),
      arrow("↓ tick"),
      layer("Overlap", el("code", null, s.overlap), el("span", "cnote", ` ${OVERLAP[s.overlap]}`)),
      arrow("↓"),
      bash,
      arrow("↓ fire"),
      layer("Fire", el("span", null, fire), el("pre", null, s.prompt + (s.check ? "\n\n+ the check's stdout" : ""))),
    );
    return L;
  }

  table() {
    const box = el("div", "sch-runs");
    const h = el("h4", null, "Recent runs");
    const lab = el("label", "sch-quiet");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = this.quiet;
    cb.onchange = () => {
      this.quiet = cb.checked;
      this.loadRuns();
    };
    lab.append(cb, document.createTextNode(" show quiet ticks"));
    h.append(lab);
    box.append(h);
    const rows = this.runs?.rows || [];
    if (!rows.length) {
      box.append(el("p", "cnote", this.runs ? "No runs yet." : "…"));
      return box;
    }
    const t = el("table", "runs");
    for (const r of rows) t.append(this.runRow(r));
    box.append(t);
    return box;
  }

  runRow(r) {
    const tr = el("tr", `k-${r.kind}`);
    const when = r.kind === "quiet" && r.n ? `${short(r.from)}–${short(r.to)}` : short(r.t);
    const label = r.kind === "quiet" ? (r.n ? `no change ×${r.n}` : "no change") : r.kind;
    const chip = el("span", `chip${r.kind === "error" ? " e" : r.kind === "fired" ? " a" : ""}`, label);
    const what = el("td", "what");
    if (r.kind === "fired") {
      const a = el("a", "sess", r.handle || r.log_id);
      a.href = r.open ? `#s=${r.log_id}` : `#read=${r.log_id}`;
      a.onclick = (e) => {
        e.preventDefault();
        this.go(a.getAttribute("href"));
      };
      const mark = r.live ? "working" : r.attention || "";
      what.append(el("span", `dot s-${mark}`), a, el("span", "cnote", ` ${r.live ? "running" : mark}${r.closed ? " · archived" : ""}`));
      if (r.stdout) what.append(el("div", "why", `stdout: ${r.stdout.trim().split("\n")[0]}`));
    } else {
      what.append(el("span", "why", why(r)));
    }
    const c = el("td");
    c.append(chip);
    tr.append(
      el("td", "t", when),
      c,
      what,
      el("td", "r", r.secs != null ? duration(r.secs) : ""),
      el("td", "r cost", r.cost_usd != null ? `$${r.cost_usd.toFixed(2)}` : ""),
    );
    return tr;
  }

  // -- the editor ---------------------------------------------------------------
  editor() {
    const E = this.form;
    const f = el("div", "sch-form");
    const head = el("div", "sch-dhead");
    head.append(el("h3", null, E.isNew ? "New schedule" : `Edit ${E.name}`));
    f.append(head, el("label", null, "Name"));
    const name = input(E.name, "pr-triage", (v) => {
      E.name = v;
      this.validate();
    });
    name.id = "sch-name";
    name.disabled = !E.isNew;
    f.append(name, el("div", "hint", ""));
    name.nextSibling.id = "sch-name-hint";
    f.append(el("p", "cnote", "A tick fires only if it passes every layer."));

    const L = el("div", "sch-layers");
    const clock = input(E[E.clock], HINTS[E.clock], (v) => {
      E[E.clock] = v;
      this.preview();
    });
    clock.id = "sch-clock";
    const clockHint = el("div", "hint");
    clockHint.id = "sch-clock-hint";
    L.append(layer("Clock", seg(CLOCKS, E.clock, (v) => {
      E.clock = v;
      this.draw();
      this.preview();
    }), clock, clockHint));
    L.append(arrow("↓ tick"));

    const ex = E.target === "existing";
    L.append(layer("Overlap", seg(Object.keys(OVERLAP), E.overlap, (v) => {
      E.overlap = v;
      this.draw();
    }, ex ? ["parallel"] : []), el("div", "hint", OVERLAP[E.overlap] + (ex ? " · parallel needs a new session per fire" : ""))));
    L.append(arrow("↓"));

    const on = el("label", "inl");
    const cb = el("input");
    cb.type = "checkbox";
    cb.id = "sch-check";
    cb.checked = E.check;
    cb.onchange = () => {
      E.check = cb.checked;
      E.tested = null;
      this.draw();
    };
    on.append(cb, document.createTextNode(" only fire when a check passes"));
    const bash = [on];
    if (E.check) {
      const cmd = input(E.bash, "gh pr list --json number -q '.[].number'", (v) => {
        E.bash = v;
        E.tested = null;
      }, "textarea");
      cmd.id = "sch-bash";
      const timeout = input(String(E.timeout), "30", (v) => (E.timeout = v));
      timeout.id = "sch-timeout";
      const row = el("div", "inl");
      const tl = el("label", null, "timeout (s) ");
      tl.append(timeout);
      row.append(tl, button("Test check", () => this.test(), "sch-test"));
      bash.push(cmd, row, el("div", "hint", "exit 0 with a stdout different from the last fire fires · exit 1 is not yet · anything else is an error, and 3 in a row pause it"));
      if (E.tested) {
        const t = el("pre", "sch-tested", E.tested);
        t.id = "sch-tested";
        bash.push(t);
      }
    } else {
      bash.push(el("div", "hint", "off: every tick that passes overlap fires"));
    }
    const bl = layer("Bash", ...bash);
    if (!E.check) bl.classList.add("off");
    L.append(bl, arrow("↓ fire"));

    const fire = [seg(["new", "existing"], E.target, (v) => {
      E.target = v;
      if (v === "existing" && E.overlap === "parallel") E.overlap = "wait";
      this.draw();
    })];
    if (ex) {
      const sel = el("select", "sch-in");
      sel.id = "sch-session";
      sel.append(el("option", null, "pick a session"));
      sel.firstChild.value = "";
      for (const m of this.sessions()) {
        const o = el("option", null, m.handle);
        o.value = m.log_id;
        o.selected = m.log_id === E.session || m.handle === E.session;
        sel.append(o);
      }
      sel.onchange = () => {
        E.session = sel.value;
        this.validate();
      };
      fire.push(el("label", null, `Session (its inbox gets > from schedule:${E.name || "…"})`), sel);
    } else {
      const sel = el("select", "sch-in");
      sel.id = "sch-agent";
      for (const a of this.agents) {
        const o = el("option", null, a);
        o.value = a;
        o.selected = a === E.agent;
        sel.append(o);
      }
      sel.onchange = () => (E.agent = sel.value);
      const sp = el("label", "inl");
      const scb = el("input");
      scb.type = "checkbox";
      scb.checked = E.spawn;
      scb.onchange = () => {
        E.spawn = scb.checked;
        this.draw();
      };
      sp.append(scb, document.createTextNode(" may spawn sessions and enqueue tasks"));
      fire.push(
        el("label", null, "Agent"), sel,
        el("label", null, "Close when the run finishes"),
        seg(Object.keys(CLOSE), E.close, (v) => {
          E.close = v;
          this.draw();
        }),
        el("div", "hint", CLOSE[E.close]),
        sp,
        el("div", "hint", E.spawn ? "the sessions it starts belong to the run and close with it by the same rule" : "session_spawn, queue_enqueue and schedule_create are refused for this run"),
      );
    }
    const prompt = input(E.prompt, "What the agent should do on each fire", (v) => {
      E.prompt = v;
      this.validate();
    }, "textarea");
    prompt.id = "sch-prompt";
    fire.push(el("label", null, "Prompt"), prompt);
    if (E.check) fire.push(el("div", "hint", "the check's stdout is appended below the prompt"));
    L.append(layer("Fire", ...fire));
    f.append(L);

    const foot = el("div", "sch-foot");
    const save = button("Save", () => this.save(), "primary");
    save.id = "sch-save";
    foot.append(save, button("Cancel", () => {
      this.form = null;
      this.draw();
      if (this.cur) this.loadRuns();
    }));
    if (!E.isNew) foot.append(el("span", "hint", "a change applies from the next tick; a live run is not touched"));
    f.append(foot);
    if (E.err) {
      const e = el("p", "cnote err", E.err);
      e.id = "sch-err";
      f.append(e);
    }
    queueMicrotask(() => this.validate());
    return f;
  }

  // The clock is checked by the server (schedule.test), a quarter second after
  // the last keystroke, so the editor never parses cron itself.
  preview() {
    const E = this.form;
    if (!E) return;
    clearTimeout(this._preview);
    const value = E[E.clock];
    E.preview = null;
    this.validate();
    this._preview = setTimeout(async () => {
      let r;
      try {
        r = await this.conn.call("schedule.test", { clock: { [E.clock]: value } });
      } catch (e) {
        r = { error: e.message };
      }
      if (this.form !== E || E[E.clock] !== value) return;
      E.preview = r;
      this.validate();
    }, 250);
  }

  validate() {
    const E = this.form;
    if (!E) return;
    const nameOk = NAME.test(E.name);
    const clockOk = !!E.preview && !E.preview.error;
    const fireOk = E.target === "new" || !!E.session;
    const hint = document.getElementById("sch-name-hint");
    if (hint) {
      hint.textContent = E.name && !nameOk ? "two or three lowercase words joined by hyphens" : "";
      hint.classList.toggle("bad", !!E.name && !nameOk);
    }
    const ch = document.getElementById("sch-clock-hint");
    if (ch) {
      ch.textContent = !E.preview ? "…" : E.preview.error || `next ticks: ${E.preview.next.map(short).join(", ")}`;
      ch.classList.toggle("bad", !!E.preview?.error);
    }
    const save = document.getElementById("sch-save");
    if (save) save.disabled = !(nameOk && clockOk && fireOk && E.prompt.trim());
  }

  async test() {
    const E = this.form;
    const p = { clock: { [E.clock]: E[E.clock].trim() }, check: { bash: E.bash, timeout_s: Number(E.timeout) || 30 } };
    if (!E.isNew) p.name = E.name;
    if (E.target === "existing" && E.session) p.session = E.session;
    if (!E.bash.trim()) E.tested = "write a command first";
    else {
      try {
        const r = await this.conn.call("schedule.test", p);
        const c = r.check;
        E.tested = r.error || `exit ${c.rc ?? "none"} · ${c.would_fire ? "would fire" : "would not fire"}: ${c.why}${c.stdout ? `\n${c.stdout}` : ""}`;
      } catch (e) {
        E.tested = e.message;
      }
    }
    if (this.form === E) this.draw();
  }

  async save() {
    const E = this.form;
    E.err = "";
    try {
      await this.conn.call(E.isNew ? "schedule.create" : "schedule.update", definition(E));
    } catch (e) {
      E.err = e.message;
      this.draw();
      return;
    }
    this.form = null;
    this.select(E.name);
  }
}
