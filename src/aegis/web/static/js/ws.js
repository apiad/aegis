// WebSocket client for the aegis web frontend.
//
// Owns one socket: auth handshake, rpc-as-promises, subscribe, and
// reconnect-with-backoff that re-auths then `resume`s every tracked
// subscription from its last seen seq. Stream frames are dispatched to
// handlers registered with on(kind, fn). Theme/render state lives elsewhere;
// this module only moves frames.

export class WSClient {
  constructor(url, token) {
    this.url = url;
    this.token = token;
    this.ws = null;
    this.connected = false;
    this.constants = {};
    this._rpcId = 0;
    this._pending = new Map();      // id -> {resolve, reject}
    this._handlers = new Map();     // kind -> Set<fn>
    this._subs = new Map();         // handle -> last_seq
    this._globals = new Set();
    this._backoff = 500;
    this._authed = false;           // got hello at least once
  }

  on(kind, fn) {
    if (!this._handlers.has(kind)) this._handlers.set(kind, new Set());
    this._handlers.get(kind).add(fn);
  }

  _dispatch(kind, frame) {
    const set = this._handlers.get(kind);
    if (set) for (const fn of set) fn(frame);
  }

  connect() {
    return new Promise((resolve, reject) => {
      this._helloResolve = resolve;
      this._helloReject = reject;
      this._open();
    });
  }

  _open() {
    this.ws = new WebSocket(this.url);
    this.ws.onopen = () => {
      this.ws.send(JSON.stringify({ type: "auth", token: this.token }));
    };
    this.ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      this._handle(msg);
    };
    this.ws.onclose = () => {
      this.connected = false;
      if (!this._authed) {
        // initial connect failed (e.g. bad token → 4401). Do not loop.
        if (this._helloReject) { this._helloReject(new Error("auth failed")); this._helloReject = null; }
        return;
      }
      this._scheduleReconnect();
    };
    this.ws.onerror = () => { /* onclose handles recovery */ };
  }

  _handle(msg) {
    if (msg.type === "hello") {
      this.connected = true;
      this._authed = true;
      this._backoff = 500;
      this.constants = msg.constants || {};
      this._resume();
      if (this._helloResolve) { this._helloResolve(msg); this._helloResolve = null; }
      this._dispatch("hello", msg);
      return;
    }
    if (msg.type === "rpc_response") {
      const p = this._pending.get(msg.id);
      if (p) {
        this._pending.delete(msg.id);
        if (msg.ok) p.resolve(msg.result);
        else p.reject(new Error(msg.error || "rpc failed"));
      }
      return;
    }
    if (msg.type === "error") {
      const p = msg.id != null ? this._pending.get(msg.id) : null;
      if (p) { this._pending.delete(msg.id); p.reject(new Error(msg.message || msg.code)); }
      return;
    }
    if (msg.type === "stream") {
      if (msg.handle && msg.seq != null) this._subs.set(msg.handle, msg.seq);
      this._dispatch(msg.kind, msg);
    }
  }

  rpc(method, params = {}) {
    const id = ++this._rpcId;
    return new Promise((resolve, reject) => {
      this._pending.set(id, { resolve, reject });
      this._send({ type: "rpc", id, method, params });
    });
  }

  subscribe(handle) {
    if (!this._subs.has(handle)) this._subs.set(handle, 0);
    this._send({ type: "subscribe", target: { kind: "session", handle } });
  }

  subscribeGlobal(stream) {
    this._globals.add(stream);
    this._send({ type: "subscribe", target: { kind: "global", stream } });
  }

  _resume() {
    const subs = [...this._subs.entries()].map(
      ([handle, last_seq]) => ({ handle, last_seq }));
    if (subs.length || this._globals.size) {
      this._send({ type: "resume", subscriptions: subs,
                   globals: [...this._globals] });
    }
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  _scheduleReconnect() {
    const delay = Math.min(this._backoff, 10000);
    this._backoff = Math.min(this._backoff * 2, 10000);
    setTimeout(() => this._open(), delay);
  }
}
