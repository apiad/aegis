// One gauge row, drawn the same in the Fleet band and the session sidebar.
// Severity and the projection arrive decided (aegis/quota); this file turns
// them into markup, plus the two things that move with the clock: the tick at
// the share of the window already gone, and the countdown to its reset.

import { icon } from "./glyphs.js";

export const PROJECT_FROM = 80; // the projection prints from here, as quota's PACE_WARN_AT

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

// "3h 06m", "2d 4h", "12m"; a reset already past (a laptop that slept) is "now".
export function countdown(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return "now";
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  if (d >= 1) return `${d}d ${h % 24}h`;
  if (h >= 1) return `${h}h ${String(m % 60).padStart(2, "0")}m`;
  return `${m}m`;
}

export function age(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h`;
}

function clock(ts) {
  const d = new Date(ts * 1000);
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return ts - Date.now() / 1000 > 86400 ? `${d.toLocaleDateString([], { weekday: "short" })} ${hm}` : hm;
}

// The share of the window already gone, clamped to the bar; null draws no tick.
export function elapsed(w, now) {
  if (w.starts_at == null || w.resets_at == null) return null;
  const span = w.resets_at - w.starts_at;
  if (span <= 0) return null;
  return Math.min(1, Math.max(0, (now - w.starts_at) / span));
}

export function hostSeverity(pct) {
  return pct >= 95 ? "critical" : pct >= 80 ? "warning" : "normal";
}

// `lines` are a member's own quota lines on this window ({launch, stop}, as
// its policy holds them, quota_gate.py): a green tick and a red one. A pace
// launch line is the elapsed tick already drawn.
function bar(kind, pct, severity, tick, lines = null) {
  const b = el("div", `bar ${kind} ${severity}`);
  const fill = el("i");
  fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  b.append(fill);
  if (tick != null) {
    const t = el("span", "tick");
    t.style.left = `${tick * 100}%`;
    b.append(t);
  }
  for (const [which, at] of [["l", lines?.launch], ["s", lines?.stop]]) {
    if (typeof at !== "number") continue;
    const t = el("span", `tick lim ${which}`);
    t.style.left = `${at}%`;
    t.title = `${which === "l" ? "launch" : "stop"} line ${at}%`;
    b.append(t);
  }
  return b;
}

// A sprite icon followed by its text, as one span.
function marked(cls, name, text) {
  const n = el("span", cls);
  n.append(icon(name), text);
  return n;
}

function projection(w) {
  return w.projected != null && w.projected >= PROJECT_FROM ? marked("proj", "arrow", ` ${w.projected}%`) : null;
}

// The whole reading as a sentence, for the hover title.
function sentence(p, w, now) {
  const gone = elapsed(w, now);
  let s = `${p.label}, ${w.label} window: ${Math.round(w.percent)}% used`;
  if (gone != null) s += `, ${Math.round(gone * 100)}% of the window gone`;
  if (w.projected != null) s += `, on pace for ${w.projected}% at reset`;
  s += ".";
  if (w.resets_at != null) s += ` Resets ${clock(w.resets_at)}, in ${countdown(w.resets_at - now)}.`;
  if (p.state === "stale") s += ` This reading is ${age(now - p.read_at)} old: ${p.note}.`;
  return s;
}

export function quotaRow(p, w, now) {
  const stale = p.state === "stale";
  const g = el("div", `gauge ${w.severity}${stale ? " stale" : ""}`);
  g.dataset.kind = w.kind;
  g.title = sentence(p, w, now);
  const val = el("span", "val");
  val.append(el("b", null, `${Math.round(w.percent)}%`));
  const proj = projection(w);
  if (proj) val.append(proj);
  if (w.resets_at != null) val.append(marked("rst", "again", ` ${countdown(w.resets_at - now)}`));
  g.append(el("span", null, w.label), bar("q", w.percent, w.severity, elapsed(w, now)), val);
  return g;
}

export function quotaSideRow(p, w, now, lines = null) {
  const stale = p.state === "stale";
  const r = el("div", `qrow ${w.severity}${stale ? " stale" : ""}`);
  r.dataset.kind = w.kind;
  r.title = sentence(p, w, now);
  const kv = el("div", "kv");
  const proj = projection(w);
  const sv = el("span", `sv ${stale ? "" : w.severity}`, `${Math.round(w.percent)}%`);
  if (proj) sv.append(" ", proj);
  kv.append(el("span", null, w.label), sv);
  const dim = el("div", "kv dim");
  if (w.resets_at != null)
    dim.append(el("span", null, `resets in ${countdown(w.resets_at - now)}`), el("span", null, clock(w.resets_at)));
  r.append(kv, bar("q", w.percent, w.severity, elapsed(w, now), lines), dim);
  return r;
}

export function hostRow(label, pct, tail) {
  const sev = hostSeverity(pct);
  const g = el("div", `gauge ${sev}`);
  const val = el("span", "val");
  val.append(el("b", null, `${pct}%`));
  if (tail) val.append(el("span", "rst", tail));
  g.append(el("span", null, label), bar("h", pct, sev, null), val);
  return g;
}

// One row per disk the host mounts, named by its mount point when there are
// several. A linked server older than #541 sends only `disk`.
export function diskRows(h) {
  const disks = h.disks || (h.disk ? [h.disk] : []);
  return disks.map((d) => {
    const row = hostRow(disks.length > 1 ? d.mount : "Disk", d.pct, `${d.used_gb} / ${d.total_gb} GB`);
    if (d.mount) row.firstChild.title = d.mount;
    return row;
  });
}

// "8.3", "84": megabits a second from bytes a second.
export function mbps(bps) {
  const m = (bps * 8) / 1e6;
  return m < 10 ? m.toFixed(1) : String(Math.round(m));
}

function bytes(n) {
  return n < 1e6 ? `${Math.round(n / 1e3)} KB` : `${(n / 1e6).toFixed(1).replace(/\.0$/, "")} MB`;
}

function textRow(label, value, tail, cls, title) {
  const g = el("div", `gauge net${cls ? ` ${cls}` : ""}`);
  if (title) g.title = title;
  const val = el("span", "val");
  val.append(el("b", null, value));
  if (tail) val.append(el("span", "rst", tail));
  g.append(el("span", null, label), val);
  return g;
}

// The network rows under the host meters (net/service.py). No bar: none of
// the three is a share of anything. A reading that may no longer hold (the
// link is down, the lookup failing, the transfer cut short) keeps its value,
// muted, with its age: it still says which network you were last on.
// `measure`, when given, runs on a click on the Speed row.
export function netRows(net, now, measure = null) {
  if (!net) return [];
  const where = (anchor) => anchor.replace(/^\[/, "").replace(/\]?:\d+$/, "");
  const rows = [
    net.ok
      ? textRow("Net", `${net.rtt_ms} ms`, where(net.anchor), "", `TCP handshake to ${net.anchor}, ${age(now - net.at)} ago.`)
      : textRow("Net", "no internet", net.error === "unreachable" ? "anchors refused" : net.error, "critical", `No anchor accepted a connection ${age(now - net.at)} ago.`),
  ];
  if (net.ip) {
    const old = !net.ok || !!net.ip_error;
    const tail = [net.colo, net.loc].filter(Boolean).join(" · ");
    const aged = old && net.ip_at != null ? `${age(now - net.ip_at)} old` : "";
    let title = `Exit address ${net.ip}`;
    if (net.colo) title += `, Cloudflare ${net.colo}, ${net.loc}`;
    if (net.ip_at != null) title += `, looked up ${age(now - net.ip_at)} ago`;
    if (net.ip_error) title += `; the last lookup failed: ${net.ip_error}`;
    rows.push(textRow("Exit", net.ip, [tail, aged].filter(Boolean).join(" · "), old ? "stale" : "", `${title}.`));
  }
  // The address what aegis starts exits from, through a link (exit.py).
  const via = net.via;
  if (via) {
    // An IPv6 address leaves room for little else on a phone.
    const place = via.ip.includes(":") ? via.name : [via.name, via.colo, via.loc].filter(Boolean).join(" · ");
    if (via.down) rows.push(textRow("Via", via.down, via.name, "critical", `Nothing goes out through ${via.name} now, and nothing falls back to this machine's address: ${via.down}.`));
    else if (via.ip) rows.push(textRow("Via", via.ip, place, via.error ? "stale" : "", `What aegis starts reaches the internet from ${via.ip}, through ${via.name}.`));
    else rows.push(textRow("Via", via.error ? "no answer" : "looking up", via.name, via.error ? "stale" : "", via.error ? `The lookup through ${via.name} failed: ${via.error}.` : ""));
  }
  const sp = net.speed;
  let speed;
  if (net.measuring) speed = textRow("Speed", "measuring…", "", "", "Downloading a test file.");
  else if (!sp) speed = textRow("Speed", "not measured", measure && net.ok ? "click to measure" : "", "stale", "");
  else if (sp.complete) {
    const title = `${(sp.bps / 1e6).toFixed(2)} MB/s: ${bytes(sp.received)} in ${sp.elapsed_s} s, ${age(now - sp.at)} ago.`;
    speed = textRow("Speed", `${mbps(sp.bps)} Mbps`, `${age(now - sp.at)} ago`, net.ok ? "" : "stale", title);
  } else {
    const title = `The transfer did not complete: ${bytes(sp.received)} of ${bytes(sp.asked)} in ${sp.elapsed_s} s, ${age(now - sp.at)} ago.`;
    speed = textRow("Speed", "unmeasured", `${sp.error || "cut short"} · ${bytes(sp.received)} of ${bytes(sp.asked)}`, "stale", title);
  }
  if (measure && net.ok && !net.measuring) {
    speed.classList.add("measure");
    speed.title = `${speed.title} Click to measure now.`.trim();
    speed.addEventListener("click", measure);
  }
  rows.push(speed);
  return rows;
}

// One number of the sidebar's at-a-glance rows: a label, the value, and a thin
// bar when the value is a share. `small` follows the value, as a projection.
export function tile(label, value, { pct = null, kind = "", severity = "normal", tick = null, small = "", title = "" } = {}) {
  const t = el("div", `tile ${severity}`);
  if (title) t.title = title;
  const v = el("div", "v", value);
  if (small) {
    const sm = el("small");
    sm.append(small);
    v.append(sm);
  }
  t.append(el("div", "k", label), v);
  if (pct != null) t.append(bar(kind, pct, severity, tick));
  return t;
}

export function quotaTile(p, w, now) {
  const stale = p.state === "stale";
  const t = tile(w.label, `${Math.round(w.percent)}%`, {
    pct: w.percent,
    kind: "q",
    severity: stale ? "stale" : w.severity,
    tick: stale ? null : elapsed(w, now),
    small: !stale && w.projected != null && w.projected >= PROJECT_FROM ? marked(null, "arrow", String(w.projected)) : "",
  });
  t.dataset.kind = w.kind;
  return t;
}

// The quota a session spends: the provider of its harness, and for OpenCode
// the one its model names (`opencode-go/deepseek-v4-pro` spends OpenCode Go;
// `openrouter/...` spends nothing aegis reads). None when aegis reads no quota
// for it.
export function providerFor(s, providers) {
  if (!s) return null;
  return (
    providers.find(
      (p) => p.harness === s.harness && (s.harness !== "opencode" || (s.model || "").startsWith(`${p.name}/`)),
    ) || null
  );
}

export function noteRow(note) {
  const g = el("div", "gauge");
  g.append(el("span"), el("span", "note", note));
  return g;
}

// "Claude", or "Claude · 14m old" for a stale reading.
export function providerLine(p, now) {
  return p.state === "stale" && p.read_at != null ? `${p.label} · ${age(now - p.read_at)} old` : p.label;
}

// The Quota heading: a running backoff wins, else the oldest reading's age.
export function quotaHeading(providers, now) {
  const retry = providers.map((p) => p.retry_at).filter((t) => t != null);
  if (retry.length) return `rate limited, retrying in ${countdown(Math.min(...retry) - now)}`;
  const read = providers.map((p) => p.read_at).filter((t) => t != null);
  return read.length ? `read ${age(now - Math.min(...read))} ago` : "";
}
