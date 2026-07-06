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

// Next/prev tab with wraparound (dir = +1 / -1). Mirrors the TUI's
// ctrl+tab / ctrl+right / ctrl+left navigation.
export function cycleHandle(handles, current, dir) {
  if (!handles.length) return null;
  const i = handles.indexOf(current);
  if (i === -1) return handles[0];
  const n = handles.length;
  return handles[(i + dir + n) % n];
}

// 1-based tab index → handle (ctrl+1..9), or null when out of range.
export function gotoHandle(handles, n) {
  return handles[n - 1] ?? null;
}

// Classify a touch gesture into a tab direction. +1 = next (swipe left),
// -1 = prev (swipe right), 0 = ignore (too short, or vertical-dominant so
// transcript scrolling is never hijacked).
export function swipeDirection(dx, dy, threshold = 60) {
  if (Math.abs(dx) < threshold || Math.abs(dx) <= Math.abs(dy)) return 0;
  return dx < 0 ? 1 : -1;
}
