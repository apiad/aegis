# Dictation live text — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dictated words land in the composer a second after each pause, and a final pass over each 20-to-28-second stretch replaces them; pieces with under a second of voice are never transcribed alone; a stop the person did not ask for says why.

**Architecture:** `Chunker` in `src/aegis/client/js/dictation.js` grows a second lane: it cuts provisional pieces of 4 to 8 s and, at the first provisional boundary 20 s or more after the last final cut, emits the whole stretch as a final piece. `Dictation` keeps items in order, inserts provisional text as today and replaces the span a final covers once its parts are done. `micSource` reports a track that ends or a context that leaves `running`, and `stop` takes a reason.

**Tech Stack:** Plain ES modules in the browser, the Cactus Whistle WASM build behind the existing worker, Playwright browser tests in `tests/test_browser.py` with the stub engine in `tests/fixtures/dictation/needle.js` (answers each piece with its length in seconds; 50 ms under 12 s, 400 ms at 12 s or more).

**Spec:** `docs/superpowers/specs/2026-10-09-dictation-live-text-design.md`

## Global Constraints

- Audio never leaves the browser (DESIGN.md). Nothing in this plan touches the server or the worker.
- Numbers are the spec's: provisional pieces 4 to 8 s (`PREVIEW_MIN`, `PREVIEW_MAX`), final at the first boundary at or past 20 s (`FINAL_MIN`), pause 300 ms under RMS 0.01, voice gate 1 s of 200 ms frames over RMS 0.01 at 50 ms hops, tail split over 10 s at the quietest 200 ms of the middle third.
- No configuration key. No visual marker on provisional text.
- Every chunk of code, comment and test name in English. Commits in conventional-commit form, each ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Work on branch `feat/dictation-live` in `.claude/worktrees/dictation-live`. Run `uv sync` and `uv run playwright install chromium` there once before the first test.
- Browser tests: `uv run pytest tests/test_browser.py -k dictation -q` while iterating; `make check` and `rift check` before the PR; `make bench` table in the PR body.

## Review Focus

1. **A pause right at the 4 s mark with a word straddling it.** The cut lands in the middle of the 300 ms window, as in 2.3.0; the final pass hears the whole word. Pinned by Task 1's two-lane test (the provisional boundary sits inside the gap).
2. **The person types before the dictated span while a final is in flight.** The final must still replace the right text, found by content, not by stale offsets. Pinned in Task 2 (typing-before test).
3. **The person edits inside the provisional span.** Their edit stays; the final is dropped. Pinned in Task 2.
4. **A recording of one short word.** Nothing was emitted; the stop must still transcribe it (`onlyPiece`). Pinned in Task 1's finish test.
5. **The browser ends the track mid-recording.** The recording finishes, the pieces so far land, and the composer says why. Pinned in Task 3.

---

### Task 1: The chunker's two lanes and the voice gate

**Files:**
- Modify: `src/aegis/client/js/dictation.js` (constants, `voiceSecs`, `Chunker`)
- Test: `tests/test_browser.py` (replace `test_dictation_cuts_at_the_first_pause_after_20s_and_at_30s_without_one` and `test_dictation_splits_a_long_tail_in_two_and_drops_silence`)

**Interfaces:**
- Produces: `Chunker(emit)` whose `emit` receives `{ parts: Float32Array[], final: boolean }`: a provisional piece has one part of 4 to 8 s; a final has one part (the stretch) or two (the tail at stop, split). `Chunker.finish(onlyPiece = false)` returns the same shapes in order. `voiceSecs(a: Float32Array): number`, exported.

- [ ] **Step 1: Replace the two chunker tests**

In `tests/test_browser.py`, delete `test_dictation_cuts_at_the_first_pause_after_20s_and_at_30s_without_one` and `test_dictation_splits_a_long_tail_in_two_and_drops_silence`; in their place:

```python
def test_dictation_cuts_provisional_pieces_at_4_to_8s_and_a_final_past_20s(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const out = []; const c = new m.Chunker((p) => out.push({ final: p.final, secs: p.parts.map((a) => +(a.length / 16000).toFixed(2)) }));
        for (const p of [tone(5), gap(0.5), tone(17), gap(0.5), tone(3)])
            for (let i = 0; i < p.length; i += 1600) c.push(p.subarray(i, i + 1600));
        return out;""",
    )
    prov = [p for p in got if not p["final"]]
    fin = [p for p in got if p["final"]]
    assert 5.0 <= prov[0]["secs"][0] <= 5.4, "the pause after 5 s cuts the first piece"
    # A pure tone has no quiet point, so the cut inside [4 s, 8 s] is wherever
    # rounding puts it: assert the range, not the place.
    assert all(4.0 <= p["secs"][0] <= 8.0 for p in prov[1:]), prov
    assert len(fin) == 1 and len(fin[0]["secs"]) == 1
    idx = got.index(fin[0])
    before = [p["secs"][0] for p in got[:idx]]
    assert 20 <= fin[0]["secs"][0] <= 28 and sum(before[:-1]) < 20, "closed at the first boundary past 20 s"
    assert abs(fin[0]["secs"][0] - sum(before)) < 0.05, "the final is exactly the joined provisional pieces"


def test_dictation_voice_gate_skips_quiet_pieces_but_the_final_spans_them(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const out = []; const c = new m.Chunker((p) => out.push({ final: p.final, secs: +(p.parts[0].length / 16000).toFixed(1) }));
        const quiet = [tone(0.5), gap(3.5)];
        for (let k = 0; k < 6; k++) for (const p of quiet) for (let i = 0; i < p.length; i += 1600) c.push(p.subarray(i, i + 1600));
        return { out, voiced: m.voiceSecs(tone(2)), quiet: m.voiceSecs(gap(2)), sliver: m.voiceSecs(tone(0.5)) };""",
    )
    assert got["voiced"] >= 1.8 and got["quiet"] == 0 and 0.3 <= got["sliver"] <= 0.6
    assert [p for p in got["out"] if not p["final"]] == [], "no provisional piece had a second of voice"
    fin = [p for p in got["out"] if p["final"]]
    assert len(fin) == 1 and fin[0]["secs"] >= 20, "the final still spans the quiet pieces"


def test_dictation_finish_returns_the_pending_piece_then_the_tail_in_halves(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const fin = (only, ...parts) => { const c = new m.Chunker(() => {}); for (const p of parts) c.push(p); return c.finish(only).map((p) => ({ final: p.final, secs: p.parts.map((a) => +(a.length / 16000).toFixed(1)) })); };
        return { long: fin(false, tone(6), gap(0.3), tone(8)), short: fin(false, tone(4)),
                 silent: fin(false, gap(5)), sliver: fin(false, tone(0.5)), only: fin(true, tone(0.5)) };""",
    )
    long = got["long"]
    assert all(not p["final"] for p in long[:-1]) and long[-1]["final"]
    assert len(long[-1]["secs"]) == 2 and 14.1 <= sum(long[-1]["secs"]) <= 14.4, "the 14.3 s stretch splits in two"
    assert got["short"] == [{"final": False, "secs": [4.0]}, {"final": True, "secs": [4.0]}]
    assert got["silent"] == [] and got["sliver"] == []
    assert got["only"] == [{"final": True, "secs": [0.5]}], "a one-word recording is still transcribed"
```

- [ ] **Step 2: Run the three tests and see them fail**

Run: `cd .claude/worktrees/dictation-live && uv run pytest tests/test_browser.py -k "provisional or voice_gate or finish_returns" -q`
Expected: 3 failed (`p.parts` undefined, `m.voiceSecs` not a function).

- [ ] **Step 3: Rewrite the constants, add `voiceSecs`, rewrite `Chunker`**

In `src/aegis/client/js/dictation.js`, replace the constants block and `Chunker`:

```js
export const SR = 16000;
// Two lanes (docs/superpowers/specs/2026-10-09-dictation-live-text-design.md):
// provisional pieces of 4 to 8 s that show words a second after a pause, and
// final pieces of 20 to 28 s made of them, whose text replaces the provisional
// text. Joined-up finals measured 29.4% WER against 30.4% for a plain 20 s cut.
const PREVIEW_MIN = 4 * SR;
const PREVIEW_MAX = 8 * SR;
const FINAL_MIN = 20 * SR;
const PAUSE = 0.3 * SR;
const QUIET = 0.01;
// A piece with under a second of voice is what hallucinates ("Thank you."): it
// is never transcribed alone. Silence itself comes back empty from the engine.
const VOICE = 1.0;
// At stop, a longer tail is split across both workers: the wait after stop is
// the one you feel, and two halves finish 1.9 times sooner than the whole.
const SPLIT_OVER = 10 * SR;
const FRAME = 0.2 * SR;
const HOP = 0.05 * SR;
```

Keep `rms`, `quietest`, `resampler` as they are. Add after `quietest`:

```js
// Seconds of 200 ms frames, 50 ms apart, whose RMS is over the pause threshold.
export function voiceSecs(a) {
  let n = 0;
  for (let s = 0; s + FRAME <= a.length; s += HOP) if (rms(a, s, s + FRAME) > QUIET) n++;
  return n * (HOP / SR);
}

function concat(parts) {
  const out = new Float32Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}
```

Replace the `Chunker` class:

```js
// Cuts the 16 kHz stream into pieces for the two lanes. `emit` gets
// { parts, final }: a provisional piece has one part; a final piece is the
// stretch since the previous final cut (one part), or at stop its two halves.
export class Chunker {
  constructor(emit) {
    this.emit = emit;
    this.buf = new Float32Array(0); // not yet cut
    this.stretch = []; // pieces cut since the last final, voiced or not
  }

  push(samples) {
    this.buf = concat([this.buf, samples]);
    for (;;) {
      const n = this.buf.length;
      if (n >= PREVIEW_MIN && rms(this.buf, n - PAUSE, n) < QUIET) this.cut(n - PAUSE / 2);
      else if (n >= PREVIEW_MAX) this.cut(quietest(this.buf, PREVIEW_MIN, PREVIEW_MAX));
      else return;
    }
  }

  cut(at) {
    const piece = this.buf.slice(0, at);
    this.buf = this.buf.slice(at);
    this.stretch.push(piece);
    if (voiceSecs(piece) >= VOICE) this.emit({ parts: [piece], final: false });
    if (this.stretch.reduce((n, p) => n + p.length, 0) >= FINAL_MIN) {
      const audio = concat(this.stretch);
      this.stretch = [];
      if (voiceSecs(audio) >= VOICE) this.emit({ parts: [audio], final: true });
    }
  }

  // The pieces at stop, in order: what was pending as a provisional piece, then
  // the stretch as a final, in two halves cut at the quiet point of its middle
  // third when it is long. A stretch with under a second of voice is dropped,
  // unless it is all the recording has (`onlyPiece`).
  finish(onlyPiece = false) {
    const out = [];
    const t = this.buf;
    this.buf = new Float32Array(0);
    if (t.length) {
      this.stretch.push(t);
      if (voiceSecs(t) >= VOICE) out.push({ parts: [t], final: false });
    }
    const audio = concat(this.stretch);
    this.stretch = [];
    if (!audio.length || (voiceSecs(audio) < VOICE && !onlyPiece)) return out;
    if (audio.length <= SPLIT_OVER) out.push({ parts: [audio], final: true });
    else {
      const third = Math.floor(audio.length / 3);
      const at = quietest(audio, third, 2 * third);
      out.push({ parts: [audio.slice(0, at), audio.slice(at)], final: true });
    }
    return out;
  }
}
```

Leave `Dictation` as it is for now; it still calls `new Chunker((a) => this.enqueue(rec, a))` and `rec.chunker.finish()`, which Task 2 rewrites. Until then the other dictation tests fail; that is expected between Task 1 and Task 2.

- [ ] **Step 4: Run the three tests and see them pass**

Run: `uv run pytest tests/test_browser.py -k "provisional or voice_gate or finish_returns" -q`
Expected: 3 passed. If the first test's index assert is off by one, read the pieces the run prints, confirm they follow the rule (5.15, 8, 8, then final at 21.15), and fix the index.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/client/js/dictation.js tests/test_browser.py
git commit -F - <<'EOF'
feat(dictation): the chunker cuts two lanes and gates pieces by voice (#210)

Provisional pieces of 4 to 8 s, and a final piece of the stretch at the
first boundary past 20 s. A piece with under a second of voiced frames is
not emitted on its own; the final still spans it.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 2: Items, groups, priority and replacement in `Dictation`

**Files:**
- Modify: `src/aegis/client/js/dictation.js` (`insert` → a `doc` abstraction with `insert` and `replace`; `Dictation.start/stop/enqueue/dispatch/answer`)
- Test: `tests/test_browser.py` (rewrite `test_dictation_inserts_pieces_in_order_after_the_last_one_and_keeps_typing`; add the replacement, typing-inside, typing-before and `pick` tests)

**Interfaces:**
- Consumes: `Chunker` emitting `{ parts, final }` and `finish(onlyPiece)` from Task 1.
- Produces: `pick(queue)` exported: index of the job a free worker takes (the first provisional, else 0). `Dictation.stop(reason = "button")` and `toggle(target, reason = "button")` (the reason is used in Task 3; here it is accepted and ignored).

- [ ] **Step 1: Rewrite the insertion test and add three more**

Replace `test_dictation_inserts_pieces_in_order_after_the_last_one_and_keeps_typing` with:

```python
def test_dictation_shows_provisional_text_and_replaces_it_with_the_final(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        el.value = 'Before. After.'; el.setSelectionRange(7, 7);
        const errors = [], seen = [];
        el.addEventListener('input', () => seen.push(el.value));
        const d = new m.Dictation({ prepare, onError: (e) => errors.push(e) });
        await d.start({ el, key: null, current: () => null }, source);
        // Four 5 s phrases with pauses: pieces of 5.15 and 5.5 s, a final at 21.65 s.
        for (let k = 0; k < 4; k++) feed(tone(5), gap(0.5));
        await until(() => (/\\[2\\d\\.\\ds/.test(el.value)));
        const afterFinal = el.value;
        el.value += ' typed';
        // Two phrases and 3 s pending at stop: an 11 s stretch plus the 3 s tail.
        for (let k = 0; k < 2; k++) feed(tone(5), gap(0.5));
        feed(tone(3));
        await d.stop();
        await until(() => !(d.state !== 'idle'));
        if (errors.length) throw new Error(errors.join('; '));
        return { seen, afterFinal, end: el.value };""",
    )
    prov = [v for v in got["seen"] if re.search(r"\[5\.\ds kw=2\]", v) and not re.search(r"\[2\d\.", v)]
    assert len(prov) >= 4, got["seen"]
    assert re.fullmatch(r"Before\. \[2\d\.\ds kw=2\] After\.", got["afterFinal"]), got["afterFinal"]
    # At stop the 3 s pending piece showed first, then the 14 s stretch came back
    # as two halves that replaced the provisional pieces together.
    assert any(re.search(r"\[3\.[0-4]s kw=2\]", v) for v in got["seen"]), "the pending piece showed first"
    assert re.fullmatch(
        r"Before\. \[2\d\.\ds kw=2\] \[\d+\.\ds kw=2\] \[\d+\.\ds kw=2\] After\. typed", got["end"]
    ), got["end"]


def test_dictation_keeps_an_edit_inside_provisional_text_and_drops_the_final(
    dict_server, page
):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        const d = new m.Dictation({ prepare });
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(4.5), gap(0.5));
        await until(() => (el.value.includes('kw=')));
        el.value = el.value.replace('kw=2', 'EDITED');
        for (let k = 0; k < 3; k++) feed(tone(5), gap(0.5));
        await until(() => !(d.queue.length || d.workers.some((w) => w.job)));
        await sleep(100);
        await d.stop();
        await until(() => !(d.state !== 'idle'));
        return el.value;""",
    )
    assert got.startswith("[4.7s EDITED] [5.5s kw=2]"), got
    assert not re.search(r"\[2\d\.", got), "the final over the edited span was dropped"


def test_dictation_replaces_the_span_after_typing_before_it(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        const d = new m.Dictation({ prepare });
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(4.5), gap(0.5));
        await until(() => (el.value.includes('kw=')));
        el.value = 'Typed first. ' + el.value;
        for (let k = 0; k < 3; k++) feed(tone(5), gap(0.5));
        await until(() => (/\\[2\\d\\.\\ds/.test(el.value)));
        await d.stop();
        await until(() => !(d.state !== 'idle'));
        return el.value;""",
    )
    assert re.fullmatch(r"Typed first\. \[2\d\.\ds kw=2\]", got), got


def test_dictation_picks_provisional_jobs_before_final_ones(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """return [m.pick([{ final: true }, { final: true }, { final: false }]),
                m.pick([{ final: true }]), m.pick([{ final: false }, { final: true }])];""",
    )
    assert got == [2, 0, 0]
```

- [ ] **Step 2: Run the four tests and see them fail**

Run: `uv run pytest tests/test_browser.py -k "provisional_text or edit_inside or typing_before or picks_provisional" -q`
Expected: 4 failed.

- [ ] **Step 3: Rewrite insertion and `Dictation`**

Replace the `insert` function with a `doc` helper and two operations:

```js
// Where a recording's text goes: the textarea while it still shows the draft
// the recording started in, else that draft in localStorage.
function doc(rec) {
  const t = rec.target;
  if (t.key === null || t.current() === t.key) {
    return {
      get: () => t.el.value,
      set: (v) => {
        t.el.value = v;
        t.el.dispatchEvent(new Event("input", { bubbles: true }));
      },
    };
  }
  return { get: () => localStorage.getItem(t.key) || "", set: (v) => localStorage.setItem(t.key, v) };
}

// A separating space when the text before the insertion point does not end in one.
const lead = (before, text) => (before && !/\s$/.test(before) ? " " : "") + text;

// A provisional piece: at the place the last piece ended (clamped if the text
// shrank), remembered as the exact string inserted and its range.
function insert(rec, item, text) {
  if (!text) return;
  const d = doc(rec);
  const v = d.get();
  const at = Math.min(rec.at, v.length);
  const piece = lead(v.slice(0, at), text);
  d.set(v.slice(0, at) + piece + v.slice(at));
  Object.assign(item, { piece, from: at, to: at + piece.length });
  rec.at = at + piece.length;
}

// A final: replaces the provisional text it covers if that text is still there,
// at its recorded range or wherever it moved to; else the person edited it and
// the final is dropped. With nothing to cover it inserts like a piece.
function replace(rec, item, text) {
  const covered = item.covers.filter((c) => c.piece);
  if (!covered.length) return insert(rec, item, text);
  const d = doc(rec);
  const v = d.get();
  const expected = covered.map((c) => c.piece).join("");
  let from = covered[0].from;
  if (v.slice(from, from + expected.length) !== expected) {
    from = v.indexOf(expected);
    if (from < 0 || v.indexOf(expected, from + 1) >= 0) return;
  }
  const to = from + expected.length;
  const piece = text ? lead(v.slice(0, from), text) : "";
  d.set(v.slice(0, from) + piece + v.slice(to));
  Object.assign(item, { piece, from, to: from + piece.length });
  if (rec.at >= to) rec.at += piece.length - expected.length;
}

// The job a free worker takes: the first provisional one, else the oldest.
export function pick(queue) {
  const i = queue.findIndex((j) => !j.final);
  return i < 0 ? 0 : i;
}
```

In `Dictation`:

`start(target, source = micSource)`: the `rec` gains `items: []` (in order, awaiting insertion), `pending: []` (provisional items since the last final), `emitted: 0`; drop `order` and `done`. The chunker line becomes `rec.chunker = new Chunker((p) => this.enqueue(rec, p));`.

`stop(reason = "button")`: replace the finish loop with
```js
    for (const p of rec.chunker.finish(rec.emitted === 0)) this.enqueue(rec, p);
```

`enqueue(rec, { parts, final })`:
```js
  enqueue(rec, { parts, final }) {
    const item = { final, jobs: parts.length, texts: [], covers: final ? rec.pending.splice(0) : [] };
    rec.items.push(item);
    rec.emitted++;
    if (!final) rec.pending.push(item);
    parts.forEach((audio, i) => {
      this.queue.push({ id: this.next++, i, audio, item, rec, final, secs: audio.length / SR });
    });
    this.dispatch();
  }
```

`dispatch()`: `const job = this.queue.splice(pick(this.queue), 1)[0];` in place of `shift()`.

`answer(w, data)`:
```js
  answer(w, data) {
    const job = w.job;
    w.job = null;
    if (!job) return;
    const { rec, item } = job;
    if (data.type === "text") item.texts[job.i] = data.text;
    else {
      item.texts[job.i] = "";
      this.onError(`A piece of ${job.secs.toFixed(1)} s could not be transcribed: ${data.message}`);
    }
    item.jobs--;
    while (rec.items.length && rec.items[0].jobs === 0) {
      const it = rec.items.shift();
      const text = it.texts.filter(Boolean).join(" ");
      if (it.final) replace(rec, it, text);
      else insert(rec, it, text);
    }
    this.dispatch();
    this.settle();
  }
```

`settle()`: `!r.order.length` becomes `!r.items.length`. `abort()`: `this.queue = this.queue.filter((j) => j.rec !== rec); rec.items = [];` in place of `rec.order = []`. `toggle(target, reason = "button")` passes `reason` to `stop`. `finish(el)` calls `this.stop("send")`.

Update the module's header comment: the second sentence becomes "cuts it into provisional pieces at pauses and, every 20 s or so, re-transcribes the stretch as one final piece whose text replaces them, in one of two Whistle workers".

- [ ] **Step 4: Run every dictation test**

Run: `uv run pytest tests/test_browser.py -k dictation -q`
Expected: all pass, including the untouched `test_dictation_puts_late_text_in_the_draft_the_textarea_no_longer_shows` (its 3 s tone now lands as `[3.0s kw=2]` twice: a provisional piece replaced by the same final text, so the draft still starts with `draft of a [3.0s kw=2]`) and the `mic_page` tests.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/client/js/dictation.js tests/test_browser.py
git commit -F - <<'EOF'
feat(dictation): provisional text lands at once and the final pass replaces it (#210)

Items are inserted in order; a final item replaces the span its
provisional pieces occupy when that text is still there, found by content
when the person typed before it, and is dropped when they edited inside
it. Provisional jobs go to a free worker before final ones.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 3: Why a recording stopped

**Files:**
- Modify: `src/aegis/client/js/dictation.js` (`micSource`, `Dictation.start/stop`)
- Modify: `src/aegis/client/js/app.js:412` (`follow`), `:859` (Alt+M), `:960-961` (mic clicks)
- Test: `tests/test_browser.py` (one new test; `run_dictation`'s `source` grows an `end` hook)

**Interfaces:**
- Consumes: `Dictation.stop(reason)` from Task 2.
- Produces: `micSource(onSamples, onEnd)` where `onEnd(reason: "track" | "suspended")` fires once if the browser ends the capture. Reasons the app passes: `"button"`, `"key"`, `"send"`, `"tab"`.

- [ ] **Step 1: Extend the test source and write the test**

In `run_dictation`, change the two lines
```js
            let push = null;
            const source = async (on) => { push = on; return async () => {}; };
```
to
```js
            let push = null, ended = null;
            const source = async (on, end) => { push = on; ended = end; return async () => {}; };
            const endCapture = (why) => ended(why);
```
and add `'endCapture'` to both argument lists of the constructed function (the names list and the call), after `'prepare'` / `prepare`.

Add the test:

```python
def test_dictation_says_why_when_the_browser_ends_the_capture(dict_server, page):
    page.goto(dict_server.url)
    got = run_dictation(
        page,
        """const el = document.createElement('textarea'); document.body.append(el);
        const errors = [], states = [];
        const d = new m.Dictation({ prepare, onError: (e) => errors.push(e), onState: (s) => states.push(s) });
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(3));
        endCapture('track');
        while (d.state !== 'idle') await sleep(20);
        const first = { errors: [...errors], text: el.value };
        await d.start({ el, key: null, current: () => null }, source);
        feed(tone(2));
        await d.stop('button');
        while (d.state !== 'idle') await sleep(20);
        return { first, errors, states };""",
    )
    assert got["first"]["errors"] == ["Microphone stopped: the browser ended the microphone"]
    assert "[3.0s" in got["first"]["text"], "what was said before the track ended still lands"
    assert len(got["errors"]) == 1, "a stop the person asked for says nothing"
    assert got["states"][-1] == "idle"
```

- [ ] **Step 2: Run it and see it fail**

Run: `uv run pytest tests/test_browser.py -k says_why -q`
Expected: FAIL (`errors` empty; the state never returns to idle, so the test times out — if so, that is the failure).

- [ ] **Step 3: Implement**

`micSource(onSamples, onEnd)`: after `const node = ...` and the connections, add

```js
  let closing = false;
  stream.getAudioTracks()[0].addEventListener("ended", () => !closing && onEnd("track"));
  ctx.addEventListener("statechange", () => !closing && ctx.state !== "running" && onEnd("suspended"));
```
and in the returned stop function set `closing = true;` first. Both reasons map to one sentence each:

```js
const STOPPED = {
  track: "Microphone stopped: the browser ended the microphone",
  suspended: "Microphone stopped: the browser suspended the audio",
};
```

In `Dictation.start`, the capture call becomes
```js
      rec.stopCapture = await source(
        (s) => {
          this.onLevel(rms(s));
          rec.chunker.push(s);
        },
        (why) => this.rec === rec && this.stop(why),
      );
```

In `Dictation.stop(reason = "button")`, after `this.settle();` add
```js
    if (STOPPED[reason]) this.onError(STOPPED[reason]);
```

In `app.js`: `dictation.stop("tab")` at line 412; `dictation.toggle(v === "spawn" ? spawnTarget() : sessionTarget(), "key")` at 859; the two click handlers pass `"button"` (the default, so they can stay as they are; pass it anyway so the four reasons read in one place).

- [ ] **Step 4: Run the dictation tests**

Run: `uv run pytest tests/test_browser.py -k dictation -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/aegis/client/js/dictation.js src/aegis/client/js/app.js tests/test_browser.py
git commit -F - <<'EOF'
feat(dictation): a capture the browser ends stops the recording and says so (#210)

The track's ended event and a context that leaves running stop the
recording with a reason; the four stops the person asks for carry theirs
and show nothing.

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
```

---

### Task 4: Docs, gates and the PR

**Files:**
- Modify: `DESIGN.md:289-296` ("Audio never leaves the browser")
- Modify: `docs/superpowers/specs/2026-10-09-dictation-live-text-design.md` (status; the `emit` shapes read `parts`)
- Create: `changelog.d/210-dictation-live-text.added.md`

- [ ] **Step 1: DESIGN.md**

In the "Audio never leaves the browser" paragraph, after the sentence naming `js/dictation.js`, add: "The client cuts speech into provisional pieces of 4 to 8 s whose text lands at once, and every 20 s or so re-transcribes the stretch as one final piece that replaces it; a piece with under a second of voice is never transcribed alone."

- [ ] **Step 2: The spec**

Status line: `**Status: built, 2026-10-09 (#<PR>).** Issue #210.` once the PR number exists. In "The chunker: two lanes over one buffer", the two shapes read `{ parts: [audio], final: false }` and `{ parts, final: true }` with one part for a stretch and two for the split tail, and the sentence on `finish` says it returns those shapes.

- [ ] **Step 3: Changelog fragment**

```markdown
- **Dictation shows words while you talk.** Text landed only every 20 seconds,
  and the first 20 looked like nothing was happening. Now each pause puts the
  words on screen about a second later, and a final pass over every 20-to-28
  second stretch replaces them with the text that stays. Pieces with under a
  second of voice are no longer transcribed alone, which is where `Thank you.`
  came from; and a recording the browser ends on its own says so.
```

Run: `make changelog-check`

- [ ] **Step 4: Gates**

```bash
uv run pytest tests/test_browser.py -k dictation -q
make check
rift check
GITHUB_ACTIONS=true uv run pytest -q -m "not live" tests/test_browser.py -k dictation
make bench
```

Read each exit code directly, never through a pipe. Keep the bench table for the PR body.

- [ ] **Step 5: Check it by hand on a server from the branch**

```bash
mkdir -p /tmp/dl-serve && cd /tmp/dl-serve && uv run --project /home/apiad/Workspace/repos/aegis/.claude/worktrees/dictation-live aegis serve --port 8750
```
Open `http://localhost:8750`, press the mic, talk in Spanish with pauses: words appear a second after each pause; around 20 s the stretch is rewritten; stop: the last words appear within about a second, the correction within a few. Say one short word and stop: it is transcribed. Ask Alex to do the same on zion and on the phone. Report what was checked and by whom.

- [ ] **Step 6: Commit and open the PR**

```bash
git add DESIGN.md docs/superpowers/specs/2026-10-09-dictation-live-text-design.md changelog.d/210-dictation-live-text.added.md
git commit -F - <<'EOF'
docs: dictation live text in DESIGN.md, the spec's status and a release note (#210)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
EOF
git push
gh pr create --title "Dictation: text while you talk, and no \"Thank you\" from slivers of speech (#210)" --body-file -
```

PR body: what was measured (the spec's three tables in short), what was tried and dropped (shorter chunks alone: 5.4 points of WER for text that stays), the bench table, what was checked by hand and by whom, and the attribution line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Then stop: Alex merges.
