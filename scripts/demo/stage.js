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
    .d-logo { position: absolute; inset: 0; display: grid; place-items: center;
          background: #11100e; opacity: 0; transition: opacity .8s ease; }
    .d-logo .in { display: grid; justify-items: center; transform: translateY(-70px); }
    .d-logo img { width: 230px; height: 230px; transform: scale(.82) rotate(-40deg);
          transition: transform 1.6s cubic-bezier(.2,.8,.2,1); }
    .d-logo .w { font: 500 76px/1 DemoMono, ui-monospace, monospace; color: #f4f1ea;
          letter-spacing: .02em; margin-top: 4px; opacity: 0; transform: translateY(10px);
          transition: opacity .8s ease .5s, transform .8s ease .5s; }
    .d-logo .s { margin-top: 22px; font-size: 22px; color: #b9b4aa; min-height: 30px;
          opacity: 0; transition: opacity .8s ease .9s; }
    .d-logo .s code { font: 500 21px DemoMono, ui-monospace, monospace; color: #e0a458; }
    .d-logo.on img { transform: none; }
    .d-logo.on .w { opacity: 1; transform: none; }
    .d-logo.on .s { opacity: 1; }
    .d-cap { position: absolute; left: 50%; bottom: 118px; transform: translateX(-50%);
          max-width: 1000px; padding: 10px 20px; border-radius: 12px; text-align: center;
          background: rgba(11,13,16,.82); font-size: 23px; opacity: 0; z-index: 2; }
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
    .d-spot { position: absolute; border-radius: 12px; opacity: 0;
          box-shadow: 0 0 0 200vmax rgba(5,6,8,.62); transition: opacity .6s ease; }
    .d-lifted { position: relative; z-index: 50; transform: scale(1.045) translateY(-4px);
          box-shadow: 0 24px 60px rgba(0,0,0,.6), 0 0 0 1px rgba(224,164,88,.5);
          border-radius: 10px; }
    .d-over { position: absolute; inset: 0; opacity: 0; transition: opacity .7s ease; }
    .d-phone { position: absolute; right: 70px; top: 50%; width: 300px; height: 620px;
          margin-top: -310px; border-radius: 42px; background: #050608; padding: 12px;
          box-shadow: 0 40px 100px rgba(0,0,0,.7), 0 0 0 2px #2a2b2f;
          transform: translateX(520px) rotateY(-18deg); transition: transform 1.1s cubic-bezier(.2,.8,.2,1); }
    .d-phone.on { transform: none; }
    .d-phone iframe { width: 390px; height: 832px; border: 0; border-radius: 32px;
          transform: scale(.7179); transform-origin: 0 0; background: #111; }
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
  // The world runs SLOW times slower than the video (director.py), so every
  // duration here is in video time and the clock is the page's divided by it.
  const SLOW = window.__demoSlow || 1;
  const clock = () => performance.now() / SLOW;
  const later = (fn, ms) => setTimeout(fn, ms * SLOW);
  const cam = { rx: 0, ry: 0, rz: 0, s: 1, x: 0, y: 0 };
  let tween = null, sway = 0, swayTo = 0;
  const ease = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  const root = () => document.getElementById("a2");
  // The sway: slow, out-of-step sines on tilt and scale, so a held shot still
  // breathes. Its amount eases toward the asked one instead of jumping.
  const apply = () => {
    const r = root();
    if (!r) return;
    sway += (swayTo - sway) * 0.03;
    const t = clock() / 1000;
    const rx = cam.rx + sway * 1.4 * Math.sin(t * 0.45);
    const ry = cam.ry + sway * 2.2 * Math.sin(t * 0.31 + 1.3);
    const s = cam.s * (1 + sway * 0.012 * Math.sin(t * 0.23 + 0.4));
    r.style.transform =
      `translate(${cam.x}px, ${cam.y}px) scale(${s}) ` +
      `rotateX(${rx}deg) rotateY(${ry}deg) rotateZ(${cam.rz}deg)`;
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

  // -- spotlight and lift ------------------------------------------------------
  let spotSel = null, lifted = null;
  function placeSpot() {
    const t = spotSel && document.querySelector(spotSel);
    if (!t) { spot.style.opacity = 0; return; }
    const r = t.getBoundingClientRect(), p = 8;
    Object.assign(spot.style, {
      left: r.left - p + "px", top: r.top - p + "px",
      width: r.width + 2 * p + "px", height: r.height + 2 * p + "px", opacity: 1,
    });
  }

  function frame() {
    const now = clock();
    if (tween) {
      const k = Math.min(1, (now - tween.t0) / tween.ms);
      const e = ease(k);
      for (const key in tween.to) cam[key] = tween.from[key] + (tween.to[key] - tween.from[key]) * e;
      if (k >= 1) tween = null;
    }
    apply();
    placeSpot();
    for (const c of callouts.values()) place(c);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  let n = 0, capN = 0;
  // The camera moves the UI under the resting mouse, which would hover rows
  // and open their popovers: this catches the mouse except while clicking.
  const shield = document.createElement("div");
  shield.style.cssText = "position:fixed;inset:0;z-index:2147483646;background:transparent";
  document.body.appendChild(shield);
  const spot = el("d-spot");
  const cap = el("d-cap d-fade");
  const lower = el("d-lower d-fade", `<div class="l1"></div><div class="l2"></div>`);
  const title = el("d-title d-fade", `<div class="in"><div class="k"></div><div class="h"></div><div class="s"></div></div>`);
  // The logo and the wordmark, as the site's header draws them: the mark in
  // its own colours, the name in the mono face.
  const logo = el("d-logo", `<div class="in"><img src="/static/icons/aegis-512.png" alt=""><div class="w">aegis</div><div class="s"></div></div>`);
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
      tween = { t0: clock(), ms: Math.max(1, ms), from: { ...cam }, to };
    },
    moving() { return !!tween; },
    drift(amount) { swayTo = amount; },
    shield(on) { shield.style.display = on ? "block" : "none"; },
    overlay(html) {
      const old = stage.querySelector(".d-over.live");
      if (old) { old.classList.remove("live"); show(old, 0); later(() => old.remove(), 800); }
      if (!html) return;
      const o = el("d-over live", html);
      requestAnimationFrame(() => show(o, 1));
    },
    phone(url) {
      let p = stage.querySelector(".d-phone");
      if (!url) { if (p) p.classList.remove("on"); return; }
      if (!p) {
        p = el("d-phone", `<iframe src="${url}"></iframe>`);
        p.style.pointerEvents = "none";
      }
      later(() => p.classList.add("on"), 50);
    },
    spotlight(sel) { spotSel = sel; },
    lift(sel) {
      if (lifted) { lifted.classList.remove("d-lifted"); lifted = null; }
      const t = sel && document.querySelector(sel);
      if (t) {
        lifted = t;
        // Kept after the lift ends, so the element also eases back.
        t.style.transition = "transform .5s ease, box-shadow .5s ease";
        t.classList.add("d-lifted");
      }
    },
    caption(text, ms) {
      if (text) cap.textContent = text;
      show(cap, !!text);
      const mine = ++capN;
      if (text && ms) later(() => { if (capN === mine) show(cap, 0); }, ms);
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
    logo(sub) {
      const on = sub !== null && sub !== undefined;
      if (on) logo.querySelector(".s").innerHTML = sub;
      show(logo, on);
      logo.classList.toggle("on", on);
      cursor.style.visibility = on ? "hidden" : "";
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
      later(() => { callouts.delete(id); c.box.remove(); c.label.remove(); c.arrow.remove(); }, 400);
    },
    point(x, y) { cursor.style.left = x + "px"; cursor.style.top = y + "px"; },
    ring(x, y) {
      const r = el("d-ring");
      r.style.left = x + "px"; r.style.top = y + "px";
      later(() => r.remove(), 800);
    },
  };
};
if (document.body) boot(); else document.addEventListener("DOMContentLoaded", boot);
})();
