// Pointing inside a sandboxed page (#479, spec 07). The server adds this
// script to every HTML preview, artifacts included (web.py, inject_point);
// the page runs in an opaque origin, so the client cannot read a selection or
// an element inside it, and this script reports them to the host instead.
//
// It posts {jsonrpc, method: "aegis/point", params: {kind, x, y, rect,
// locator: {heading, id, css}, text}} to the parent on a gesture by the
// person: "menu" on a right-click (not with Shift) or a touch held 500 ms
// without moving 8 px, "select" when a selection settles, "clear" when it
// goes. The host draws the menu or the bar in its own document, and acts only
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

  let pointed = null; // the element the last menu was about
  let outline = null; // its own outline, to put back

  function menuAt(target, x, y, touch) {
    const r = selected();
    const inside = r && [...r.getClientRects()].some((b) => x >= b.left && x <= b.right && y >= b.top && y <= b.bottom);
    const el = inside ? elOf(r.commonAncestorContainer) : elOf(target);
    pointed = el;
    post({ kind: "menu", x, y, touch, ...locate(el, inside ? r.toString() : el.innerText || el.textContent || "") });
  }

  document.addEventListener("contextmenu", (ev) => {
    if (ev.shiftKey) return;
    ev.preventDefault();
    if (swallow) return; // the long press already reported it
    menuAt(ev.target, ev.clientX, ev.clientY, false);
  });

  let timer = null;
  let at = null;
  let swallow = false;
  document.addEventListener("pointerdown", (ev) => {
    swallow = false;
    clearTimeout(timer);
    if (ev.pointerType === "mouse") return;
    at = [ev.clientX, ev.clientY];
    const target = ev.target;
    timer = setTimeout(() => {
      swallow = true;
      menuAt(target, at[0], at[1], true);
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

  window.addEventListener("message", (ev) => {
    if (ev.source !== window.parent || ev.data?.jsonrpc !== "2.0") return;
    if (ev.data.method === "aegis/point-open" && pointed) {
      outline = pointed.style.outline;
      pointed.style.outline = "2px solid #2f6fdb";
    } else if (ev.data.method === "aegis/point-closed" && pointed) {
      pointed.style.outline = outline ?? "";
      pointed = null;
    }
  });
})();
