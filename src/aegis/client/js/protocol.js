// The client end of the aegis protocol: one websocket, calls and channels.
//
// A channel delivers a snapshot, then patches numbered from 1. A gap in the
// numbers, or a reconnect, means resubscribe, saying which revision it holds.
//
// The socket signs in with the HttpOnly cookie the server set; the page never
// holds the token.
//
// A call or subscription may name a linked `server`: the home server forwards
// it down its link and stamps `server` on what comes back (links.py). A
// subscription is keyed by its server and channel.

export const PROTO = 3;

// Channels this client asks to patch with merges: a row it holds comes back as
// {merge: {key, ...}} with only the fields that changed (channels.py, #525).
const MERGES = new Set(["sessions", "workflow-runs"]);

export class OpError extends Error {
  constructor(code, message, data = null) {
    super(`${code}: ${message}`);
    this.code = code;
    this.data = data; // what the refusal carries, such as harness_untested's version
  }
}

export class Connection {
  constructor(url, { onState = () => {} } = {}) {
    this.url = url;
    this.onState = onState;
    this.ws = null;
    this.open = false;
    this.nextId = 0;
    this.pending = new Map(); // call id -> {resolve, reject}
    this.subs = new Map(); // key -> {channel, server, seq, onSnapshot, onPatch, onError, since}
    this.watches = new Map(); // server -> the last watch message sent to it
    this.backoff = 500;
    this.stopped = false;
    this.retryTimer = null;
    this.retryAt = 0; // epoch ms of the next attempt, or 0
  }

  connect() {
    clearTimeout(this.retryTimer);
    this.retryTimer = null;
    this.retryAt = 0;
    this.onState("connecting");
    const ws = (this.ws = new WebSocket(this.url));
    ws.onopen = () => ws.send(JSON.stringify({ t: "hello", proto: PROTO }));
    ws.onmessage = (ev) => this._receive(JSON.parse(ev.data));
    ws.onclose = (ev) => this._closed(ev.code);
  }

  _receive(msg) {
    switch (msg.t) {
      case "welcome":
        this.open = true;
        this.backoff = 500;
        this.server = msg.server;
        this.build = msg.build || ""; // the client the server serves now (#499)
        this.native = msg.native === true;
        // Who this browser is here, and whose a session with no owner is (tokens.py).
        this.me = msg.me || null;
        this.owner = msg.owner || "";
        this.onState("open", msg.server);
        for (const key of this.subs.keys()) this._sendSub(key);
        for (const w of this.watches.values()) this.ws.send(JSON.stringify(w));
        break;
      case "reply": {
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new OpError(msg.error.code, msg.error.message, msg.error.data || null));
        else p.resolve(msg.result);
        break;
      }
      case "snapshot": {
        const s = this.subs.get(subKey(msg.channel, msg.server));
        if (!s) return;
        s.seq = msg.seq;
        s.onSnapshot(msg.data);
        break;
      }
      case "patch": {
        const key = subKey(msg.channel, msg.server);
        const s = this.subs.get(key);
        if (!s) return;
        if (msg.seq !== s.seq + 1) {
          this._sendSub(key); // a gap: start over from a snapshot
          return;
        }
        s.seq = msg.seq;
        s.onPatch(msg.ops);
        break;
      }
      case "error": {
        const s = msg.channel && this.subs.get(subKey(msg.channel, msg.server));
        if (s) s.onError?.(msg.error);
        else console.warn("aegis:", msg);
        break;
      }
    }
  }

  _closed(code) {
    this.open = false;
    for (const p of this.pending.values()) p.reject(new OpError("offline", "the connection closed"));
    this.pending.clear();
    if (code === 4401) {
      this.onState("unauthorized");
      return;
    }
    if (code === 4400) {
      this.onState("version");
      return;
    }
    this.onState("closed");
    if (this.stopped) return;
    this.retryAt = Date.now() + this.backoff;
    this.retryTimer = setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 5000);
  }

  // Give up on a socket that stalls without closing: closed now, every
  // pending call rejected as offline, and a reconnect scheduled. A dead
  // socket's own close event can take minutes, so it is not waited for.
  drop() {
    const ws = this.ws;
    if (!this.open || !ws) return;
    ws.onmessage = ws.onclose = null;
    ws.close();
    this._closed(1006);
  }

  // Try now instead of at the end of the backoff: the network or the app
  // came back, or the person asked.
  retryNow() {
    if (this.open || this.stopped || !this.retryTimer) return;
    this.connect();
  }

  call(op, params = {}, server = null) {
    if (!this.open) return Promise.reject(new OpError("offline", "not connected"));
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      const msg = { t: "call", id, op, params };
      if (server) msg.server = server;
      this.ws.send(JSON.stringify(msg));
    });
  }

  // A message that is neither a call nor a subscription. A watch is state, not
  // an event: the last one per server is sent again after every welcome.
  post(msg) {
    if (msg.t === "watch") this.watches.set(msg.server || "", msg);
    if (this.open) this.ws.send(JSON.stringify(msg));
  }

  // since: optional () => the revision this subscriber holds, or null. A
  // channel that keeps revisions answers a resubscribe with what changed.
  subscribe(channel, onSnapshot, onPatch, onError, since, server = null) {
    const key = subKey(channel, server);
    this.subs.set(key, { channel, server, seq: 0, onSnapshot, onPatch, onError, since });
    if (this.open) this._sendSub(key);
    return () => {
      if (this.subs.get(key)?.onSnapshot !== onSnapshot) return; // replaced since
      this.subs.delete(key);
      if (this.open) this.ws.send(JSON.stringify(server ? { t: "unsub", channel, server } : { t: "unsub", channel }));
    };
  }

  // A linked server came back: its subscriptions ended with the link, so ask
  // again, each with the revision it holds.
  resubscribe(server) {
    if (!this.open) return;
    for (const [key, s] of this.subs) if (s.server === server) this._sendSub(key);
  }

  _sendSub(key) {
    const s = this.subs.get(key);
    if (!s) return;
    const rev = s.since?.();
    const msg = { t: "sub", channel: s.channel };
    if (s.server) msg.server = s.server;
    if (Number.isInteger(rev)) msg.since = rev;
    if (MERGES.has(s.channel)) msg.merge = true;
    this.ws.send(JSON.stringify(msg));
  }
}

function subKey(channel, server) {
  return server ? `${server}\u0000${channel}` : channel;
}
