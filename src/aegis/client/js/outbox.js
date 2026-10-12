// Messages written with no connection (#342). A plain-text message sent while
// its session's server is out of reach becomes that session's draft, kept in
// the device store (devicestore.js) so it survives a reload, and drawn after
// the transcript's last row as queued. When the connection is back each draft
// goes out by itself, carrying the transcript revision its writer saw; the
// server refuses it as stale if the session moved on since, and the draft then
// stays and asks.
//
// One draft per session: writing again appends to it, and it keeps its first
// revision, the oldest view the person answered. Two queued messages to one
// session would make the second read as stale as soon as the first went out.
//
// A draft is {text, rev, written_at, state}; state is "queued" (goes out on
// reconnect), "stale" (the session moved on) or "gone" (the session is closed).

import { outboxAll, outboxDelete, outboxPut } from "./devicestore.js";

const drafts = new Map(); // key -> draft, this page's copy of the store
const listeners = [];

const changed = () => listeners.forEach((fn) => fn());

// Called when any draft is added, changed or removed.
export const onDrafts = (fn) => listeners.push(fn);

export const draftFor = (key) => drafts.get(key) || null;

// The store's drafts into this page, at boot and after a flush (another tab
// may have sent or changed some).
export async function loadDrafts() {
  const all = await outboxAll();
  drafts.clear();
  for (const [key, d] of all) drafts.set(key, d);
  changed();
}

// `rev` is the transcript revision the page held for the session, or -1 when
// it held none: then any content at all makes the draft ask.
export async function queue(key, text, rev) {
  const was = drafts.get(key);
  const d = was ? { ...was, text: `${was.text}\n\n${text}` } : { text, rev, written_at: Date.now(), state: "queued" };
  drafts.set(key, d);
  changed();
  await outboxPut(key, d);
}

export async function discard(key) {
  drafts.delete(key);
  changed();
  await outboxDelete(key);
}
