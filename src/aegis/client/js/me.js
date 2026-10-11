// Who this browser is on its server (tokens.py): the welcome names it. A member
// reads every session and changes only its own; an admin changes anything.

export const who = { name: null, role: null, owner: "" };

export function setWho(me, owner) {
  who.name = me?.name ?? null;
  who.role = me?.role ?? null;
  who.owner = owner || "";
}

// The owner of session `m`: a session from before owners is the admin's. Null
// for one on a linked server, which decides for itself.
export function ownerOf(m) {
  return m.server ? null : m.owner || who.owner || null;
}

// Whether this browser may change session `m`.
export function mine(m) {
  const o = ownerOf(m);
  return !who.name || who.role === "admin" || !o || o === who.name;
}

// The owner to show beside `m`: someone other than this browser.
export function shownOwner(m) {
  const o = ownerOf(m);
  return who.name && o && o !== who.name ? o : null;
}
