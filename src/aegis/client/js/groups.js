// Groups (#586). A session belongs to the group of the session that started
// it (SpawnSpec.group on the server); the bar and the Fleet draw each group as
// one block, headed by the session that started it, else the handoff that
// continues that session (`leads`, #641), else its oldest open member. Pure
// functions over metas.

const tool = (m) => m.kind === "term" || m.kind === "browser";

// A group's key, the same on every server: `<log id>@<server>`. `here` names
// this server, for its own sessions, which carry no `server`.
export function groupKey(m, here) {
  const at = m.server || here;
  if (tool(m) || !m.group) return `${m.log_id || m.key}@${at}`;
  return m.group.includes("@") ? m.group : `${m.group}@${at}`;
}

// A group's colour, --group-0 to --group-3 (base.css), from its key: the bar
// and the Fleet paint one group alike.
export function hueOf(key) {
  let h = 0;
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return h % 4;
}

const older = (a, b) => (a.created_at || 0) - (b.created_at || 0);
// The head first: the group's own session, then its handoff, then the rest.
const rank = (m) => (!m.group ? 0 : m.leads ? 1 : 2);

// The blocks in bar order: [{key, head, members}], members starting with the
// head. A block stands where its first member stands in `metas`, which is in
// the browser's tab order, and members keep that order (tabs.js TabOrder).
export function blocksOf(metas, here) {
  const groups = new Map();
  for (const m of metas) {
    const k = groupKey(m, here);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  return [...groups].map(([key, ms]) => {
    const on = ms.filter((m) => !m.off);
    const head = (on.length ? on : ms).slice().sort((a, b) => rank(a) - rank(b) || older(a, b))[0];
    return { key, head, members: [head, ...ms.filter((m) => m !== head)] };
  });
}
