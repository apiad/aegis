// The stage follows the step nearest the middle of the viewport, and the
// version line follows PyPI. Nothing else on the page moves on its own.
//
// A step picks the stage's view (Fleet or a session) and, through two
// attributes on the stage's markup, what it shows: `data-show` lists the steps
// an element appears in, `data-on` the steps a tab is the current one in.

const FLEET = new Set(["fleet", "quota", "links"]);

(() => {
  const stage = document.getElementById("stage");
  const steps = [...document.querySelectorAll(".step")];
  if (!stage || !steps.length) return;
  const a2 = stage.querySelector(".a2");
  const entries = stage.querySelector("#entries");
  const fold = stage.querySelector(".nav .fold");
  const has = (el, attr, step) => el.dataset[attr].split(" ").includes(step);

  const show = (step) => {
    if (stage.dataset.step === step) return;
    stage.dataset.step = step;
    a2.dataset.view = FLEET.has(step) ? "fleet" : "session";
    for (const el of stage.querySelectorAll("[data-show]")) el.hidden = !has(el, "show", step);
    for (const el of stage.querySelectorAll("[data-on]")) el.classList.toggle("on", has(el, "on", step));
    // The fold step and the answered artifact show the transcript folded.
    const folded = step === "fold" || step === "answer";
    entries.classList.toggle("prose-view", folded);
    fold.dataset.level = folded ? "1" : "0";
    for (const s of steps) s.classList.toggle("on", s.dataset.step === step);
  };

  // A step is current while it crosses a band around the middle of the viewport.
  const io = new IntersectionObserver(
    (seen) => {
      for (const e of seen) if (e.isIntersecting) show(e.target.dataset.step);
    },
    { rootMargin: "-45% 0px -45% 0px", threshold: 0 },
  );
  for (const s of steps) io.observe(s);
  show(steps[0].dataset.step);
})();

// The latest release, from PyPI; the markup already says the version this page was written for.
fetch("https://pypi.org/pypi/aegis-harness/json")
  .then((r) => (r.ok ? r.json() : null))
  .then((j) => {
    const v = j?.info?.version;
    const el = document.getElementById("ver");
    if (v && el) el.textContent = `Version ${v}`;
  })
  .catch(() => {});
