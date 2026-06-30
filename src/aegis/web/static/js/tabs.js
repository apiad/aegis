// Pure tab reconciliation: diff the current tab handles against the latest
// session_list snapshot. Returns the sessions to open as tabs and the
// handles whose tabs should close. Order is preserved from each input.

export function reconcileTabs(existingHandles, sessions) {
  const have = new Set(existingHandles);
  const want = new Set(sessions.map((s) => s.handle));
  const added = sessions.filter((s) => !have.has(s.handle));
  const removed = existingHandles.filter((h) => !want.has(h));
  return { added, removed };
}
