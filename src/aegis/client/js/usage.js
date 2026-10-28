// The usage page: one call to usage.page draws every panel, and a change on
// the usage channel asks again, at most once every 2 s. The server decides
// every label, number, width and shade (src/aegis/usage/page.py); this module
// draws and asks, nothing more. A cell, bar or row lists the sessions behind
// it through usage.read, and a session opens its transcript. With a linked
// server, the page asks again every minute: linked servers do not signal.

const REFRESH_MS = 2000;
const LINKED_MS = 60_000;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function zone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

export class Usage {
  constructor(conn, box, { onOpen }) {
    Object.assign(this, { conn, box, onOpen });
    this.params = { window: 4, split: "purpose", measure: "cost" };
    this.shown = false;
    this.last = 0;
    this.timer = null;
    this.poll = null;
    this.seq = 0;
    this.page = null;
  }

  open() {
    this.shown = true;
    this.load();
  }

  close() {
    this.shown = false;
    clearTimeout(this.timer);
    clearTimeout(this.poll);
  }

  // A turn ended somewhere: ask again, no sooner than 2 s after the last ask.
  changed() {
    if (!this.shown || this.timer) return;
    const wait = Math.max(0, this.last + REFRESH_MS - Date.now());
    this.timer = setTimeout(() => {
      this.timer = null;
      if (this.shown) this.load();
    }, wait);
  }

  async load() {
    this.last = Date.now();
    const seq = ++this.seq;
    let p;
    try {
      p = await this.conn.call("usage.page", { ...this.params, tz: zone() });
    } catch (e) {
      if (seq === this.seq) this.box.replaceChildren(el("p", "unote", `Usage could not be read: ${e.message || e}`));
      return;
    }
    if (seq !== this.seq) return;
    this.page = p;
    this.draw(p);
    clearTimeout(this.poll);
    if (this.shown && p.controls.servers.length > 1) {
      this.poll = setTimeout(() => this.shown && this.load(), LINKED_MS);
    }
  }

  set(key, value) {
    this.params[key] = value;
    this.load();
  }

  draw(p) {
    const head = el("div", "uhead");
    head.append(el("b", null, "Usage"));
    const live = el("span", "ulive");
    live.append(el("i"), `live · ${p.controls.servers.length} server${p.controls.servers.length === 1 ? "" : "s"}`);
    head.append(live);
    this.drill = el("div", "pane udrill");
    this.drill.hidden = true;
    this.box.replaceChildren(
      head,
      this.controls(p),
      this.tiles(p.tiles),
      this.drill,
      grid(pane(p.trend.title, `Stacked by ${p.split}`, this.trend(p.trend)),
           pane("Who the money goes to", "By repo owner; bars split by server", this.owners(p.owners))),
      grid(pane("Repo by purpose", "Share of each repo's measure; the darker, the more", this.heat(p.heat), true),
           pane("When", "Weekday by hour, your time zone", this.when(p.when), true)),
      grid(pane("Driven by", "What woke each turn", this.bars(p.mode, (r) => ({ mode: r.key }))),
           pane("Harness and model", "Unpriced tokens shown as tokens", this.bars(p.model, (r) => r.where))),
      pane("Outcomes", "From the journal; cost is the measure of the same slice", this.outcomes(p.outcomes), true, "uout"),
      grid(pane("Subscriptions", "Each fee split by owner, elsewhere and unused", subscriptions(p.subscriptions)),
           pane("Context length", "Every call rereads its context: cost by band, peaks and an earlier handoff", context(p.context))),
      foot(p.foot, p.servers),
    );
  }

  controls(p) {
    const c = p.controls;
    const row = el("div", "uctl");
    const seg = (label, key, items) => {
      const l = el("label", null, label);
      const s = el("span", "useg");
      for (const it of items) {
        const b = el("button", it.key === this.params[key] ? "on" : "", it.label);
        b.type = "button";
        b.dataset.key = String(it.key);
        b.addEventListener("click", () => this.set(key, it.key));
        s.append(b);
      }
      l.append(s);
      return l;
    };
    row.append(
      seg("window", "window", c.windows.map((w) => ({ key: w, label: `${w}w` }))),
      seg("split by", "split", c.splits.map((s) => ({ key: s, label: s }))),
      seg("measure", "measure", c.measures),
    );
    const servers = el("label", null, "servers");
    const chips = el("span", "uchips");
    for (const s of c.servers) {
      const b = el("button", `uchip${s.on ? " on" : ""}`, s.name);
      b.type = "button";
      b.dataset.server = s.name;
      b.addEventListener("click", () => {
        const on = c.servers.filter((x) => x.on !== (x.name === s.name)).map((x) => x.name);
        if (on.length) this.set("servers", on.length === c.servers.length ? null : on);
      });
      chips.append(b);
    }
    servers.append(chips);
    row.append(servers);
    return row;
  }

  tiles(tiles) {
    const box = el("div", "utiles");
    for (const t of tiles) {
      const n = el("div", "utile");
      n.dataset.key = t.key;
      const v = el("div", "v", t.value);
      if (t.change) v.append(el("span", `chg ${t.change.dir}`, ` ${t.change.text}`));
      n.append(el("div", "k", t.label), v, el("div", "d", t.sub));
      box.append(n);
    }
    return box;
  }

  trend(t) {
    const wrap = el("div");
    const legend = el("div", "ulegend");
    for (const s of t.series) {
      const it = el("span");
      it.append(el("i", `c${s.colour}`), `${s.label} ${s.text}`);
      legend.append(it);
    }
    const cols = el("div", "utrend");
    for (const w of t.weeks) {
      const col = el("div", "ucol");
      col.title = `${w.label}: ${w.text}`;
      const bar = el("div", "ubar");
      for (const s of w.stack) {
        const seg = el("div", `c${s.colour}`);
        seg.style.height = `${s.frac * 100}%`;
        seg.title = `${w.label} · ${s.key}: ${s.text}`;
        if (s.key !== "other" && t.split !== "server")
          this.clickable(seg, { [t.split]: s.key === "(none)" ? "" : s.key }, `${s.key}, all weeks`);
        bar.append(seg);
      }
      col.append(bar, el("div", "ulab", w.label));
      cols.append(col);
    }
    wrap.append(legend, cols);
    return wrap;
  }

  owners(rows) {
    const box = el("div");
    for (const r of rows) {
      const line = el("div", "uhb");
      const stack = el("div", "ustack");
      for (const part of r.parts) {
        const d = el("div", `c${part.colour}`);
        d.style.width = `${part.frac * 100}%`;
        d.title = `${part.server}: ${part.text}`;
        stack.append(d);
      }
      line.append(el("span", "nm", r.label), stack, el("span", "vv", r.text));
      this.clickable(line, r.where, r.label);
      box.append(line);
    }
    if (!rows.length) box.append(el("p", "unote", "Nothing counted in this window."));
    return box;
  }

  bars(rows, where) {
    const box = el("div");
    for (const r of rows) {
      const line = el("div", "uhb");
      const stack = el("div", "ustack");
      const d = el("div", "c0");
      d.style.width = `${r.frac * 100}%`;
      stack.append(d);
      line.append(el("span", "nm", r.label), stack, el("span", "vv", r.text));
      line.title = r.share ? `${r.label}: ${r.text}, ${r.share}` : `${r.label}: ${r.text}`;
      this.clickable(line, where(r), r.label);
      box.append(line);
    }
    if (!rows.length) box.append(el("p", "unote", "Nothing counted in this window."));
    return box;
  }

  heat(h) {
    const t = el("table", "uheat");
    const hr = el("tr");
    hr.append(el("th", null, "repo"), ...h.cols.map((c) => el("th", null, c)), el("th", null, "total"));
    t.append(hr);
    for (const r of h.rows) {
      const tr = el("tr");
      tr.append(el("td", null, r.label));
      for (const c of r.cells) {
        const td = el("td", `l${c.level}`, c.text);
        if (c.text) this.clickable(td, c.where, `${r.label} · ${c.purpose}`);
        tr.append(td);
      }
      tr.append(el("td", "tot", r.text));
      t.append(tr);
    }
    if (!h.rows.length) return el("p", "unote", "No repo has work in this window.");
    return t;
  }

  when(w) {
    const t = el("table", "uwhen");
    const hr = el("tr");
    hr.append(el("th"), ...w.hours.map((h, i) => el("th", null, i % 6 === 0 ? h : "")));
    t.append(hr);
    w.days.forEach((d, i) => {
      const tr = el("tr");
      tr.append(el("th", null, d));
      w.cells[i].forEach((c, h) => {
        const td = el("td", `l${c.level}`);
        if (c.text) td.title = `${d} ${w.hours[h]}: ${c.text}`;
        tr.append(td);
      });
      t.append(tr);
    });
    return t;
  }

  outcomes(o) {
    if (!o.rows.length) return el("p", "unote", "No commits, PRs or issues in this window.");
    const t = el("table");
    const hr = el("tr");
    for (const h of ["repo", "commits", "PRs opened", "merged", "issues", "measure", "per merged PR"]) hr.append(el("th", null, h));
    t.append(hr);
    for (const r of o.rows) {
      const tr = el("tr");
      for (const v of [r.label, r.commits, r.prs_opened, r.prs_merged, r.issues, r.text, r.per_pr]) tr.append(el("td", null, String(v)));
      this.clickable(tr, r.where, r.label);
      t.append(tr);
    }
    return t;
  }

  clickable(node, where, title) {
    if (!where) return;
    node.classList.add("uhit");
    node.tabIndex = 0;
    const go = () => this.sessions(where, title);
    node.addEventListener("click", go);
    node.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") go();
    });
  }

  // The sessions behind a cell, bar or row, costliest first.
  async sessions(where, title) {
    const d = this.drill;
    d.hidden = false;
    const h = el("h5", null, `Sessions · ${title}`);
    const x = el("button", "uclose", "×");
    x.type = "button";
    x.setAttribute("aria-label", "Close");
    x.addEventListener("click", () => {
      d.hidden = true;
    });
    h.append(x);
    d.replaceChildren(h, el("p", "unote", "…"));
    d.scrollIntoView({ block: "nearest" });
    let a;
    try {
      a = await this.conn.call("usage.read", {
        since: this.page.since,
        server: this.page.controls.servers[0].name,
        split: ["session"],
        where,
        measure: this.page.measure === "hours" ? "secs" : this.page.measure,
        tz: zone(),
      });
    } catch (e) {
      d.replaceChildren(h, el("p", "unote", `Could not list them: ${e.message || e}`));
      return;
    }
    const list = el("div", "ulist");
    for (const r of a.rows) {
      const row = el("a", "urow");
      row.href = r.open ? `#s=${r.session}` : `#read=${r.session}`;
      row.addEventListener("click", (ev) => {
        ev.preventDefault();
        this.onOpen(r);
      });
      row.append(el("span", "nm", r.handle || r.session), el("span", "vv", `$${r.cost.toFixed(2)} · ${r.turns} turns`));
      list.append(row);
    }
    if (!a.rows.length) list.append(el("p", "unote", "No session."));
    d.replaceChildren(h, list);
  }
}

function pane(title, hint, body, scroll = false, cls = "") {
  const p = el("div", `pane ${cls}`.trim());
  p.append(el("h5", null, title), el("div", "h", hint));
  if (scroll) {
    const s = el("div", "uscroll");
    s.append(body);
    p.append(s);
  } else p.append(body);
  return p;
}

function grid(...panes) {
  const g = el("div", "ugrid");
  g.append(...panes);
  return g;
}

function hbar(label, parts, text, cls = "") {
  const line = el("div", `uhb ${cls}`.trim());
  const stack = el("div", "ustack");
  for (const [c, frac, title] of parts) {
    const d = el("div", c);
    d.style.width = `${frac * 100}%`;
    if (title) d.title = title;
    stack.append(d);
  }
  line.append(el("span", "nm", label), stack, el("span", "vv", text));
  return line;
}

function subscriptions(subs) {
  const box = el("div");
  for (const s of subs) {
    const n = el("div", "usub");
    const hd = el("div", "uhd");
    hd.append(el("span", null, s.name), el("span", null, `fee ${s.fee} · ${s.value} at API prices · ${s.ratio}`));
    const colour = (x) => (x.colour === "rest" ? "urest" : `c${x.colour % 8}`);
    const legend = el("div", "ulegend");
    for (const x of s.parts) {
      const it = el("span");
      it.append(el("i", colour(x)), `${x.label} ${x.text}`);
      legend.append(it);
    }
    const stack = el("div", "ustack");
    for (const x of s.parts) {
      const d = el("div", colour(x));
      d.style.width = `${x.frac * 100}%`;
      d.title = `${x.label}: ${x.text}`;
      stack.append(d);
    }
    n.append(hd, stack, legend, el("p", "unote", s.note));
    box.append(n);
  }
  if (!subs.length) box.append(el("p", "unote", "No subscription ran here in this window."));
  return box;
}

function context(c) {
  const box = el("div");
  if (!c.bands.length) {
    box.append(el("p", "unote", "No call's context counted in this window."));
    return box;
  }
  const legend = el("div", "ulegend");
  for (const [cls, label] of [["urest", "share of calls"], ["c0", "share of cost"]]) {
    const it = el("span");
    it.append(el("i", cls), label);
    legend.append(it);
  }
  box.append(legend);
  for (const b of c.bands) {
    const line = el("div", "uhb");
    const bars = el("div", "ucx");
    for (const [cls, frac, t] of [["urest", b.calls_frac, `${b.calls} of calls`], ["c0", b.cost_frac, `${b.cost} of cost`]]) {
      const st = el("div", "ustack");
      const d = el("div", cls);
      d.style.width = `${frac * 100}%`;
      d.title = t;
      st.append(d);
      bars.append(st);
    }
    line.title = `${b.label}: ${b.calls} of calls, ${b.cost} of cost, ${b.per_call} a call`;
    line.append(el("span", "nm", b.label), bars, el("span", "vv", `${b.per_call}/call`));
    box.append(line);
  }
  box.append(el("div", "h", `Session peaks, handed off · not: ${c.peaks.sessions} session${c.peaks.sessions === 1 ? "" : "s"}; handoff_at ${c.peaks.handoff_at} marked`));
  for (const r of c.peaks.rows) {
    if (!r.handed && !r.other && !r.mark) continue;
    box.append(hbar(r.mark ? `${r.label} · handoff` : r.label,
      [["c1", r.handed_frac, `${r.handed} handed off`], ["urest", r.other_frac, `${r.other} did not`]],
      `${r.handed} · ${r.other}`, r.mark ? "mark" : ""));
  }
  const t = el("table");
  const hr = el("tr");
  for (const h of ["hand off at", "handoffs", "saved", "rewrite", "reread", "net"]) hr.append(el("th", null, h));
  t.append(hr);
  for (const r of c.replay) {
    const tr = el("tr");
    for (const v of [r.at, r.handoffs, r.saved, r.rewrite, r.reread, r.net]) tr.append(el("td", null, String(v)));
    t.append(tr);
  }
  const sc = el("div", "uscroll");
  sc.append(t);
  box.append(sc);
  return box;
}

function ago(s) {
  return s < 60 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
}

function foot(f, servers) {
  const n = el("div", "ufoot");
  for (const line of f.lines) n.append(el("div", null, line));
  for (const [name, s] of Object.entries(servers).slice(1)) {
    const line = s.state === "missing" ? `${name} not reached` : `${name} answered ${ago(s.age_s)} ago`;
    n.append(el("div", null, line));
  }
  return n;
}
