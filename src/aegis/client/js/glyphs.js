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
<symbol id="g-work" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="8" cy="8" r="5.4" opacity=".25"/><path d="M8 2.6a5.4 5.4 0 0 1 5.4 5.4"/></symbol>
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
