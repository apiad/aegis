// The status badges, as one inline SVG sprite: Unicode marks render differently
// in each font. Python decides a session's attention; this only maps it to a
// symbol and a word. Drawn in currentColor; the knocked-out glyph inside a badge
// uses --knock, which base.css sets to the theme's background.

import { MARK } from "./mark.js";

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
<symbol id="g-close" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></symbol>
<symbol id="g-latest" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v8M4.6 7.3L8 10.7l3.4-3.4M3.8 13.5h8.4"/></symbol>
<symbol id="g-start" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13.5v-8M4.6 8.7L8 5.3l3.4 3.4M3.8 2.5h8.4"/></symbol>
<symbol id="g-fold" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3.5h10M3 12.5h10M5.5 6.5L8 9l2.5-2.5"/></symbol>
<symbol id="g-bell" viewBox="0 0 16 16"><path d="M8 2.5a3.5 3.5 0 0 0-3.5 3.5v2.6L3.2 11h9.6l-1.3-2.4V6A3.5 3.5 0 0 0 8 2.5zM6.6 12.6a1.5 1.5 0 0 0 2.8 0" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></symbol>
<symbol id="g-gear" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M2.5 4.5h3M9.5 4.5h4M2.5 11.5h6M12.5 11.5h1"/><circle cx="7.5" cy="4.5" r="1.8"/><circle cx="10.5" cy="11.5" r="1.8"/></symbol>
<symbol id="g-work" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><circle cx="8" cy="8" r="5.4" opacity=".25"/><path d="M8 2.6a5.4 5.4 0 0 1 5.4 5.4"/></symbol>
<symbol id="g-copy" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><rect x="5.5" y="5.5" width="8" height="8" rx="1.6"/><path d="M10.5 5.5V3.9a1.4 1.4 0 0 0-1.4-1.4H3.9a1.4 1.4 0 0 0-1.4 1.4v5.2a1.4 1.4 0 0 0 1.4 1.4h1.6"/></symbol>
<symbol id="g-sparkle" viewBox="0 0 16 16" fill="currentColor"><path d="M7 1.8l1.2 3.4 3.4 1.2-3.4 1.2L7 11 5.8 7.6 2.4 6.4l3.4-1.2z"/><path d="M12.2 9.6l.6 1.6 1.6.6-1.6.6-.6 1.6-.6-1.6-1.6-.6 1.6-.6z"/></symbol>
<symbol id="g-send" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 3v4.6a2 2 0 0 1-2 2H3.8M6.6 6.8L3.8 9.6l2.8 2.8"/></symbol>
<symbol id="g-stop" viewBox="0 0 16 16"><rect x="3.5" y="3.5" width="9" height="9" rx="1.8" fill="currentColor"/></symbol>
<symbol id="g-prompt" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 3.5L10 8l-4.5 4.5"/></symbol>
<symbol id="g-think" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2.5v11M3.24 5.25l9.52 5.5M3.24 10.75l9.52-5.5"/></symbol>
<symbol id="g-caret-r" viewBox="0 0 16 16" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"><path d="M6 4.5l4.5 3.5L6 11.5z"/></symbol>
<symbol id="g-caret-d" viewBox="0 0 16 16" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"><path d="M4.5 6h7L8 10.5z"/></symbol>
<symbol id="g-left" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.5L5.5 8l4.5 4.5"/></symbol>
<symbol id="g-right" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 3.5L10.5 8 6 12.5"/></symbol>
<symbol id="g-arrow-up" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13V3.5M4 7.5l4-4 4 4"/></symbol>
<symbol id="g-arrow" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 8h9.5M8.5 4l4 4-4 4"/></symbol>
<symbol id="g-open" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9.5 2.5h4v4M13.5 2.5L7.5 8.5M11.5 9.5v2.5a1.5 1.5 0 0 1-1.5 1.5H4A1.5 1.5 0 0 1 2.5 12V6A1.5 1.5 0 0 1 4 4.5h2.5"/></symbol>
<symbol id="g-window" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M2 6.2h12"/></symbol>
<symbol id="g-again" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 8A5.5 5.5 0 1 1 11.9 4.1L13.5 5.7M13.5 2.5v3.2h-3.2"/></symbol>
<symbol id="g-file" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 1.8h5.2L12.5 5v8.2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.8a1 1 0 0 1 1-1zM9 2v3.3h3.3M5.5 8.5h4.5M5.5 11h4.5"/></symbol>
<symbol id="g-artifact" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="11" height="11" rx="2"/><rect x="5.5" y="5.5" width="5" height="5" rx=".8" fill="currentColor" stroke="none"/></symbol>
<symbol id="g-book" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4.5C6.6 3.4 4.6 3 2.5 3v9c2.1 0 4.1.4 5.5 1.5 1.4-1.1 3.4-1.5 5.5-1.5V3c-2.1 0-4.1.4-5.5 1.5zM8 4.5v9"/></symbol>
<symbol id="g-pencil" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.6 2.7l2.7 2.7-7.7 7.7-3.3.6.6-3.3zM9.2 4.1l2.7 2.7"/></symbol>
<symbol id="g-terminal" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.8"/><path d="M4.8 6.2l2 1.8-2 1.8M8.8 10h2.6"/></symbol>
<symbol id="g-search" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.3"/><path d="M10.2 10.2l3.3 3.3"/></symbol>
<symbol id="g-globe" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.8"/><path d="M2.2 8h11.6M8 2.2c-1.7 1.6-2.5 3.5-2.5 5.8s.8 4.2 2.5 5.8c1.7-1.6 2.5-3.5 2.5-5.8S9.7 3.8 8 2.2z"/></symbol>
<symbol id="g-swap" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5.5h9.5M10 3l2.5 2.5L10 8M13 10.5H3.5M6 8l-2.5 2.5L6 13"/></symbol>
<symbol id="g-slash" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10.5 2.5l-5 11"/></symbol>
<symbol id="g-dot" viewBox="0 0 16 16"><circle cx="8" cy="8" r="3.4" fill="currentColor"/></symbol>
<symbol id="g-pip" viewBox="0 0 16 16"><circle cx="8" cy="8" r="1.5" fill="currentColor"/></symbol>
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

// A plain symbol by name: the read marks, the navigator's arrows, copy, the
// buttons, and the transcript's gutter, whose names Python sends (describe.py).
export function icon(name) {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", `ic i-${name}`); // prefixed: a bare "send" or "open" took the button's rules
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(NS, "use");
  use.setAttribute("href", `#g-${name}`);
  svg.append(use);
  return svg;
}

// An entry's glyph, which Python names (describe.py). A linked server older
// than the names still sends a character, which stays text.
export function entryIcon(name) {
  return document.getElementById(`g-${name}`) ? icon(name) : document.createTextNode(name || "");
}

// The Gorgoneion (mark.js) in the theme: the head and serpents in
// currentColor, the eye knocked out in the background. The favicon keeps
// its fixed amber; a page copy follows the theme.
export function gorgoneion() {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "gorgon");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = MARK.replaceAll('fill="#e0a872"', 'fill="currentColor"').replaceAll('fill="#11100e"', 'style="fill:var(--bg)"');
  return svg;
}
