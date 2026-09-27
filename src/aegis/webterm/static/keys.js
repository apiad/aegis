// What the on-screen keys send. A phone keyboard has no Esc, Tab, Ctrl or
// arrows, and the TUI needs all four.
export const KEYS = {
  esc: "\x1b", tab: "\t",
  up: "\x1b[A", down: "\x1b[B", right: "\x1b[C", left: "\x1b[D",
};

// Ctrl+<letter> is the letter's code minus 64: Ctrl+C is 0x03. Only one
// character is a keystroke; anything longer is a paste and passes through.
export function ctrl(text) {
  if (text.length !== 1) return text;
  const code = text.toUpperCase().charCodeAt(0);
  return code >= 64 && code <= 95 ? String.fromCharCode(code - 64) : text;
}
