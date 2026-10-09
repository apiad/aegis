// The landing page's one aegis window, kept in step with the text beside it.
//
// Scrolling makes the step nearest the middle of the viewport current, and the
// window shows that step: `data-show` lists the steps an element appears in,
// `data-on` the steps a tab is the current one in. The other way round, any
// element with `data-feature` lights its step on hover and, on click, scrolls
// the text to it; hovering a step lights its pieces in the window. A few
// controls also act like the client: the fold button, a folded line, the
// panel's rows and button, its edge, the layout picker and the theme.

const FLEET = new Set(["fleet", "quota", "links"]);
const OPEN_CARD = { subs: "usage-go", panel: "plan" }; // the card a step opens by itself
const THEMES = ["ink", "logbook", "syalia"];
const LAYOUTS = {
  sidebar: ["Sidebar sections", "Sidebar sections it is."],
  tabs: ["Tabs", "Tabs it is, one per section."],
  single: ["One long page", "One long page it is, with the sections as anchors."],
};

const stage = document.getElementById("stage");
const steps = [...document.querySelectorAll(".step")];
const a2 = stage?.querySelector(".a2");
const entries = stage?.querySelector("#entries");
const foldBtn = stage?.querySelector(".nav .fold");
const float = stage?.querySelector("#float");
const has = (el, attr, step) => el.dataset[attr].split(" ").includes(step);
let current = null;
let level = 0;
let lockUntil = 0; // while a click scrolls the text, the steps it passes do not count

// -- folding: the client's three levels, computed from the rows on show ---------
// Level 1 folds the work (tool calls and notes) between two messages; level 2
// folds everything but the messages. Each run becomes one line on its first row.
const kind = (row) =>
  row.matches(".user, .prose") ? "msg" : row.matches(".inbox, .artifact, .file") ? "keep" : "work";

function summary(run) {
  const tools = run.filter((r) => r.matches(".tool"));
  const names = new Map();
  for (const r of tools) {
    const n = r.querySelector(".tn")?.textContent || "tool";
    names.set(n, (names.get(n) || 0) + 1);
  }
  const parts = [`${tools.length} tool call${tools.length === 1 ? "" : "s"}`];
  if (names.size) parts.push([...names].map(([n, c]) => (c > 1 ? `${n} ×${c}` : n)).join(", "));
  const failed = run.filter((r) => r.matches(".err")).length;
  if (failed) parts.push(`${failed} failed`);
  const kept = run.filter((r) => kind(r) === "keep").length;
  if (kept) parts.push(`${kept} more`);
  return parts.join(" · ");
}

function setFold(n) {
  level = n;
  foldBtn.dataset.level = String(n);
  entries.classList.toggle("prose-view", n > 0);
  for (const r of entries.querySelectorAll(".row")) {
    delete r.dataset.fold;
    r.classList.remove("run-open");
    r.querySelector(":scope > .runline")?.remove();
  }
  if (!n) return;
  const rows = [...entries.querySelectorAll(":scope > .row:not([hidden])")];
  const folds = (r) => kind(r) === "work" || (n === 2 && kind(r) === "keep");
  for (let i = 0; i < rows.length; ) {
    if (!folds(rows[i])) {
      i++;
      continue;
    }
    const run = [];
    while (i < rows.length && folds(rows[i])) run.push(rows[i++]);
    const head = run[0];
    head.dataset.fold = "head";
    for (const r of run.slice(1)) r.dataset.fold = "in";
    const line = document.createElement("button");
    line.type = "button";
    line.className = "runline";
    line.dataset.feature = "fold";
    line.dataset.act = "run";
    line.innerHTML = `<span class="t">${head.querySelector(".t")?.textContent || ""}</span><span class="g">▸</span><span class="rs"></span>`;
    line.querySelector(".rs").textContent = summary(run);
    head.prepend(line);
  }
}

// -- the cards the panel's rows open --------------------------------------------
function openCard(row) {
  const card = row?.querySelector(":scope > .pcard");
  if (!card || row.hidden) return closeCard();
  float.innerHTML = card.innerHTML;
  float.hidden = false;
  const box = stage.querySelector(".v-session").getBoundingClientRect();
  const r = row.getBoundingClientRect();
  const zoom = box.width / stage.querySelector(".v-session").offsetWidth || 1;
  float.style.top = `${Math.max(8, (r.top - box.top) / zoom - 8)}px`;
  for (const p of stage.querySelectorAll(".prow.open-lit")) p.classList.remove("open-lit");
  row.classList.add("open-lit");
}

function closeCard() {
  float.hidden = true;
  for (const p of stage.querySelectorAll(".prow.open-lit")) p.classList.remove("open-lit");
}

// -- a step: what the window shows -----------------------------------------------
function show(step) {
  if (current === step) return;
  current = step;
  stage.dataset.step = step;
  a2.dataset.view = FLEET.has(step) ? "fleet" : "session";
  if (step !== "panel") delete a2.dataset.side;
  for (const el of stage.querySelectorAll("[data-show]")) el.hidden = !has(el, "show", step);
  for (const el of stage.querySelectorAll("[data-on]")) el.classList.toggle("on", has(el, "on", step));
  setFold(step === "fold" || step === "answer" ? 1 : 0);
  closeCard();
  const card = OPEN_CARD[step];
  if (card) openCard(stage.querySelector(`[data-card="${card}"]`));
  for (const s of steps) s.classList.toggle("on", s.dataset.step === step);
}

// Scroll the text to a step and show it at once; the steps the scroll passes
// through do not take the window over on the way.
function go(step) {
  const art = steps.find((s) => s.dataset.step === step);
  if (!art) return;
  show(step);
  lockUntil = performance.now() + 1200;
  const y = art.getBoundingClientRect().top + scrollY - innerHeight / 2 + 80;
  scrollTo({ top: y, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}

// -- light: a hovered feature and its step, either side --------------------------
// From the window, `only` is the element under the pointer: it alone is
// outlined. From the text, every visible piece of the step is.
function light(step, on, only = null) {
  for (const s of steps) s.classList.toggle("hl", on && s.dataset.step === step);
  for (const el of stage.querySelectorAll(".lit")) el.classList.remove("lit");
  if (!on) return;
  if (only) return only.classList.add("lit");
  for (const el of stage.querySelectorAll(`[data-feature="${step}"]`)) if (el.offsetParent) el.classList.add("lit");
}

function act(el, ev) {
  switch (el.dataset.act) {
    case "fold":
      if (current !== "fold") return go("fold");
      setFold((level + 1) % 3);
      return;
    case "run": {
      ev.stopPropagation();
      const head = el.parentElement;
      const open = !head.classList.contains("run-open");
      head.classList.toggle("run-open", open);
      let r = head.nextElementSibling;
      while (r && r.dataset.fold === "in") {
        r.classList.toggle("run-open", open);
        r = r.nextElementSibling;
      }
      if (current !== "fold") go("fold");
      return;
    }
    case "pick": {
      const [label, said] = LAYOUTS[el.dataset.v];
      stage.querySelector("#ans-json").textContent = `{"layout": "${el.dataset.v}"}`;
      stage.querySelector("#ans-prose").innerHTML = `${said} Building it in <code>web/settings/Page.tsx</code> now.`;
      for (const o of stage.querySelectorAll(".opt")) o.classList.toggle("on", o === el);
      el.title = label;
      return go("answer");
    }
    case "reopen":
      return go("artifact");
    case "collapse":
      if (current !== "panel") go("panel");
      if (a2.dataset.side === "closed") delete a2.dataset.side;
      else a2.dataset.side = "closed";
      closeCard();
      return;
    case "theme": {
      const next = THEMES[(THEMES.indexOf(stage.dataset.theme) + 1) % THEMES.length];
      stage.dataset.theme = next;
      el.textContent = next[0].toUpperCase() + next.slice(1);
      if (current !== "themes") go("themes");
      return;
    }
    case "reply":
      el.classList.add("sent");
      setTimeout(() => el.classList.remove("sent"), 600);
      if (current !== "replies") go("replies");
      return;
  }
}

if (stage && steps.length) {
  // Scroll -> window.
  const io = new IntersectionObserver(
    (seen) => {
      if (performance.now() < lockUntil) return;
      for (const e of seen) if (e.isIntersecting) show(e.target.dataset.step);
    },
    { rootMargin: "-45% 0px -45% 0px", threshold: 0 },
  );
  for (const s of steps) io.observe(s);
  addEventListener("scrollend", () => (lockUntil = 0));
  show(steps[0].dataset.step);

  // Window -> text: hover lights, click goes.
  let lit = null;
  stage.addEventListener("mouseover", (ev) => {
    const f = ev.target.closest("[data-feature]");
    if (f !== lit) light(f?.dataset.feature, !!(lit = f), f);
    const row = ev.target.closest(".prow");
    if (row && row.querySelector(":scope > .pcard")) openCard(row);
    else if (!ev.target.closest("#float") && !OPEN_CARD[current]) closeCard();
  });
  stage.addEventListener("mouseleave", () => {
    light((lit = null), false);
    if (!OPEN_CARD[current]) closeCard();
  });
  stage.addEventListener("click", (ev) => {
    const a = ev.target.closest("[data-act]");
    if (a) return act(a, ev);
    const f = ev.target.closest("[data-feature]");
    if (f) go(f.dataset.feature);
  });

  // Text -> window: hovering a step lights its pieces.
  for (const s of steps) {
    s.addEventListener("mouseenter", () => light(s.dataset.step, true));
    s.addEventListener("mouseleave", () => light(null, false));
  }

  // The panel's edge: drag to resize, double-click for the default width.
  const grip = stage.querySelector(".grip");
  grip.addEventListener("pointerdown", (ev) => {
    ev.preventDefault();
    grip.setPointerCapture(ev.pointerId);
    a2.dataset.resizing = "";
    closeCard();
    const box = stage.querySelector(".v-session").getBoundingClientRect();
    const zoom = box.width / stage.querySelector(".v-session").offsetWidth || 1;
    const move = (e) => a2.style.setProperty("--side-w", `${Math.max(200, Math.min(420, (box.right - e.clientX) / zoom))}px`);
    const up = () => {
      delete a2.dataset.resizing;
      grip.removeEventListener("pointermove", move);
      grip.removeEventListener("pointerup", up);
    };
    grip.addEventListener("pointermove", move);
    grip.addEventListener("pointerup", up);
    if (current !== "panel") go("panel");
  });
  grip.addEventListener("dblclick", () => a2.style.removeProperty("--side-w"));
}

// The latest release, from PyPI; the markup already says the version this page was written for.
fetch("https://pypi.org/pypi/aegis-harness/json")
  .then((r) => (r.ok ? r.json() : null))
  .then((j) => {
    const v = j?.info?.version;
    const el = document.getElementById("ver");
    if (v && el) el.textContent = `Version ${v}`;
  })
  .catch(() => {});
