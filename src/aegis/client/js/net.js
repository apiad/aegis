// What needs the server while there is none. app.js sets #a2[data-offline]
// while the socket is not open; every control marked `.needs-net` then says
// so (aria-disabled, and "Needs the connection" as its title, its own title
// kept in data-title), and its handler returns early on offline().

export const offline = () => "offline" in document.getElementById("a2").dataset;

export function markNeedsNet(within = document) {
  const off = offline();
  for (const b of within.querySelectorAll(".needs-net")) {
    const marked = b.getAttribute("aria-disabled") === "true";
    if (off && !marked) {
      b.dataset.title = b.title;
      b.title = "Needs the connection";
      b.setAttribute("aria-disabled", "true");
    } else if (!off && marked) {
      b.title = b.dataset.title || "";
      delete b.dataset.title;
      b.removeAttribute("aria-disabled");
    }
  }
}
