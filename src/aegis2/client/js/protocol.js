// The client end of the aegis2 protocol: one websocket, calls and channels.
//
// A channel delivers a snapshot, then patches numbered from 1. A gap in the
// numbers, or a reconnect, means resubscribe and take a fresh snapshot.

export const PROTO = 1;

export class OpError extends Error {
  constructor(code, message) {
    super(`${code}: ${message}`);
    this.code = code;
  }
}

export class Connection {
  constructor(url, token, { onState = () => {} } = {}) {
    this.url = url;
    this.token = token;
    this.onState = onState;
    this.ws = null;
    this.open = false;
    this.nextId = 0;
    this.pending = new Map(); // call id -> {resolve, reject}
    this.subs = new Map(); // channel -> {seq, onSnapshot, onPatch, onError}
    this.backoff = 500;
    this.stopped = false;
  }

  connect() {
    this.onState("connecting");
    const ws = (this.ws = new WebSocket(this.url));
    ws.onopen = () => ws.send(JSON.stringify({ t: "hello", token: this.token, proto: PROTO }));
    ws.onmessage = (ev) => this._receive(JSON.parse(ev.data));
    ws.onclose = (ev) => this._closed(ev.code);
  }

  _receive(msg) {
    switch (msg.t) {
      case "welcome":
        this.open = true;
        this.backoff = 500;
        this.server = msg.server;
        this.onState("open", msg.server);
        for (const channel of this.subs.keys()) this._sendSub(channel);
        break;
      case "reply": {
        const p = this.pending.get(msg.id);
        if (!p) return;
        this.pending.delete(msg.id);
        if (msg.error) p.reject(new OpError(msg.error.code, msg.error.message));
        else p.resolve(msg.result);
        break;
      }
      case "snapshot": {
        const s = this.subs.get(msg.channel);
        if (!s) return;
        s.seq = msg.seq;
        s.onSnapshot(msg.data);
        break;
      }
      case "patch": {
        const s = this.subs.get(msg.channel);
        if (!s) return;
        if (msg.seq !== s.seq + 1) {
          this._sendSub(msg.channel); // a gap: start over from a snapshot
          return;
        }
        s.seq = msg.seq;
        s.onPatch(msg.ops);
        break;
      }
      case "error":
        if (msg.channel && this.subs.has(msg.channel)) this.subs.get(msg.channel).onError?.(msg.error);
        else console.warn("aegis2:", msg);
        break;
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
    setTimeout(() => this.connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 5000);
  }

  call(op, params = {}) {
    if (!this.open) return Promise.reject(new OpError("offline", "not connected"));
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ t: "call", id, op, params }));
    });
  }

  subscribe(channel, onSnapshot, onPatch, onError) {
    this.subs.set(channel, { seq: 0, onSnapshot, onPatch, onError });
    if (this.open) this._sendSub(channel);
    return () => {
      this.subs.delete(channel);
      if (this.open) this.ws.send(JSON.stringify({ t: "unsub", channel }));
    };
  }

  _sendSub(channel) {
    this.ws.send(JSON.stringify({ t: "sub", channel }));
  }
}
