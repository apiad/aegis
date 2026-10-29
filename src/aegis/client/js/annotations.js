// Visual annotations for images and PDFs (#780). The browser keeps normalized
// coordinates in the same comment tray as text comments. They survive a
// resize and a reload, and become JSON in the message the person sends.

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

const rounded = (n) => Number(n.toFixed(4));

// Add the drawing layer and its controls to a preview stage. A PDF's native
// viewer does not expose its current page, so the person names the visible
// page before drawing; image coordinates need no extra context.
export function annotate(stage, tray, { path, kind }) {
  if (!tray || !["image", "pdf"].includes(kind)) return null;
  const target = stage.querySelector("img, iframe");
  if (!target) return null;
  stage.classList.add("visual-annotations");
  const tools = el("div", "vatools");
  const toggle = el("button", "btn", "Annotate");
  toggle.type = "button";
  toggle.setAttribute("aria-pressed", "false");
  toggle.title = "Draw a point or region for the agent";
  tools.append(toggle);
  let page = null;
  if (kind === "pdf") {
    const label = el("label", "vapage", "page ");
    page = el("input");
    page.type = "number";
    page.min = "1";
    page.value = "1";
    page.setAttribute("aria-label", "PDF page to annotate");
    page.title = "Set the page visible in the PDF viewer before drawing";
    label.append(page);
    tools.append(label);
  }
  const layer = el("div", "valayer");
  layer.setAttribute("aria-label", `Annotations on ${path}`);
  stage.append(layer, tools);
  tray.layer(layer);

  const sync = () => {
    const outer = stage.getBoundingClientRect();
    const inner = target.getBoundingClientRect();
    layer.style.left = `${inner.left - outer.left + stage.scrollLeft}px`;
    layer.style.top = `${inner.top - outer.top + stage.scrollTop}px`;
    layer.style.width = `${inner.width}px`;
    layer.style.height = `${inner.height}px`;
    tray.pins();
  };
  target.addEventListener("load", sync);
  const resize = new ResizeObserver(sync);
  resize.observe(stage);
  resize.observe(target);
  requestAnimationFrame(sync);

  toggle.addEventListener("click", () => {
    const on = toggle.getAttribute("aria-pressed") !== "true";
    toggle.setAttribute("aria-pressed", String(on));
    layer.classList.toggle("active", on);
  });

  let start = null;
  let draft = null;
  const point = (ev) => {
    const b = layer.getBoundingClientRect();
    if (!b.width || !b.height) return null;
    return {
      x: Math.max(0, Math.min(1, (ev.clientX - b.left) / b.width)),
      y: Math.max(0, Math.min(1, (ev.clientY - b.top) / b.height)),
    };
  };
  layer.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || !layer.classList.contains("active")) return;
    ev.preventDefault();
    start = point(ev);
    if (!start) return;
    draft = el("span", "vadraft");
    layer.append(draft);
    layer.setPointerCapture(ev.pointerId);
  });
  layer.addEventListener("pointermove", (ev) => {
    if (!start || !draft) return;
    const now = point(ev);
    if (!now) return;
    const x = Math.min(start.x, now.x);
    const y = Math.min(start.y, now.y);
    Object.assign(draft.style, {
      left: `${x * 100}%`,
      top: `${y * 100}%`,
      width: `${Math.abs(now.x - start.x) * 100}%`,
      height: `${Math.abs(now.y - start.y) * 100}%`,
    });
  });
  layer.addEventListener("pointerup", (ev) => {
    if (!start) return;
    const end = point(ev);
    if (!end) return;
    draft?.remove();
    draft = null;
    const dx = Math.abs(end.x - start.x);
    const dy = Math.abs(end.y - start.y);
    const type = dx < 0.01 && dy < 0.01 ? "point" : "region";
    const at = {
      type,
      x: rounded(type === "point" ? end.x : Math.min(start.x, end.x)),
      y: rounded(type === "point" ? end.y : Math.min(start.y, end.y)),
      width: rounded(type === "point" ? 0 : dx),
      height: rounded(type === "point" ? 0 : dy),
    };
    const annotation = { file: path, type };
    if (page) annotation.page = Math.max(1, Number(page.value) || 1);
    annotation.region = { x: at.x, y: at.y, width: at.width, height: at.height };
    const where = page ? `page ${annotation.page}` : "image";
    tray.add({ locator: `${where} ${type}`, quote: "", at, annotation });
    start = null;
  });
  layer.addEventListener("pointercancel", () => {
    start = null;
    draft?.remove();
    draft = null;
  });
  return { sync, destroy: () => resize.disconnect() };
}
