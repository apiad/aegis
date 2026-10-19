// A session's process state as the client reads it (#479). A server older
// than the asleep state sends "stopped" or "parked" and no asleep_why; both
// read as asleep with the reason they meant. Such a server has neither
// session.restart nor a Wake for a session that did not park, so `old` marks
// what it cannot do.
const OLD = { stopped: "slept", parked: "idle" };

// "idle 20 min": how long a parked session sat idle before its park, when
// the server says (parked_idle_s); "" when it does not.
export function idleFor(m) {
  const mins = Math.max(1, Math.round((m.parked_idle_s || 0) / 60));
  return m.parked_idle_s ? (mins >= 120 ? `${Math.round(mins / 60)} h` : `${mins} min`) : "";
}

export function lifecycle(m) {
  const old = !("asleep_why" in m);
  if (OLD[m.state]) return { ...m, state: "asleep", asleep_why: OLD[m.state], old };
  return old ? { ...m, asleep_why: null, old } : m;
}
