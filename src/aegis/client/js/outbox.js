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
// reconnect), "sending" (its send has no answer yet), "stale" (the session
// moved on), "gone" (the session is closed) or "error" (refused for another
// reason, in `error`). A draft leaves the device only when a send's answer
// says it arrived: a page closed mid-send leaves it "sending", and the next
// page puts it back in queue; sent again with the same revision, it asks
// instead of arriving twice, since the first send's own row moved past it.

import { outboxAll, outboxAppend, outboxClaim, outboxDelete, outboxSettle } from "./devicestore.js";
import { toast } from "./toast.js";

const drafts = new Map(); // key -> draft, this page's copy of the store
const listeners = [];
let sender = null; // (key, params) -> the session.send call, set by app.js
let round = Promise.resolve();
// Codes that mean the server was out of reach, not that it refused: the draft
// goes back in queue and the walk stops for that round.
const UNREACHED = new Set(["offline", "server_offline", "timeout"]);
const SENDING_STALE_MS = 30000; // a send this old with no answer: its page died

const changed = () => listeners.forEach((fn) => fn());

// Called when any draft is added, changed or removed.
export const onDrafts = (fn) => listeners.push(fn);

export const draftFor = (key) => drafts.get(key) || null;

// The store's drafts into this page, at boot and after every change. A draft
// left "sending" by a page that closed mid-send goes back in queue.
export async function loadDrafts() {
  const all = await outboxAll();
  drafts.clear();
  for (const [key, d] of all) {
    if (d.state === "sending" && Date.now() - (d.sent_at || 0) > SENDING_STALE_MS) {
      await outboxSettle(key, "queued");
      d.state = "queued";
    }
    drafts.set(key, d);
  }
  changed();
}

// `rev` is the transcript revision the page held for the session, or -1 when
// it held none: then any content at all makes the draft ask. `nonce` is that
// of a send of this text that got no answer: the draft goes out under it, so
// if that send did arrive the server answers without delivering it twice.
export async function queue(key, text, rev, nonce) {
  await outboxAppend(key, text, rev, nonce);
  await loadDrafts();
}

export async function discard(key) {
  await outboxDelete(key);
  await loadDrafts();
}

export const setSender = (fn) => (sender = fn);

// One send of a claimed draft, settled by its answer. Answers the state it
// settled in, or "unreached" when the server could not be reached.
async function deliver(key, d, params) {
  try {
    await sender(key, params);
    await outboxSettle(key, "sent");
    return "sent";
  } catch (e) {
    if (UNREACHED.has(e.code)) {
      await outboxSettle(key, "queued");
      return "unreached";
    }
    const state = e.code === "stale" ? "stale" : e.code === "archived" || e.code === "no_session" ? "gone" : "error";
    await outboxSettle(key, state, state === "error" ? e.message : "");
    return state;
  }
}

// Send every queued draft whose key `only` accepts. Claiming marks it
// "sending" in one transaction, so of two tabs flushing at once only one sends.
export async function flush(only = () => true) {
  const out = { sent: 0, stale: 0, gone: 0, error: 0 };
  if (!sender) return out;
  // A send with no answer for SENDING_STALE_MS: its page died mid-send.
  const stuck = (d) => d.state === "sending" && Date.now() - (d.sent_at || 0) > SENDING_STALE_MS;
  for (const [key, d] of await outboxAll()) {
    if ((d.state !== "queued" && !stuck(d)) || !only(key)) continue;
    const mine = await outboxClaim(key, (x) => x.state === "queued" || stuck(x));
    if (!mine) continue; // another tab took it
    await loadDrafts();
    const got = await deliver(key, mine, { text: mine.text, expect_rev: mine.rev, ...(mine.nonce && { nonce: mine.nonce }) });
    if (got === "unreached") break;
    out[got]++;
  }
  await loadDrafts();
  return out;
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// One flush and one toast for it. Rounds run one after another.
export function flushNow(only) {
  const next = round.then(() => flush(only));
  round = next.catch(() => {});
  return next.then((out) => {
    const { sent, stale, gone, error } = out;
    const ask = stale + gone + error;
    const look = `need${ask === 1 ? "s" : ""} a look`;
    if (sent) toast(`${plural(sent, "queued message")} sent${ask ? ` · ${ask} ${look}` : ""}`);
    else if (ask) toast(`${plural(ask, "queued message")} ${look}`);
    return out;
  });
}

// A draft sent as written, asked for: no revision to compare. Claimed first,
// so a second tap while the first send is out does nothing.
export async function sendAnyway(key) {
  if (!sender) return;
  const mine = await outboxClaim(key, (d) => ["stale", "error", "queued"].includes(d.state));
  if (!mine) return;
  await loadDrafts();
  const got = await deliver(key, mine, { text: mine.text, ...(mine.nonce && { nonce: mine.nonce }) });
  await loadDrafts();
  if (got === "unreached") toast("Not sent: the connection went", { kind: "error" });
}
