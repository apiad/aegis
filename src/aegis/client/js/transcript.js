// The transcript: entry nodes keyed by id, patched in place.
//
// It follows the bottom while the reader is at the bottom; once they scroll
// up, new entries raise the jump pill instead of moving the page.

import { render } from "./entries.js";

export class Transcript {
  constructor(scroller, list, jump) {
    this.scroller = scroller;
    this.list = list;
    this.jump = jump;
    this.nodes = new Map();
    scroller.addEventListener("scroll", () => {
      if (this.atBottom()) this.jump.hidden = true;
    });
    jump.addEventListener("click", () => this.toBottom());
  }

  atBottom() {
    const s = this.scroller;
    return s.scrollHeight - s.scrollTop - s.clientHeight < 48;
  }

  toBottom() {
    this.scroller.scrollTop = this.scroller.scrollHeight;
    this.jump.hidden = true;
  }

  snapshot(entries) {
    this.nodes.clear();
    const frag = document.createDocumentFragment();
    for (const e of entries) {
      const n = render(e);
      this.nodes.set(e.id, n);
      frag.append(n);
    }
    this.list.replaceChildren(frag);
    this.toBottom();
  }

  apply(ops) {
    const follow = this.atBottom();
    let added = false;
    for (const op of ops) {
      if (op.upsert) {
        const e = op.upsert;
        const n = render(e);
        const old = this.nodes.get(e.id);
        if (old) {
          // A row the reader opened or closed keeps that state across updates.
          const was = old.querySelector("details");
          const now = n.querySelector("details");
          if (was && now && old.dataset.touched) {
            now.open = was.open;
            n.dataset.touched = "1";
          }
          old.replaceWith(n);
        } else {
          this.list.append(n);
          added = true;
        }
        this.nodes.set(e.id, n);
        n.querySelector("summary")?.addEventListener("click", () => (n.dataset.touched = "1"));
      } else if (op.remove !== undefined) {
        this.nodes.get(op.remove)?.remove();
        this.nodes.delete(op.remove);
      }
    }
    if (follow) this.toBottom();
    else if (added) this.jump.hidden = false;
  }

  clear() {
    this.nodes.clear();
    this.list.replaceChildren();
  }
}
