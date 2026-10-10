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

function bar(kind, pct, severity, tick) {
  const b = el("div", `bar ${kind} ${severity}`);
  const fill = el("i");
  fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
  b.append(fill);
  if (tick != null) {
    const t = el("span", "tick");
    t.style.left = `${tick * 100}%`;
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

export function quotaSideRow(p, w, now) {
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
  r.append(kv, bar("q", w.percent, w.severity, elapsed(w, now)), dim);
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
