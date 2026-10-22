// Groups (#586). A session belongs to the group of the session that started
// it (SpawnSpec.group on the server); the bar and the Fleet draw each group as
// one block, headed by its oldest open member. Pure functions over metas.

const tool = (m) => m.kind === "term" || m.kind === "browser";

// A group's key, the same on every server: `<log id>@<server>`. `here` names
// this server, for its own sessions, which carry no `server`.
export function groupKey(m, here) {
  const at = m.server || here;
  if (tool(m) || !m.group) return `${m.log_id || m.key}@${at}`;
  return m.group.includes("@") ? m.group : `${m.group}@${at}`;
}

const older = (a, b) => (a.created_at || 0) - (b.created_at || 0);

// The blocks in bar order: [{key, head, members}], members starting with the
// head. `order` is the browser's own ({blocks: group keys, members: {group
// key: session keys}}); what it does not name follows in `metas`' order.
export function blocksOf(metas, here, order = { blocks: [], members: {} }) {
  const groups = new Map();
  for (const m of metas) {
    const k = groupKey(m, here);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(m);
  }
  const keys = [...(order.blocks || []).filter((k) => groups.has(k))];
  for (const k of groups.keys()) if (!keys.includes(k)) keys.push(k);
  return keys.map((k) => {
    const ms = groups.get(k);
    const on = ms.filter((m) => !m.off);
    const head = (on.length ? on : ms).slice().sort(older)[0];
    const named = (order.members || {})[k] || [];
    const rest = ms.filter((m) => m !== head);
    rest.sort((a, b) => rank(named, a.key) - rank(named, b.key));
    return { key: k, head, members: [head, ...rest] };
  });
}

// Where `key` stands in a stored list; unlisted keys keep their given order.
function rank(list, key) {
  const i = list.indexOf(key);
  return i < 0 ? list.length : i;
}

// The stored tab order: today's {keys, blocks, members}, or the flat list of
// keys a browser kept before groups.
export function readOrder(raw) {
  let v;
  try {
    v = JSON.parse(raw || "[]");
  } catch {
    v = [];
  }
  if (Array.isArray(v)) return { blocks: [], members: {}, keys: v };
  return { blocks: v.blocks || [], members: v.members || {}, keys: v.keys || [] };
}
