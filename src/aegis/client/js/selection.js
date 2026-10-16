// A small, browser-native toolbar for actions on selected assistant text.
// The selection stays in the document while a person chooses an action, so a
// button's mousedown must not replace it before the click is handled.

const ownerOf = (node) => {
  const el = node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement;
  return el?.closest(".row.prose > .body") || null;
};

const clamp = (n, min, max) => Math.min(Math.max(n, min), Math.max(min, max));

export class SelectionMenu {
  constructor(menu, { root, scroller, onAction }) {
    this.menu = menu;
    this.root = root;
    this.scroller = scroller;
    this.onAction = onAction;
    this.text = "";
    this.owner = null;
    this.frame = 0;

    this.onSelection = () => {
      cancelAnimationFrame(this.frame);
      this.frame = requestAnimationFrame(() => this.refresh());
    };
    document.addEventListener("selectionchange", this.onSelection);
    document.addEventListener("pointerdown", (ev) => {
      if (!this.menu.contains(ev.target)) this.close();
    });
    this.scroller.addEventListener("scroll", () => this.close(), { passive: true });
    window.addEventListener("resize", () => this.close());
    const view = this.root.closest("#a2");
    if (view) {
      new MutationObserver(() => view.dataset.view !== "session" && this.close()).observe(view, {
        attributes: true,
        attributeFilter: ["data-view"],
      });
    }
    new MutationObserver(() => this.owner && !this.root.contains(this.owner) && this.close()).observe(this.root, {
      childList: true,
      subtree: true,
    });
    this.menu.addEventListener("mousedown", (ev) => ev.preventDefault());
    this.menu.addEventListener("click", (ev) => {
      const button = ev.target.closest("[data-selection-act]");
      if (!button) return;
      const text = this.text;
      const action = button.dataset.selectionAct;
      this.close();
      this.onAction(action, text);
    });
  }

  close() {
    cancelAnimationFrame(this.frame);
    this.text = "";
    this.owner = null;
    this.menu.hidden = true;
  }

  refresh() {
    const selection = window.getSelection();
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return this.close();
    const range = selection.getRangeAt(0);
    const start = ownerOf(selection.anchorNode);
    const end = ownerOf(selection.focusNode);
    if (!start || start !== end || !this.root.contains(start) || this.menu.contains(range.commonAncestorContainer)) {
      return this.close();
    }
    const text = selection.toString().trim();
    if (!text) return this.close();
    this.owner = start;
    this.text = text;
    this.menu.hidden = false;
    this.position(range.getBoundingClientRect());
  }

  position(rect) {
    // The menu is fixed to the viewport, so a response can scroll without
    // changing the coordinate system between measuring and painting.
    const gap = 8;
    const margin = 8;
    const width = this.menu.offsetWidth;
    const height = this.menu.offsetHeight;
    const left = clamp(rect.left + (rect.width - width) / 2, margin, window.innerWidth - width - margin);
    let top = rect.bottom + gap;
    if (top + height > window.innerHeight - margin) top = rect.top - height - gap;
    top = clamp(top, margin, window.innerHeight - height - margin);
    this.menu.style.left = `${Math.round(left)}px`;
    this.menu.style.top = `${Math.round(top)}px`;
  }
}
