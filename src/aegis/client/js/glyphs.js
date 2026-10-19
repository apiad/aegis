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
<symbol id="g-btw" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5.9 5.9a2.1 2.1 0 1 1 2.9 1.95c-.55.22-.8.6-.8 1.15v.5"/><circle cx="8" cy="11.9" r=".75" fill="currentColor" stroke="none"/></symbol>
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
<symbol id="g-dock" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="10" rx="1.6"/><path d="M9.5 3v10"/></symbol>
<symbol id="g-expand" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 6V2.5H6M10 2.5h3.5V6M13.5 10v3.5H10M6 13.5H2.5V10"/></symbol>
<symbol id="g-again" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 8A5.5 5.5 0 1 1 11.9 4.1L13.5 5.7M13.5 2.5v3.2h-3.2"/></symbol>
<symbol id="g-file" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 1.8h5.2L12.5 5v8.2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.8a1 1 0 0 1 1-1zM9 2v3.3h3.3M5.5 8.5h4.5M5.5 11h4.5"/></symbol>
<symbol id="g-folder" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M1.8 4.2a1 1 0 0 1 1-1h3.4l1.5 1.6h5.5a1 1 0 0 1 1 1v6.9a1 1 0 0 1-1 1H2.8a1 1 0 0 1-1-1z"/></symbol>
<symbol id="g-file-gone" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M4 1.8h5.2L12.5 5v8.2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.8a1 1 0 0 1 1-1zM9 2v3.3h3.3M2 14.5L14 1.5"/></symbol>
<symbol id="g-github" viewBox="0 0 16 16" fill="currentColor"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></symbol>
<symbol id="g-pypi" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><path d="M8 1.5l5.5 3v7L8 14.5l-5.5-3v-7zM2.5 4.5L8 7.5l5.5-3M8 7.5v7"/></symbol>
<symbol id="g-arxiv" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 2l10 12M13 2L3 14"/></symbol>
<symbol id="g-npm" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"><rect x="1.5" y="4" width="13" height="8" rx="1"/><path d="M4 10V6h3v4M5.5 6v4M9 10V6h3v3"/></symbol>
<symbol id="g-artifact" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="11" height="11" rx="2"/><rect x="5.5" y="5.5" width="5" height="5" rx=".8" fill="currentColor" stroke="none"/></symbol>
<symbol id="g-clock" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.8"/><path d="M8 4.8V8l2.2 1.5"/></symbol>
<symbol id="g-book" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M8 4.5C6.6 3.4 4.6 3 2.5 3v9c2.1 0 4.1.4 5.5 1.5 1.4-1.1 3.4-1.5 5.5-1.5V3c-2.1 0-4.1.4-5.5 1.5zM8 4.5v9"/></symbol>
<symbol id="g-pencil" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M10.6 2.7l2.7 2.7-7.7 7.7-3.3.6.6-3.3zM9.2 4.1l2.7 2.7"/></symbol>
<symbol id="g-terminal" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="1.8" y="2.8" width="12.4" height="10.4" rx="1.8"/><path d="M4.8 6.2l2 1.8-2 1.8M8.8 10h2.6"/></symbol>
<symbol id="g-search" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="7" cy="7" r="4.3"/><path d="M10.2 10.2l3.3 3.3"/></symbol>
<symbol id="g-globe" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.8"/><path d="M2.2 8h11.6M8 2.2c-1.7 1.6-2.5 3.5-2.5 5.8s.8 4.2 2.5 5.8c1.7-1.6 2.5-3.5 2.5-5.8S9.7 3.8 8 2.2z"/></symbol>
<symbol id="g-swap" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 5.5h9.5M10 3l2.5 2.5L10 8M13 10.5H3.5M6 8l-2.5 2.5L6 13"/></symbol>
<symbol id="g-slash" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10.5 2.5l-5 11"/></symbol>
<symbol id="g-trash" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.8 4.5h10.4M6.2 4.5V3a.8.8 0 0 1 .8-.8h2a.8.8 0 0 1 .8.8v1.5M4.2 4.5l.6 8.4a1 1 0 0 0 1 .9h4.4a1 1 0 0 0 1-.9l.6-8.4M6.8 7v4.2M9.2 7v4.2"/></symbol>
<symbol id="g-quiet" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8s2-3.6 5.5-3.6c.9 0 1.7.2 2.4.6M13.5 8s-.8 1.4-2.3 2.5M6.2 11.2c.6.3 1.2.4 1.8.4 1.1 0 2-.3 2.8-.8M2.5 13.5l11-11"/></symbol>
<symbol id="g-unquiet" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 8s2-3.6 5.5-3.6S13.5 8 13.5 8s-2 3.6-5.5 3.6S2.5 8 2.5 8z"/><circle cx="8" cy="8" r="1.6"/></symbol>
<symbol id="g-interrupt" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="5.8"/><path d="M6.5 5.8v4.4M9.5 5.8v4.4"/></symbol>
<symbol id="g-play" viewBox="0 0 16 16" fill="currentColor" stroke="currentColor" stroke-width="1" stroke-linejoin="round"><path d="M5 3.5l7.5 4.5L5 12.5z"/></symbol>
<symbol id="g-sleep" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12.5 9.5A5 5 0 1 1 6.5 3.5a4 4 0 0 0 6 6z"/></symbol>
<symbol id="g-wake" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="2.6"/><path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1M11.3 4.7l1.1-1.1"/></symbol>
<symbol id="g-restart" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M13 8a5 5 0 1 1-1.6-3.7"/><path d="M13 2.5v3h-3"/></symbol>
<symbol id="g-handoff" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 5.5h8M8 3l2.5 2.5L8 8M13.5 10.5h-8M8 8l-2.5 2.5L8 13"/><path d="M13.5 3v5" opacity=".5"/></symbol>
<symbol id="g-archive" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="2.8" width="12" height="3.2" rx=".8"/><path d="M3.2 6v6.4a1 1 0 0 0 1 1h7.6a1 1 0 0 0 1-1V6M6.5 8.6h3"/></symbol>
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
// its fixed amber; a page copy follows the theme, and moves (#348): the eye,
// drawn 1.2 times the favicon's, looks left, right and blinks (base.css),
// and each serpent bends so its tip crosses from one side of its body to
// the other, the six out of phase so the wave travels round the head.
const BEAT = 4.5; // seconds per serpent bend
const FRAMES = 16; // outlines per beat
const SWING = (14 * Math.PI) / 180; // the tip's angle either side of the axis

// One serpent's bend as SMIL `values`. Every outline point turns about the
// serpent's root (the midpoint of its first and last points, under the
// disc) by an angle that grows from nothing at the root to the full swing at
// the tip, so the body bends rather than pivots. Interpolating with the
// serpent's mirror image instead collapses it to a line mid-beat, since a
// point and its reflection average onto the axis.
function bend(d) {
  const n = d.replace(/[MZ]/g, " ").trim().split(/\s+/).map(Number);
  const pts = [];
  for (let i = 0; i < n.length; i += 2) pts.push([n[i], n[i + 1]]);
  const [a, b] = [pts[0], pts[pts.length - 1]];
  const [rx, ry] = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const r = Math.hypot(rx - 32, ry - 32);
  const [ux, uy] = [(rx - 32) / r, (ry - 32) / r];
  const along = ([x, y]) => (x - rx) * ux + (y - ry) * uy;
  const tip = pts.reduce((m, q) => (along(q) > along(m) ? q : m));
  const len = along(tip);
  // How far the drawn tip already leans off the axis: the swing is centred.
  const lean = Math.atan2((tip[0] - rx) * uy - (tip[1] - ry) * ux, len);
  const frames = [];
  for (let k = 0; k <= FRAMES; k++) {
    const th = -(lean + SWING * Math.sin((2 * Math.PI * k) / FRAMES));
    const outline = pts.map(([x, y]) => {
      const t = (th * Math.max(0, along([x, y]))) / len;
      const [dx, dy, c, s] = [x - rx, y - ry, Math.cos(t), Math.sin(t)];
      return `${(rx + dx * c + dy * s).toFixed(2)} ${(ry - dx * s + dy * c).toFixed(2)}`;
    });
    frames.push(`M${outline.join(" ")}Z`);
  }
  return frames.join(";");
}

const STILL = matchMedia("(prefers-reduced-motion: reduce)");
let BENDS = null;

export function gorgoneion() {
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "gorgon");
  svg.setAttribute("viewBox", "0 0 64 64");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = MARK.replaceAll('fill="#e0a872"', 'fill="currentColor"').replaceAll('fill="#11100e"', 'style="fill:var(--bg)"');
  const [disc, snakes, eye, pupil] = svg.children;
  const outlines = snakes.getAttribute("d").split(/ (?=M)/);
  BENDS ??= outlines.map(bend);
  const serpents = outlines.map((d, i) => {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("class", "serpent");
    p.setAttribute("fill", "currentColor");
    p.setAttribute("d", d);
    // SMIL ignores CSS, so reduced motion is honoured here and not in base.css.
    if (!STILL.matches) {
      const anim = document.createElementNS(NS, "animate");
      anim.setAttribute("attributeName", "d");
      anim.setAttribute("values", BENDS[i]);
      anim.setAttribute("dur", `${BEAT}s`);
      anim.setAttribute("begin", `${(-i * BEAT) / 6}s`);
      anim.setAttribute("repeatCount", "indefinite");
      p.append(anim);
    }
    return p;
  });
  eye.setAttribute("class", "eye");
  pupil.setAttribute("class", "pupil");
  const lid = document.createElementNS(NS, "g");
  lid.setAttribute("class", "lid");
  lid.append(pupil);
  const eyes = document.createElementNS(NS, "g");
  eyes.setAttribute("transform", "translate(32 32) scale(1.2) translate(-32 -32)");
  eyes.append(eye, lid);
  snakes.replaceWith(...serpents);
  svg.append(eyes); // over the serpents' roots, as in mark.js
  return svg;
}
