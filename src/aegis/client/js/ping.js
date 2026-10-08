// The ping outside the page: the count of sessions that need you or broke, in
// the document title and on the favicon, and a desktop notification when a
// session enters one of those states while the page is hidden. The states are
// decided in Python; this only counts them and notices transitions.

const URGENT = new Set(["needs_you", "error"]);
let count = 0;
let base = "aegis";
let seen = null; // log_id -> attention, after the first draw

export function setTitle(text) {
  base = text;
  document.title = count ? `(${count}) ${base}` : base;
}

function favicon(n) {
  const accent = getComputedStyle(document.getElementById("a2")).getPropertyValue("--accent").trim();
  const dot = n ? `<circle id="dot" cx="25" cy="7" r="6" fill="${accent}" stroke="#11100e" stroke-width="2"/>` : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect x="2" y="4" width="24" height="24" rx="6" fill="#2a2721"/><text x="14" y="22" font-family="sans-serif" font-size="16" font-weight="700" text-anchor="middle" fill="#f1ede2">a</text>${dot}</svg>`;
  document.getElementById("favicon").href = `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

// The dot takes the theme's accent, so a theme switch redraws it.
export function redrawFavicon() {
  favicon(count);
}

export function updatePing(metas, { onOpen }) {
  const n = metas.filter((m) => URGENT.has(m.attention)).length;
  if (n !== count || seen === null) {
    count = n;
    setTitle(base);
    favicon(n);
  }
  const now = new Map(metas.map((m) => [m.log_id, m.attention]));
  if (seen !== null && document.hidden && "Notification" in window && Notification.permission === "granted") {
    for (const m of metas) {
      if (!URGENT.has(m.attention) || seen.get(m.log_id) === m.attention) continue;
      const note = new Notification(`${m.title || m.handle}: ${m.attention === "error" ? "error" : "needs you"}`, {
        body: m.attention_line || "",
        tag: `${m.log_id}:${m.attention}:${m.attention_line || ""}`,
      });
      note.onclick = () => {
        window.focus();
        onOpen(m.log_id);
      };
    }
  }
  seen = now;
}

export function installBell(button) {
  const draw = () => {
    const p = "Notification" in window ? Notification.permission : "denied";
    button.dataset.state = p;
    button.title = p === "granted" ? "Desktop notifications are on" : p === "denied" ? "Notifications are blocked in this browser" : "Turn on desktop notifications";
  };
  draw();
  button.addEventListener("click", async () => {
    if ("Notification" in window && Notification.permission === "default") await Notification.requestPermission();
    draw();
  });
}
