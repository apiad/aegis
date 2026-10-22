// The demo's camera and overlays, injected into the real client by
// director.py. The camera moves the client's root (#a2) in 3D; the overlays
// live in #demo-stage, a sibling the camera does not move, and a callout
// re-measures its target every frame, so its arrow follows the element
// through any tilt or zoom.
(() => {
const boot = () => {
  if (window.demo) return;
  const css = `
    @font-face { font-family: DemoSans; src: url(/static/fonts/inter-500.woff2); font-weight: 500; }
    @font-face { font-family: DemoSans; src: url(/static/fonts/inter-600.woff2); font-weight: 600 700; }
    @font-face { font-family: DemoSerif; src: url(/static/fonts/serif-600.woff2); font-weight: 600; }
    @font-face { font-family: DemoMono; src: url(/static/fonts/jbmono-500.woff2); }
    html, body { background: #0b0d10 !important; }
    body { perspective: 2400px; overflow: hidden; }
    #a2 { transform-origin: 50% 50%; will-change: transform;
          box-shadow: 0 40px 120px rgba(0,0,0,.55); }
    #demo-stage { position: fixed; inset: 0; pointer-events: none;
          z-index: 2147483647; font: 500 19px/1.35 DemoSans, system-ui, sans-serif;
          color: #f4f1ea; }
    #demo-stage * { box-sizing: border-box; }
    .d-fade { transition: opacity .45s ease, transform .45s ease; }
    .d-title { position: absolute; inset: 0; display: grid; place-items: center;
          background: rgba(11,13,16,.88); opacity: 0; }
    .d-title .in { text-align: center; transform: translateY(14px); transition: transform .6s ease; }
    .d-title.on .in { transform: none; }
    .d-title .k { font-size: 17px; letter-spacing: .2em; text-transform: uppercase;
          color: #e0a458; margin-bottom: 18px; }
    .d-title .h { font: 600 56px/1.1 DemoSerif, Georgia, serif; }
    .d-title .s { margin-top: 12px; font-size: 22px; color: #b9b4aa; }
    .d-cap { position: absolute; left: 50%; bottom: 118px; transform: translateX(-50%);
          max-width: 1000px; padding: 10px 20px; border-radius: 12px; text-align: center;
          background: rgba(11,13,16,.82); font-size: 23px; opacity: 0; }
    .d-lower { position: absolute; left: 54px; bottom: 100px; padding: 12px 20px;
          border-left: 6px solid #e0a458; background: rgba(11,13,16,.88);
          border-radius: 0 12px 12px 0; opacity: 0; transform: translateX(-30px); }
    .d-lower .l1 { font-size: 15px; color: #e0a458; letter-spacing: .12em; text-transform: uppercase; }
    .d-lower .l2 { font: 500 23px/1.3 DemoMono, ui-monospace, monospace; margin-top: 6px; }
    .d-box { position: absolute; border: 3px solid #e0a458; border-radius: 10px;
          box-shadow: 0 0 0 9999px rgba(11,13,16,.0); opacity: 0; transition: opacity .35s ease; }
    .d-label { position: absolute; padding: 10px 18px; border-radius: 10px; white-space: nowrap;
          background: #e0a458; color: #14110c; font-weight: 650; font-size: 19px; opacity: 0;
          transition: opacity .35s ease; }
    .d-arrow { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; opacity: 0;
          transition: opacity .35s ease; }
    .d-ring { position: absolute; width: 64px; height: 64px; margin: -32px 0 0 -32px; border-radius: 50%;
          border: 4px solid #e0a458; animation: d-ring .7s ease-out forwards; }
    @keyframes d-ring { from { transform: scale(.3); opacity: 1 } to { transform: scale(1.6); opacity: 0 } }
    .d-cursor { position: absolute; width: 26px; height: 26px; margin: -4px 0 0 -4px;
          transition: left .5s cubic-bezier(.3,.7,.3,1), top .5s cubic-bezier(.3,.7,.3,1);
          filter: drop-shadow(0 2px 4px rgba(0,0,0,.5)); }
  `;
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const stage = document.createElement("div");
  stage.id = "demo-stage";
  document.body.appendChild(stage);

  const el = (cls, html = "") => {
    const d = document.createElement("div");
    d.className = cls;
    d.innerHTML = html;
    stage.appendChild(d);
    return d;
  };
  const show = (d, on) => { d.style.opacity = on ? 1 : 0; };

  // -- camera ---------------------------------------------------------------
  const cam = { rx: 0, ry: 0, rz: 0, s: 1, x: 0, y: 0 };
  let tween = null;
  const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const root = () => document.getElementById("a2");
  const apply = () => {
    const r = root();
    if (r) r.style.transform =
      `translate(${cam.x}px, ${cam.y}px) scale(${cam.s}) ` +
      `rotateX(${cam.rx}deg) rotateY(${cam.ry}deg) rotateZ(${cam.rz}deg)`;
  };

  // -- callouts, measured every frame -----------------------------------------
  const callouts = new Map();
  function place(c) {
    const t = document.querySelector(c.sel);
    if (!t) { show(c.box, 0); show(c.label, 0); show(c.arrow, 0); return; }
    const r = t.getBoundingClientRect();
    const p = c.pad;
    Object.assign(c.box.style, {
      left: r.left - p + "px", top: r.top - p + "px",
      width: r.width + 2 * p + "px", height: r.height + 2 * p + "px",
    });
    // The label sits on the side with the most room, the arrow joins it to the box.
    const lw = c.label.offsetWidth, lh = c.label.offsetHeight;
    const W = innerWidth, H = innerHeight, gap = 60;
    const room = { left: r.left, right: W - r.right, top: r.top, bottom: H - r.bottom };
    const side = c.side || Object.entries(room).sort((a, b) => b[1] - a[1])[0][0];
    let lx, ly, ax, ay;
    const cx = (r.left + r.right) / 2, cy = (r.top + r.bottom) / 2;
    if (side === "left") { lx = r.left - p - gap - lw; ly = cy - lh / 2; ax = r.left - p; ay = cy; }
    else if (side === "right") { lx = r.right + p + gap; ly = cy - lh / 2; ax = r.right + p; ay = cy; }
    else if (side === "top") { lx = cx - lw / 2; ly = r.top - p - gap - lh; ax = cx; ay = r.top - p; }
    else { lx = cx - lw / 2; ly = r.bottom + p + gap; ax = cx; ay = r.bottom + p; }
    lx = Math.max(24, Math.min(W - lw - 24, lx));
    ly = Math.max(24, Math.min(H - lh - 24, ly));
    Object.assign(c.label.style, { left: lx + "px", top: ly + "px" });
    const fx = Math.max(lx, Math.min(lx + lw, ax)), fy = Math.max(ly, Math.min(ly + lh, ay));
    c.arrow.innerHTML =
      `<defs><marker id="dh${c.id}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">` +
      `<path d="M0,0 L10,5 L0,10 z" fill="#e0a458"/></marker></defs>` +
      `<line x1="${fx}" y1="${fy}" x2="${ax}" y2="${ay}" stroke="#e0a458" stroke-width="4" marker-end="url(#dh${c.id})"/>`;
    show(c.box, c.on); show(c.label, c.on); show(c.arrow, c.on);
  }

  function frame(now) {
    if (tween) {
      const k = Math.min(1, (now - tween.t0) / tween.ms);
      const e = ease(k);
      for (const key in tween.to) cam[key] = tween.from[key] + (tween.to[key] - tween.from[key]) * e;
      if (k >= 1) tween = null;
    }
    apply();
    for (const c of callouts.values()) place(c);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  let n = 0, capN = 0;
  const cap = el("d-cap d-fade");
  const lower = el("d-lower d-fade", `<div class="l1"></div><div class="l2"></div>`);
  const title = el("d-title d-fade", `<div class="in"><div class="k"></div><div class="h"></div><div class="s"></div></div>`);
  const cursor = el("d-cursor",
    `<svg viewBox="0 0 24 24" width="26" height="26"><path d="M3 2l7 19 2.6-7.4L20 11z" fill="#f4f1ea" stroke="#14110c" stroke-width="1.5"/></svg>`);
  cursor.style.left = "960px"; cursor.style.top = "800px";

  window.demo = {
    set(c) { tween = null; Object.assign(cam, c); apply(); },
    // An element's box with the camera at rest, for framing a move to it.
    flat(sel) {
      const r = root(), t = r.style.transform;
      r.style.transform = "none";
      const box = document.querySelector(sel).getBoundingClientRect();
      r.style.transform = t;
      return box;
    },
    camera(to, ms = 1200) {
      tween = { t0: performance.now(), ms: Math.max(1, ms), from: { ...cam }, to };
    },
    caption(text, ms) {
      if (text) cap.textContent = text;
      show(cap, !!text);
      const mine = ++capN;
      if (text && ms) setTimeout(() => { if (capN === mine) show(cap, 0); }, ms);
    },
    lower(l1, l2) {
      if (l1 !== undefined && l1 !== null) {
        lower.querySelector(".l1").textContent = l1;
        lower.querySelector(".l2").textContent = l2 || "";
      }
      show(lower, !!l1);
      lower.style.transform = l1 ? "none" : "translateX(-30px)";
    },
    title(kicker, head, sub) {
      if (head) {
        title.querySelector(".k").textContent = kicker || "";
        title.querySelector(".h").textContent = head;
        title.querySelector(".s").textContent = sub || "";
      }
      show(title, !!head);
      title.classList.toggle("on", !!head);
    },
    callout(sel, label, opts = {}) {
      const id = ++n;
      const c = {
        id, sel, pad: opts.pad ?? 10, side: opts.side, on: 1,
        box: el("d-box"), label: el("d-label", label),
        arrow: (() => {
          const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
          s.setAttribute("class", "d-arrow");
          stage.appendChild(s);
          return s;
        })(),
      };
      callouts.set(id, c);
      return id;
    },
    uncallout(id) {
      const c = callouts.get(id);
      if (!c) return;
      c.on = 0;
      setTimeout(() => { callouts.delete(id); c.box.remove(); c.label.remove(); c.arrow.remove(); }, 400);
    },
    point(x, y) { cursor.style.left = x + "px"; cursor.style.top = y + "px"; },
    ring(x, y) {
      const r = el("d-ring");
      r.style.left = x + "px"; r.style.top = y + "px";
      setTimeout(() => r.remove(), 800);
    },
  };
};
if (document.body) boot(); else document.addEventListener("DOMContentLoaded", boot);
})();
