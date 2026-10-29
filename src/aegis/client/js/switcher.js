// The session switcher (#781): Alt+P, or the tab strip's "N ▾" button, lists
// every open session of this server and of each linked server, one group per
// server in the Fleet's order, quiet sessions in one group last. Typing filters
// with the command menu's scorer; Enter opens through the Fleet card's own path
// (openCard), so a quiet session gets its tab back. It reads only the metas the
// client holds: opening it never waits on the network.

import { fuzzy } from "./commands.js";

// Attention first, as Alt+J walks: needs_you, error, review, the longest
// waiting first; then working, waiting and the rest, the most recent first.
const RANK = { needs_you: 0, error: 1, review: 2, working: 3, waiting: 4 };
const rankOf = (m) => RANK[m.mark] ?? RANK[m.attention] ?? 5;
function byAttention(a, b) {
  const ra = rankOf(a);
  const d = ra - rankOf(b);
  if (d) return d;
  const ta = a.last_activity || 0;
  const tb = b.last_activity || 0;
  return ra < 3 ? ta - tb : tb - ta;
}

// The second line: what the person most needs to read about it.
export function lineOf(m) {
  if (m.kind === "term") return m.last_line || "";
  if (m.kind === "browser") return m.url || "";
  return m.attention_line || m.plan_now || (m.attention === "working" ? m.activity || "" : "");
}

const fields = (m) => [m.handle || m.name || "", m.title || "", lineOf(m), m.server || ""];

// The best score among a row's fields; positions only when the handle matched,
// since only the handle is drawn with its letters marked.
function match(q, m) {
  if (!q) return { score: 0, positions: [] };
  let best = null;
  fields(m).forEach((f, i) => {
    const x = fuzzy(q, f);
    if (x && (!best || x.score > best.score)) best = { score: x.score, positions: i === 0 ? x.positions : [] };
  });
  return best;
}

// [{name, server, off, quiet, total, needs, rows: [{m, score, positions}]}].
// `here` names this server; `links` is the links channel's list, in its order.
export function switcherRows(metas, here, links, query = "") {
  const q = query.trim();
  const take = (ms) => {
    const rows = ms.flatMap((m) => {
      const x = match(q, m);
      return x ? [{ m, ...x }] : [];
    });
    if (q) rows.sort((a, b) => b.score - a.score);
    return rows;
  };
  const servers = [
    { name: here, server: null, off: false },
    ...links.map((l) => ({ name: l.name, server: l.name, off: l.state !== "linked" })),
  ];
  const at = (m) => servers.findIndex((s) => s.server === (m.server || null));
  const groups = [];
  for (const s of servers) {
    const all = metas.filter((m) => (m.server || null) === s.server && !m.quiet);
    const rows = take(all.slice().sort(byAttention));
    const needs = all.filter((m) => m.mark === "needs_you").length;
    if (rows.length) groups.push({ ...s, quiet: false, total: all.length, needs, rows });
  }
  const quiet = metas.filter((m) => m.quiet);
  const rows = take(quiet.slice().sort((a, b) => at(a) - at(b) || (b.last_activity || 0) - (a.last_activity || 0)));
  if (rows.length) groups.push({ name: "Quiet", server: null, off: false, quiet: true, total: quiet.length, needs: 0, rows });
  return groups;
}
