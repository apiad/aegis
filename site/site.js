// The stage follows the step nearest the middle of the viewport, and the
// version line follows PyPI. Nothing else on the page moves on its own.

const VIEW = { fleet: "fleet", gauges: "fleet" };

(() => {
  const stage = document.getElementById("stage");
  const steps = [...document.querySelectorAll(".step")];
  if (!stage || !steps.length) return;
  const a2 = stage.querySelector(".a2");

  const show = (step) => {
    if (stage.dataset.step === step) return;
    stage.dataset.step = step;
    a2.dataset.view = VIEW[step] || "session";
    for (const s of steps) s.classList.toggle("on", s.dataset.step === step);
  };

  // A step is current while it crosses a band around the middle of the viewport.
  const io = new IntersectionObserver(
    (entries) => {
      for (const e of entries) if (e.isIntersecting) show(e.target.dataset.step);
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
