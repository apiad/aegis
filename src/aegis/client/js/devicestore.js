// What this device keeps of what it has seen, for reading with no connection
// and for a cold start that asks the server only for what changed: the last
// Fleet, the KEEP transcripts saved most recently (as the wire carried them,
// without collapsed detail), and the outbox of messages written offline
// (outbox.js). One IndexedDB database per origin. Every call resolves, never
// rejects: a browser without IndexedDB, or with storage blocked, keeps
// nothing and aegis works online as before.

const DB = "aegis";
const VERSION = 1;
const KEEP = 20;
let opening = null;

function open() {
  if (!opening)
    opening = new Promise((resolve) => {
      if (!globalThis.indexedDB) return resolve(null);
      let req;
      try {
        req = indexedDB.open(DB, VERSION);
      } catch {
        return resolve(null);
      }
      req.onupgradeneeded = () => {
        const db = req.result;
        for (const name of ["fleet", "transcripts", "outbox"]) if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
        const tr = req.transaction.objectStore("transcripts");
        if (!tr.indexNames.contains("seq")) tr.createIndex("seq", "seq");
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      req.onblocked = () => resolve(null);
    });
  return opening;
}

// One transaction on `store`; `fn(os)` returns an IDBRequest whose result is
// the answer, or nothing. Any failure answers null.
async function run(store, mode, fn) {
  const db = await open();
  if (!db) return null;
  return new Promise((resolve) => {
    let out = null;
    try {
      const tx = db.transaction(store, mode);
      const req = fn(tx.objectStore(store));
      if (req) req.onsuccess = () => (out = req.result ?? null);
      tx.oncomplete = () => resolve(out);
      tx.onerror = tx.onabort = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

export const saveFleet = (metas) => run("fleet", "readwrite", (os) => os.put({ metas, saved_at: Date.now() }, "home"));
export const loadFleet = () => run("fleet", "readonly", (os) => os.get("home"));
export const loadTranscript = (key) => run("transcripts", "readonly", (os) => os.get(key));

// One transaction: the next `seq` (two saves can share a millisecond, so
// saved_at alone cannot order them), the put, and the oldest beyond KEEP
// dropped. Walks the `seq` index's keys only: reading every record would
// read every kept transcript's entries on each save.
export const saveTranscript = (key, { entries, rev }) =>
  run("transcripts", "readwrite", (os) => {
    const bySeq = os.index("seq");
    bySeq.openKeyCursor(null, "prev").onsuccess = (ev) => {
      const seq = (ev.target.result?.key ?? 0) + 1;
      os.put({ entries, rev, saved_at: Date.now(), seq }, key).onsuccess = () => {
        os.count().onsuccess = (c) => {
          let extra = c.target.result - KEEP;
          if (extra <= 0) return;
          bySeq.openKeyCursor().onsuccess = (e) => {
            const cur = e.target.result;
            if (!cur || extra-- <= 0) return;
            os.delete(cur.primaryKey);
            cur.continue();
          };
        };
      };
    };
  });

export async function clearDevice() {
  for (const name of ["fleet", "transcripts", "outbox"]) await run(name, "readwrite", (os) => os.clear());
}

// The outbox store, for outbox.js (slice 4): one draft per session key.
export const outboxPut = (key, draft) => run("outbox", "readwrite", (os) => os.put(draft, key));
export const outboxDelete = (key) => run("outbox", "readwrite", (os) => os.delete(key));
// The draft for `key`, deleted in the same transaction: of two tabs flushing
// at once, one gets it and the other gets null.
export const outboxTake = (key) =>
  run("outbox", "readwrite", (os) => {
    const req = os.get(key);
    req.addEventListener("success", () => req.result !== undefined && os.delete(key));
    return req;
  });
export async function outboxAll() {
  const vals = await run("outbox", "readonly", (os) => os.getAll());
  const keys = await run("outbox", "readonly", (os) => os.getAllKeys());
  return vals && keys ? keys.map((k, i) => [k, vals[i]]) : [];
}
