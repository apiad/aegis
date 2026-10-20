// Pointing inside a sandboxed page (#479, spec 07). The server adds this
// script to every HTML preview, artifacts included (web.py, inject_point);
// the page runs in an opaque origin, so the client cannot read a selection or
// an element inside it, and this script reports them to the host instead.
//
// It posts {jsonrpc, method: "aegis/point", params: {kind, x, y, rect,
// locator: {heading, id, css}, text}} to the parent on a gesture by the
// person: "menu" on a right-click (not with Shift) or a touch held 500 ms
// without moving 8 px, "select" when a selection settles, "clear" when it
// goes or the page scrolls. A field, a link, an image or a press with no
// text under it keeps the browser's own menu. The host draws the menu or the bar in its own document, and acts only
// on an item the person picks there: a message proposes, nothing more. While
// the host says its menu is open (aegis/point-open, aegis/point-closed), the
// element is outlined.
//
// A plain script with no globals: a page's own code never sees it.
(() => {
  if (window.parent === window) return;
  const LONG_PRESS = 500;
  const SLOP = 8;
  const TEXT_MAX = 300;
  const NATIVE = "input, textarea, select, [contenteditable], a[href], img, video, audio, canvas, svg";
  const post = (params) => window.parent.postMessage({ jsonrpc: "2.0", method: "aegis/point", params }, "*");

  const elOf = (n) => (n && n.nodeType === 1 ? n : n?.parentElement) || document.body;

  // The text of the nearest heading at or before `el` in document order.
  function headingOf(el) {
    let found = null;
    for (const h of document.querySelectorAll("h1, h2, h3, h4, h5, h6")) {
      if (h === el || h.contains(el) || h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING) found = h;
      else break;
    }
    return found ? found.textContent.trim().replace(/\s+/g, " ") : null;
  }

  // The element's id, or a CSS path of at most four steps, stopping at an
  // ancestor with an id.
  function cssOf(el) {
    if (el.id) return `#${CSS.escape(el.id)}`;
    const steps = [];
    for (let n = el; n && n !== document.body && n !== document.documentElement && steps.length < 4; n = n.parentElement) {
      if (n.id) {
        steps.unshift(`#${CSS.escape(n.id)}`);
        break;
      }
      const tag = n.localName;
      const same = n.parentElement ? [...n.parentElement.children].filter((c) => c.localName === tag) : [];
      steps.unshift(same.length > 1 ? `${tag}:nth-of-type(${same.indexOf(n) + 1})` : tag);
    }
    return steps.join(" > ") || "body";
  }

  const cut = (t) => {
    const s = t.trim().replace(/[ \t]+\n/g, "\n");
    return s.length > TEXT_MAX ? `${s.slice(0, TEXT_MAX)}…` : s;
  };

  function locate(el, text) {
    return { locator: { heading: headingOf(el), id: el.id || null, css: cssOf(el) }, text: cut(text) };
  }

  const selected = () => {
    const sel = getSelection();
    return sel.rangeCount && !sel.isCollapsed && sel.toString().trim() ? sel.getRangeAt(0) : null;
  };

  let pointed = null; // the element the last menu proposed or open is about
  const open = new Map(); // the host's menu number -> {el, outline}: its own, to put back

  // What a press at x, y on `target` is about: the selection under it, else
  // the element and its text. Null leaves the browser its own menu: a field
  // (paste), a link, an image or a canvas, the page's empty space, no text.
  function aboutAt(target, x, y) {
    const r = selected();
    if (r && [...r.getClientRects()].some((b) => x >= b.left && x <= b.right && y >= b.top && y <= b.bottom)) {
      return { el: elOf(r.commonAncestorContainer), text: r.toString() };
    }
    const el = elOf(target);
    if (el === document.body || el === document.documentElement || el.closest(NATIVE)) return null;
    const text = (el.innerText || el.textContent || "").trim();
    return text ? { el, text } : null;
  }

  function menuAt({ el, text }, x, y, touch) {
    pointed = el;
    post({ kind: "menu", x, y, touch, ...locate(el, text) });
  }

  document.addEventListener("contextmenu", (ev) => {
    if (ev.shiftKey) return;
    clearTimeout(timer); // a touch's contextmenu before the long press's timer: one menu, not two
    if (swallow) return ev.preventDefault(); // the long press already reported it
    const about = aboutAt(ev.target, ev.clientX, ev.clientY);
    if (!about) return;
    ev.preventDefault();
    menuAt(about, ev.clientX, ev.clientY, false);
  });

  let timer = null;
  let at = null;
  let swallow = false;
  document.addEventListener("pointerdown", (ev) => {
    swallow = false;
    // A press anywhere in the page is a press outside the host's menu: the
    // host never sees it, so it is told.
    if (pointed) post({ kind: "clear" });
    clearTimeout(timer);
    if (ev.pointerType === "mouse" || ev.target.closest?.(NATIVE)) return;
    at = [ev.clientX, ev.clientY];
    const target = ev.target;
    timer = setTimeout(() => {
      const about = aboutAt(target, at[0], at[1]);
      if (!about) return;
      swallow = true;
      window.focus(); // the press's frame, as a click's would be: the host checks it
      menuAt(about, at[0], at[1], true);
    }, LONG_PRESS);
  });
  document.addEventListener("pointermove", (ev) => {
    if (at && Math.hypot(ev.clientX - at[0], ev.clientY - at[1]) > SLOP) clearTimeout(timer);
  });
  const settle = () => {
    clearTimeout(timer);
    setTimeout(report); // a click inside a selection collapses it after pointerup
  };
  document.addEventListener("pointerup", settle);
  document.addEventListener("pointercancel", () => clearTimeout(timer));
  // The release's mousedown would move the focus into the frame, which
  // closes the host's menu through its focusout.
  document.addEventListener("mousedown", (ev) => swallow && ev.preventDefault(), true);
  document.addEventListener(
    "click",
    (ev) => {
      if (!swallow) return;
      swallow = false;
      ev.preventDefault();
      ev.stopPropagation();
    },
    true,
  );

  // A selection that settled, or one that went.
  let shown = "";
  function report() {
    const r = selected();
    const text = r ? r.toString() : "";
    if (text === shown) return;
    shown = text;
    if (!r) return post({ kind: "clear" });
    const b = r.getClientRects()[0] || r.getBoundingClientRect();
    post({ kind: "select", rect: { left: b.left, top: b.top, right: b.right, bottom: b.bottom }, ...locate(elOf(r.commonAncestorContainer), text) });
  }
  // A handle dragged on touch changes the selection with no pointerup here.
  let quiet = null;
  document.addEventListener("selectionchange", () => {
    clearTimeout(quiet);
    quiet = setTimeout(report, 300);
  });

  // Esc closes the host's menu: the focus stays in this frame while it is open.
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && (pointed || shown)) {
      shown = "";
      post({ kind: "clear" });
    }
  });

  // The host draws at fixed points: a scroll here moves the element away
  // from its menu or its bar, so they close.
  document.addEventListener(
    "scroll",
    () => {
      clearTimeout(quiet); // a report still due would bring the bar back where it was
      if (!pointed && !shown) return;
      shown = "";
      post({ kind: "clear" });
    },
    true,
  );

  window.addEventListener("message", (ev) => {
    if (ev.source !== window.parent || ev.data?.jsonrpc !== "2.0") return;
    // Each message names its menu: the host closes the old one after it
    // opened the new, so a closed message ends only its own.
    const n = ev.data.params?.n;
    if (ev.data.method === "aegis/point-open" && pointed) {
      const earlier = [...open.values()].find((o) => o.el === pointed);
      open.set(n, { el: pointed, outline: earlier ? earlier.outline : pointed.style.outline });
      pointed.style.outline = "2px solid #2f6fdb";
    } else if (ev.data.method === "aegis/point-closed" && open.has(n)) {
      const { el, outline } = open.get(n);
      open.delete(n);
      if (![...open.values()].some((o) => o.el === el)) el.style.outline = outline;
      if (el === pointed && !open.size) pointed = null;
    }
  });
})();
