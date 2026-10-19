// A session's process state as the client reads it (#479). A server older
// than the asleep state sends "stopped" or "parked" and no asleep_why; both
// read as asleep with the reason they meant. Such a server has neither
// session.restart nor a Wake for a session that did not park, so `old` marks
// what it cannot do.
const OLD = { stopped: "slept", parked: "idle" };

export function lifecycle(m) {
  const old = !("asleep_why" in m);
  if (OLD[m.state]) return { ...m, state: "asleep", asleep_why: OLD[m.state], old };
  return old ? { ...m, asleep_why: null, old } : m;
}
