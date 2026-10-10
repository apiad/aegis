// Files a person attaches in the composer: a tray of chips per session, each
// uploading in 256 KB base64 chunks (attachment.put) as soon as it is added,
// so Send waits only for what is still in flight. The server stages them
// until session.send names them (attachments.py). A chunk resent after a
// reconnect is acknowledged by the server, never written twice. With `hold`
// (the new-tab composer, whose session does not exist yet) chips only hold
// their files until take() hands them to the session that was started.

import { icon } from "./glyphs.js";

const CHUNK = 256 * 1024;
const PARALLEL = 3;

function human(n) {
  if (n < 1024) return `${n} B`;
  const kb = n / 1024;
  if (kb < 1024) return `${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB`;
  const mb = kb / 1024;
  return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`;
}

function base64(buf) {
  const bytes = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

export class Attachments {
  // call(key, op, params) runs an operation on the server holding the
  // session keyed `key`; waitOnline() resolves once the socket is open again.
  constructor(tray, { call, waitOnline, hold = false }) {
    this.tray = tray;
    this.call = call;
    this.waitOnline = waitOnline;
    this.hold = hold;
    this.byKey = new Map();
    this.key = null;
    this.queue = [];
    this.running = 0;
    this.waiters = [];
  }

  show(key) {
    this.key = key;
    this.draw();
  }

  list(key = this.key) {
    return this.byKey.get(key) || [];
  }

  add(files, key = this.key) {
    if (!key) return;
    const items = this.list(key).slice();
    for (const file of files) {
      const state = this.hold ? "held" : "queued";
      const it = { key, file, name: file.name, size: file.size, sent: 0, upload_id: null, state, error: "" };
      items.push(it);
      if (!this.hold) this.queue.push(it);
    }
    this.byKey.set(key, items);
    if (key === this.key) this.draw();
    this.pump();
  }

  // The held files of `key`, in order, and the chips gone.
  take(key) {
    const files = this.list(key).map((it) => it.file);
    this.clear(key);
    return files;
  }

  pump() {
    while (this.running < PARALLEL && this.queue.length) {
      const it = this.queue.shift();
      if (it.state !== "queued") continue;
      this.running++;
      this.upload(it).finally(() => {
        this.running--;
        this.wake();
        this.pump();
      });
    }
  }

  async upload(it) {
    it.state = "uploading";
    this.draw();
    try {
      if (!it.upload_id) {
        const r = await this.call(it.key, "attachment.begin", { name: it.name, size: it.size });
        it.upload_id = r.upload_id;
      }
      while (it.sent < it.size) {
        if (it.state === "removed") return;
        const end = Math.min(it.sent + CHUNK, it.size);
        const data = base64(await it.file.slice(it.sent, end).arrayBuffer());
        try {
          const r = await this.call(it.key, "attachment.put", { upload_id: it.upload_id, offset: it.sent, data });
          it.sent = r.size;
        } catch (e) {
          if (e.code !== "offline") throw e;
          await this.waitOnline(); // then the same chunk again
        }
        this.draw();
      }
      if (it.state !== "removed") it.state = "done";
    } catch (e) {
      if (it.state === "removed") return;
      it.state = "failed";
      it.error = e.message;
    }
    this.draw();
  }

  retry(it) {
    Object.assign(it, { state: "queued", error: "", upload_id: null, sent: 0 });
    this.queue.push(it);
    this.draw();
    this.pump();
  }

  remove(it) {
    this.byKey.set(it.key, this.list(it.key).filter((x) => x !== it));
    const had = it.upload_id && it.state !== "failed";
    it.state = "removed";
    if (had) this.call(it.key, "attachment.drop", { upload_id: it.upload_id }).catch(() => {});
    this.draw();
    this.wake();
  }

  wake() {
    const waiting = this.waiters;
    this.waiters = [];
    for (const resolve of waiting) resolve();
  }

  // Resolves once no chip of `key` is queued or uploading.
  async settled(key) {
    const busy = () => this.list(key).some((x) => x.state === "queued" || x.state === "uploading");
    while (busy()) await new Promise((resolve) => this.waiters.push(resolve));
  }

  // What Send may carry: the upload ids, or why it may not send yet.
  ready(key) {
    const items = this.list(key);
    if (items.some((x) => x.state === "failed")) return { error: "an attachment failed; click it to retry or remove it" };
    if (items.some((x) => x.state !== "done")) return { error: "attachments are still uploading" };
    return { ids: items.map((x) => x.upload_id) };
  }

  clear(key) {
    this.byKey.delete(key);
    if (key === this.key) this.draw();
  }

  draw() {
    const items = this.list();
    this.tray.hidden = items.length === 0;
    this.tray.replaceChildren(...items.map((it) => this.chip(it)));
  }

  chip(it) {
    const c = el("span", `chip att ${it.state}`);
    c.title = it.error || it.name;
    const bar = el("span", "ab");
    bar.style.setProperty("--p", it.size ? (it.sent / it.size).toFixed(3) : "1");
    const x = el("button", "ax");
    x.append(icon("close"));
    x.type = "button";
    x.title = "Remove";
    x.setAttribute("aria-label", `Remove ${it.name}`);
    x.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.remove(it);
    });
    if (it.state === "failed") c.addEventListener("click", () => this.retry(it));
    c.append(el("span", "an", it.name), el("span", "as", it.state === "failed" ? "failed · retry" : human(it.size)), bar, x);
    return c;
  }
}
