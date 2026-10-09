// The status badges, as one inline SVG sprite: Unicode marks render differently
// in each font. Python decides a session's attention; this only maps it to a
// symbol and a word. Drawn in currentColor; the knocked-out glyph inside a badge
// uses --knock, which base.css sets to the theme's background.

const NS = "http://www.w3.org/2000/svg";
const K = 'style="stroke:var(--knock)" fill="none" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"';

const SPRITE = `<defs>
<symbol id="g-need" viewBox="0 0 16 16"><rect x="1" y="1" width="14" height="14" rx="4" fill="currentColor"/><path d="M6.2 5.9a1.8 1.8 0 1 1 2.5 1.65c-.5.2-.7.55-.7 1.05" ${K}/><circle cx="8" cy="11.1" r="1" style="fill:var(--knock)"/></symbol>
<symbol id="g-err" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" ${K}/></symbol>
<symbol id="g-rev" viewBox="0 0 16 16"><rect x="1" y="1" width="14" height="14" rx="4" fill="currentColor"/><path d="M3.4 8s1.8-3 4.6-3 4.6 3 4.6 3-1.8 3-4.6 3S3.4 8 3.4 8z" style="fill:var(--knock)"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/></symbol>
<symbol id="g-wait" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor" opacity=".28"/><path d="M8 4.4V8l2.4 1.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></symbol>
<symbol id="g-done" viewBox="0 0 16 16"><circle cx="8" cy="8" r="7" fill="currentColor"/><path d="M5 8.3l2 2 4-4.3" ${K}/></symbol>
<symbol id="g-unread" viewBox="0 0 16 16"><circle cx="8" cy="8" r="3.2" fill="currentColor"/></symbol>
<symbol id="g-read" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3.6 8.4l2.9 2.9 5.9-6.4"/></symbol>
<symbol id="g-up" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 10l4-4 4 4"/></symbol>
<symbol id="g-down" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></symbol>
<symbol id="g-latest" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v8M4.6 7.3L8 10.7l3.4-3.4M3.8 13.5h8.4"/></symbol>
<symbol id="g-fold" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3.5h10M3 12.5h10M5.5 6.5L8 9l2.5-2.5"/></symbol>
<symbol id="g-bell" viewBox="0 0 16 16"><path d="M8 2.5a3.5 3.5 0 0 0-3.5 3.5v2.6L3.2 11h9.6l-1.3-2.4V6A3.5 3.5 0 0 0 8 2.5zM6.6 12.6a1.5 1.5 0 0 0 2.8 0" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></symbol>
<symbol id="g-work" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="8" cy="8" r="5.4" opacity=".25"/><path d="M8 2.6a5.4 5.4 0 0 1 5.4 5.4"/></symbol>
<symbol id="g-sparkle" viewBox="0 0 16 16" fill="currentColor"><path d="M7 1.8l1.2 3.4 3.4 1.2-3.4 1.2L7 11 5.8 7.6 2.4 6.4l3.4-1.2z"/><path d="M12.2 9.6l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z"/></symbol>
</defs>`;

const SYMBOL = { working: "work", needs_you: "need", error: "err", review: "rev", waiting: "wait", done: "done" };

export const LABEL = {
  working: "working",
  needs_you: "needs you",
  error: "error",
  review: "review",
  waiting: "waiting",
  done: "done",
};

export function installGlyphs() {
  if (document.getElementById("glyphs")) return;
  const svg = document.createElementNS(NS, "svg");
  svg.id = "glyphs";
  svg.setAttribute("width", "0");
  svg.setAttribute("height", "0");
  svg.setAttribute("aria-hidden", "true");
  svg.style.position = "absolute";
  svg.innerHTML = SPRITE;
  document.body.prepend(svg);
}

export function glyph(attention) {
  const kind = SYMBOL[attention] || "done";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", `ic ${kind}`);
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#g-${kind}`);
  svg.append(use);
  return svg;
}

// A plain symbol by name: the read marks and the navigator's arrows.
export function icon(name) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", `ic ${name}`);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#g-${name}`);
  svg.append(use);
  return svg;
}
