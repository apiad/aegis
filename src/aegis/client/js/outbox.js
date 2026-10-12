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

import { outboxAll, outboxDelete, outboxPut, outboxTake } from "./devicestore.js";
import { toast } from "./toast.js";

const drafts = new Map(); // key -> draft, this page's copy of the store
const listeners = [];
let sender = null; // (key, params) -> the session.send call, set by app.js
let round = Promise.resolve();

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

export const setSender = (fn) => (sender = fn);

// Send every queued draft whose key `only` accepts. Each is taken out of the
// store before it is sent, so a second tab flushing at the same moment finds
// nothing, and goes back on any failure. Only the send's reply deletes it: a
// send whose reply never came stays, and the next flush sends it with the same
// revision, which the first send's own entry has moved past, so it asks
// instead of arriving twice.
export async function flush(only = () => true) {
  const out = { sent: 0, stale: 0, gone: 0 };
  if (!sender) return out;
  for (const [key, d] of await outboxAll()) {
    if (d.state !== "queued" || !only(key)) continue;
    const mine = await outboxTake(key);
    if (!mine) continue; // another tab took it
    if (mine.state !== "queued") {
      await outboxPut(key, mine);
      continue;
    }
    try {
      await sender(key, { text: mine.text, expect_rev: mine.rev });
      drafts.delete(key);
      changed();
      out.sent++;
    } catch (e) {
      const state = e.code === "stale" ? "stale" : e.code === "archived" || e.code === "no_session" ? "gone" : null;
      // This page's copy is the newest: written to while the send was out.
      const back = { ...(drafts.get(key) || mine), ...(state && { state }) };
      await outboxPut(key, back);
      drafts.set(key, back);
      changed();
      if (!state) break; // the connection went again
      out[state]++;
    }
  }
  await loadDrafts();
  return out;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// One flush and one toast for it. Rounds run one after another.
export function flushNow(only) {
  const next = round.then(() => flush(only));
  round = next.catch(() => {});
  return next.then(({ sent, stale, gone }) => {
    const ask = stale + gone;
    const look = `need${ask === 1 ? "s" : ""} a look`;
    if (sent) toast(`${plural(sent, "queued message")} sent${ask ? ` · ${ask} ${look}` : ""}`);
    else if (ask) toast(`${plural(ask, "queued message")} ${look}`);
    return { sent, stale, gone };
  });
}

// A stale draft sent as written, asked for: no revision to compare.
export async function sendAnyway(key) {
  const d = drafts.get(key);
  if (!d || !sender) return;
  await sender(key, { text: d.text });
  await discard(key);
}
