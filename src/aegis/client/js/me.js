// Who this browser is on its server (tokens.py): the welcome names it. A member
// reads every session and changes only its own; an admin changes anything.

export const who = { name: null, role: null, owner: "", policy: null };

export function setWho(me, owner) {
  who.name = me?.name ?? null;
  who.role = me?.role ?? null;
  who.policy = me?.policy ?? null; // its quota lines; none for an admin
  who.owner = owner || "";
}

// The lines `who`'s policy draws on a window of a pool, or null.
export function linesOf(pool, kind) {
  const pp = who.policy?.[pool];
  return pp?.mode === "lines" ? pp.lines?.[kind] || null : null;
}

// The owner of session `m`: a session from before owners is the admin's. Null
// for one on a linked server, which decides for itself.
export function ownerOf(m) {
  return m.server ? null : m.owner || who.owner || null;
}

// Whether this browser may change session `m`. Only an admin here acts through
// a link: the link works on the far server as its own token (web.py).
export function mine(m) {
  if (!who.name || who.role === "admin") return true;
  if (m.server) return false;
  const o = ownerOf(m);
  return !o || o === who.name;
}

// The owner to show beside `m`: someone other than this browser.
export function shownOwner(m) {
  const o = ownerOf(m);
  return who.name && o && o !== who.name ? o : null;
}
