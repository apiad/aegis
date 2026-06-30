// Pure formatter for the always-on queue strip. Each queue → a compact
// summary string: name + running (▸) / queued (⏳) / ok (✓) / err (✗).

export function formatStrip(queues) {
  return (queues || []).map(
    (q) => `${q.name} ▸${q.running} ⏳${q.queued} ✓${q.ok} ✗${q.err}`);
}
