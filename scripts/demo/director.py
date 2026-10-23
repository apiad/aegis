"""Record aegis itself, for the landing page and the help pages.

The director runs a real aegis in its own process, in a fixed root, whose
agents run the real Claude Code against the canary's fake model
(``tests/canary/fakemodel.py``), so every tab, card and pane on screen is
aegis's own code and no model quota is spent. What aegis reads of the machine
(host, quotas, version) comes from a scripted ``World`` (world.py). A beat (``beats/*.py``) scripts
the fake with ``Rule``s and drives the real client through Playwright, as a
person does: it types, clicks, opens tabs.

On top, ``stage.js`` adds a camera (a 3D transform on the client's root) that
drifts, tilts and zooms, a spotlight that dims all but one element, a lift
that brings one forward, and overlays the camera does not move: the logo card, a title card,
captions, callouts that point at a real element, a lower third, a cursor and a
ring where it clicks.

Frames come from the CDP screencast, which sends one when the page changes,
with its timestamp; each is written to ffmpeg as often as the fixed output
rate needs. A machine that draws the client at 10 frames a second cannot
film motion at 30, so the world runs ``--slow`` times slower than the video:
the page's animations (CDP ``Animation.setPlaybackRate``), the camera, the
typing, the fake model and every wait, while the video's clock runs that much
faster. Only the client's own clocks (a turn's elapsed seconds) are not slowed. Narration clips are cached by the
hash of their line, mixed at the time the beat cued them, and the same lines
and times go into a WebVTT file. A music bed (``--music``, or the beat's
``MUSIC``) loops under them, ducked while a line plays.

    uv run python scripts/demo/director.py rung2 --out /tmp/demo
    uv run python scripts/demo/director.py rung2 --tts 'gen-audio {yaml} --out {out}'
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import dataclasses
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "src"))  # this tree's aegis, not the venv's

from tests.canary.fakemodel import Call, FakeModel, Reply, Rule, Say  # noqa: E402
from tests.canary.fakemodel import Shell as _Shell  # noqa: E402
from world import Server, World  # noqa: E402

__all__ = ["Call", "Rule", "Say", "Shell", "Stage", "World"]


@dataclasses.dataclass(frozen=True)
class Shell(_Shell):
    """The canary's shell step, with the description its row shows."""

    desc: str = ""


FPS = 30
# The client laid out at 1280x720 and drawn at 1.5x: a 1080p frame whose text
# reads on a phone. Overlay sizes are in these CSS pixels.
W, H, SCALE = 1280, 720, 1.5
# A clip is cued this long before its line is spoken, and the beat waits this
# long after it: a voice that starts on the cut, or a cut on its last word,
# sounds clipped.
LEAD, TAIL = 0.8, 1.6
# Silence between two lines that would otherwise run into each other.
GAP = 0.25
SLOW = 3.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DemoModel(FakeModel):
    """The canary's fake, slowed to a person's pace, with side calls (a
    session's title) answered from ``titles``. A string argument may name a
    value an earlier call of this turn returned: ``{result:id}`` becomes the
    latest ``"id"`` in the tool results, as artifact_send needs the id
    artifact_create gave."""

    def __init__(self) -> None:
        super().__init__()
        self.pace = 0.8  # video seconds per step
        self.slow = 1.0
        self.titles: dict[str, str] = {}
        self.descs: dict[str, str] = {}

    def script(self, *rules: Rule) -> None:
        """Add rules: a film's later beats keep the earlier ones live."""
        super().script(*self.rules, *rules)
        self.descs |= {
            s.cmd.split("{")[0]: s.desc
            for r in rules
            for s in r.steps
            if isinstance(s, Shell) and s.desc
        }

    def decide(self, c) -> Reply:
        if not c.tools:
            for when, title in self.titles.items():
                if when in c.last_person or when in c.history:
                    return Reply(text=title)
        r = super().decide(c)
        if not c.tools:
            return r
        args = _fill_results(r.args, c.tool_results) if r.args else r.args
        if args and "description" in args:
            cmd = args.get("command", "")
            for head, desc in self.descs.items():
                if cmd.startswith(head):
                    args = {**args, "description": desc}
        delay = max(r.delay, self.pace) * self.slow
        return dataclasses.replace(r, args=args, delay=delay)


def _fill_results(value, results: list[str]):
    if isinstance(value, dict):
        return {k: _fill_results(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_results(v, results) for v in value]
    if not isinstance(value, str) or "{result:" not in value:
        return value

    def one(m: re.Match) -> str:
        key = m.group(1)
        for raw in reversed(results):
            # A result is JSON text, often JSON inside JSON: unescape and search.
            text = raw
            for _ in range(4):
                if hit := re.search(rf'"{key}"\s*:\s*"([^"]+)"', text):
                    return hit.group(1)
                text = text.replace("\\\\", "\\").replace('\\"', '"')
        raise KeyError(f"no {key!r} in this turn's tool results")

    return re.sub(r"\{result:(\w+)\}", one, value)


def _serve_fake(fm: FakeModel, port: int) -> None:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(fm.app(), host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True).start()
    for _ in range(500):
        if server.started:
            return
        time.sleep(0.01)
    raise RuntimeError("the fake model did not start")


def _harness_env(home: Path, base: str) -> dict[str, str]:
    """Claude Code in a home of its own, every model request to the fake."""
    (home / "claude").mkdir(parents=True, exist_ok=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in (
            "CLAUDECODE",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "AEGIS_OPENER",
        )
        and not k.startswith("AEGIS_")
    }
    return env | {
        "ANTHROPIC_BASE_URL": base,
        "ANTHROPIC_API_KEY": "sk-demo",
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        # Nothing of the machine's own: no login, no quota reading, no probes.
        "CLAUDE_CREDS": str(home / "none" / "credentials.json"),
        "CLAUDE_CONFIG": str(home / "none" / "claude.json"),
        "OPENCODE_AUTH": str(home / "none" / "opencode-auth.json"),
        "AEGIS_QUOTA_CACHE": str(home / "quota"),
        "AEGIS_DICTATION_DIR": str(home / "dictation"),
        "AEGIS_NET_PROBES": "offline",
    }


CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  sonnet: {harness: claude-code, model: sonnet, effort: medium, permission: full}
queues:
  reviews: {agent: sonnet, max_parallel: 2}
notices: {disk: false, memory: false, cpu: false, quota: false, plan: false,
  monitors: false, queues: false, status: false}
nudges: {silent_s: false}
harness_versions: {person: off, agent: off, startup: off}
"""


@dataclasses.dataclass
class Cue:
    t: float
    text: str
    clip: Path | None
    length: float


class Capture:
    """Screencast frames into ffmpeg at a fixed rate. The video's clock runs
    ``slow`` times faster than the wall's, as the world runs that much
    slower."""

    def __init__(self, cdp, ffmpeg: str, out: Path, slow: float) -> None:
        self.cdp, self.out, self.slow = cdp, out, slow
        self.proc = subprocess.Popen(
            [ffmpeg, "-loglevel", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg",
             "-framerate", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "18", "-pix_fmt", "yuv420p", str(out)],
            stdin=subprocess.PIPE,
        )  # fmt: skip
        self.t0: float | None = None
        self.last: bytes | None = None
        self.written = 0
        self.frames = 0
        cdp.on("Page.screencastFrame", self._frame)
        cdp.send(
            "Page.startScreencast",
            {"format": "jpeg", "quality": 85, "maxWidth": int(W * SCALE),
             "maxHeight": int(H * SCALE)},
        )  # fmt: skip

    def now(self) -> float:
        """Seconds of video so far."""
        return 0.0 if self.t0 is None else (time.time() - self.t0) / self.slow

    def _fill(self, until: float) -> None:
        if self.last is None:
            return
        target = int(until * FPS)
        while self.written < target:
            self.proc.stdin.write(self.last)
            self.written += 1

    def _frame(self, ev: dict) -> None:
        self.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
        ts = ev["metadata"].get("timestamp") or time.time()
        if self.t0 is None:
            self.t0 = ts
        self._fill((ts - self.t0) / self.slow)
        self.last = base64.b64decode(ev["data"])
        self.frames += 1

    def stop(self) -> float:
        self.cdp.send("Page.stopScreencast")
        self._fill(self.now() + 1 / FPS)
        self.proc.stdin.close()
        self.proc.wait()
        return self.written / FPS


class Stage:
    """What a beat drives: the fake model, the page, the camera, the overlays."""

    def __init__(self, model: DemoModel, page, root: Path, audio: Path,
                 tts: str | None, server: Server, slow: float):  # fmt: skip
        self.model, self.page, self.root = model, page, root
        self.audio, self.tts = audio, tts
        self.server, self.slow = server, slow
        self.cues: list[Cue] = []
        self.free_at = 0.0  # when the last line ends, in video seconds
        self.capture: Capture | None = None
        model.slow = slow
        self.vars: dict = {}  # what one beat leaves for the next

    # -- the model --
    def script(self, *rules: Rule, titles: dict[str, str] | None = None) -> None:
        """Add the fake's rules and the titles its side calls answer."""
        self.model.script(*rules)
        self.model.titles |= titles or {}

    def spawn(
        self, cwd: str, prompt: str, agent: str | None = None, parent: str | None = None
    ) -> dict:
        """Start a session off camera, as session_spawn does; returns its
        ``log_id`` and ``handle``. Its tab opens in the client. With ``parent``
        (a log id) it joins that session's group, as one spawned from its tab."""
        Path(cwd).mkdir(parents=True, exist_ok=True)
        params = {"cwd": cwd, "prompt": prompt} | ({"agent": agent} if agent else {})
        params |= {"from_session": parent} if parent else {}
        r = self.server.call("session.spawn", params)
        return {"log_id": r["log_id"], "handle": r.get("handle", "")}

    def pace(self, seconds: float) -> None:
        """How long the fake takes over each step of a turn, in video seconds."""
        self.model.pace = seconds

    # -- the world --
    def world(self, **readings) -> None:
        """Change what aegis reads of the machine: ``cpu``, ``ram``, ``disk``
        (percent), ``claude`` or ``opencode`` (percent per window), ``version``."""
        for k, v in readings.items():
            assert hasattr(self.server.world, k), f"no reading {k!r}"
            setattr(self.server.world, k, v)
        self.server.changed()

    # -- time --
    def now(self) -> float:
        return self.capture.now() if self.capture else 0.0

    def wait(self, seconds: float) -> None:
        """Wait this many seconds of video."""
        self.page.wait_for_timeout(int(seconds * self.slow * 1000))

    def until(self, selector: str, timeout: float = 90) -> None:
        self.page.wait_for_selector(selector, timeout=timeout * 1000)

    # -- the person --
    def _center(self, selector: str) -> tuple[float, float]:
        box = self.page.locator(selector).first.bounding_box()
        assert box, f"{selector} is not on screen"
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

    def mark(self, selector: str, name: str) -> str:
        """Tag the first visible element a Playwright selector finds, for the
        overlays, which take CSS: returns ``[data-demo=name]``."""
        self.page.locator(selector).locator("visible=true").first.evaluate(
            "(e, n) => { e.dataset.demo = n; }", name
        )
        return f'[data-demo="{name}"]'

    def still(self) -> None:
        """Wait for the camera's move to end."""
        self.page.wait_for_function("() => !demo.moving()", timeout=60000)

    def _onscreen(self, x: float, y: float) -> bool:
        return 0 <= x <= W and 0 <= y <= H

    def click(self, selector: str, move: float = 0.6) -> None:
        self.still()
        x, y = self._center(selector)
        if not self._onscreen(x, y):
            # Off camera: the click happens, unseen, as a person's would.
            self.page.locator(selector).first.evaluate("e => e.click()")
            return
        self.js("demo.point", x, y)
        self.wait(move)
        self.js("demo.ring", x, y)
        self.js("demo.shield", False)
        self.page.locator(selector).first.click(force=True)
        self.park()

    def park(self) -> None:
        """Move the real mouse off the UI and raise the shield: a hovered row
        opens its popover."""
        self.page.mouse.move(2, H - 2)
        self.js("demo.shield", True)

    def type(
        self, selector: str, text: str, cps: float = 28, enter: bool = True
    ) -> None:
        loc = self.page.locator(selector).first
        self.js("demo.shield", False)
        loc.click(force=True)
        self.park()
        loc.press_sequentially(text, delay=1000 * self.slow / cps)
        if enter:
            self.wait(0.4)
            loc.press("Enter")

    def click_frame(self, frame: str, selector: str, move: float = 0.6) -> None:
        """Click inside an iframe (an artifact's page)."""
        loc = self.page.frame_locator(frame).locator(selector).first
        box = loc.bounding_box()
        assert box, f"{selector} is not on screen in {frame}"
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.js("demo.point", x, y)
        self.wait(move)
        self.js("demo.ring", x, y)
        self.js("demo.shield", False)
        loc.click(force=True)
        self.park()

    def attach(self, button: str, path: Path) -> None:
        """Pick a file through the composer's attach button."""
        x, y = self._center(button)
        self.js("demo.point", x, y)
        self.wait(0.6)
        self.js("demo.ring", x, y)
        self.js("demo.shield", False)
        with self.page.expect_file_chooser() as fc:
            self.page.locator(button).first.click(force=True)
        fc.value.set_files(str(path))
        self.park()

    def render(self, html: str, out: Path, width: int, height: int) -> None:
        """A picture for a beat to attach: this HTML in a page of its own."""
        pg = self.page.context.browser.new_page(
            viewport={"width": width, "height": height}
        )
        pg.set_content(html)
        pg.screenshot(path=str(out))
        pg.close()

    def key(self, keys: str) -> None:
        self.page.keyboard.press(keys)

    # -- the camera and the overlays --
    def js(self, fn: str, *args) -> None:
        self.page.evaluate(f"(a) => {fn}(...a)", list(args))

    REST = {"rx": 0.0, "ry": 0.0, "rz": 0.0, "s": 1.0, "x": 0.0, "y": 0.0}

    def camera(self, ms: int = 1200, **to: float) -> None:
        """Move, over ``ms`` of video, to ``rx``, ``ry``, ``rz`` (degrees),
        ``s`` (scale), ``x``, ``y`` (px); what is not named returns to rest.
        Returns at once: the beat waits as long as it wants the shot."""
        self.js("demo.camera", {**self.REST, **to}, ms)

    def focus(
        self, selector: str, s: float = 1.6, ms: int = 1400, **tilt: float
    ) -> None:
        """Zoom ``s`` times on an element: its centre moves to the screen's.
        The element is measured with the camera at rest."""
        x, y = self.page.evaluate(
            "(sel) => { const r = demo.flat(sel); return [r.x + r.width / 2, r.y + r.height / 2]; }",
            selector,
        )
        self.camera(ms, s=s, x=(W / 2 - x) * s, y=(H / 2 - y) * s, **tilt)

    def overlay(self, html: str | None = None) -> None:
        """A full-frame layer of free HTML over the client, faded in; None
        fades it out. It may use the stage's classes and fonts."""
        self.js("demo.overlay", html)

    def phone(self, on: bool = True) -> None:
        """The same client in a phone's frame, sliding in at the right."""
        self.js("demo.phone", self.server.url if on else None)

    def drift(self, amount: float = 1.0) -> None:
        """A slow sway on top of every move, so no shot is ever still; 0 stops it."""
        self.js("demo.drift", amount)

    def spotlight(self, selector: str | None = None) -> None:
        """Dim everything but this element; None lifts the dimming."""
        self.js("demo.spotlight", selector)

    def lift(self, selector: str | None = None) -> None:
        """Bring this element forward, off the window's plane; None sets it back."""
        self.js("demo.lift", selector)

    def title(self, kicker: str = "", head: str = "", sub: str = "") -> None:
        self.js("demo.title", kicker, head, sub)

    def logo(self, sub: str | None = "") -> None:
        """The aegis logo and wordmark over a plain field, with ``sub`` (HTML)
        under them; None fades the card out."""
        self.js("demo.logo", sub)

    def lower(self, l1: str | None = None, l2: str = "") -> None:
        self.js("demo.lower", l1, l2)

    def callout(self, selector: str, label: str, **opts) -> int:
        return self.page.evaluate(
            "([s, l, o]) => demo.callout(s, l, o)", [selector, label, opts]
        )

    def uncallout(self, cid: int) -> None:
        self.js("demo.uncallout", cid)

    # -- the narration --
    def clip(self, text: str) -> Path:
        return self.audio / (hashlib.sha1(text.encode()).hexdigest()[:16] + ".mp3")

    def say(self, text: str, wait: bool = False) -> float:
        """Speak a line from now: its caption shows while it plays. Returns its
        length; with ``wait``, returns after it."""
        clip = self.clip(text)
        length = _duration(clip) if clip.exists() else max(1.5, len(text) / 15)
        # One voice at a time: a line cued while the last still plays waits
        # for it, and the beat with it.
        if (busy := self.free_at - self.now()) > 0:
            self.wait(busy)
        self.free_at = self.now() + length + GAP
        self.cues.append(Cue(self.now(), text, clip if clip.exists() else None, length))
        self.js("demo.caption", text, int(length * 1000))
        if wait:
            self.wait(length)
        return length


_ffmpeg = shutil.which("ffmpeg") or ""


def _duration(path: Path) -> float:
    out = subprocess.run(
        [_ffmpeg, "-i", str(path)], capture_output=True, text=True
    ).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    assert m, f"no duration in {path}: {out[-300:]}"
    return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])


def _speak_missing(beat, stage: Stage) -> None:
    """Make the clips a beat's lines lack, with ``--tts``."""
    lines = getattr(beat, "LINES", [])
    missing = [t for t in lines if not stage.clip(t).exists()]
    if not missing:
        return
    if not stage.tts:
        print(
            f"{len(missing)} line(s) have no clip; captions only:", *missing, sep="\n  "
        )
        return
    for text in missing:
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(
                "language: en\ncharacters:\n  narrator:\n    voice: "
                f"{getattr(beat, 'VOICE', 'jim')}\nscript:\n  - narrator: {json.dumps(text)}\n"
            )
        cmd = stage.tts.format(yaml=shlex.quote(f.name), out=shlex.quote(str(stage.clip(text))),
                               text=shlex.quote(text))  # fmt: skip
        subprocess.run(cmd, shell=True, check=True)
        os.unlink(f.name)


# The music bed's level under the voice, and its fade-out at the end, in seconds.
MUSIC_GAIN, FADE = 0.32, 3.0


def _mux(
    video: Path, cues: list[Cue], out: Path, length: float, music: Path | None = None
) -> None:
    clips = [c for c in cues if c.clip]
    if not clips and not music:
        shutil.copy(video, out)
        return
    args = [_ffmpeg, "-loglevel", "error", "-y", "-i", str(video)]
    for c in clips:
        args += ["-i", str(c.clip)]
    mix = "".join(
        f"[{i + 1}:a]adelay={int(c.t * 1000)}:all=1[a{i}];" for i, c in enumerate(clips)
    )
    mix += "".join(f"[a{i}]" for i in range(len(clips)))
    voice = f"amix=inputs={len(clips)}:normalize=0," if clips else "anullsrc,"
    mix += f"{voice}apad,atrim=0:{length:.3f}"
    if music:
        # Looped to the film's length, faded at both ends, and pressed down
        # while the narrator speaks, so a line never fights the music.
        args += ["-stream_loop", "-1", "-i", str(music)]
        mix += (
            f",asplit=2[v][key];[{len(clips) + 1}:a]atrim=0:{length:.3f},"
            f"volume={MUSIC_GAIN},afade=t=in:d=1.5,"
            f"afade=t=out:st={max(0.0, length - FADE):.3f}:d={FADE}[bed];"
            "[bed][key]sidechaincompress=threshold=0.02:ratio=6:attack=80:release=900[duck];"
            "[v][duck]amix=inputs=2:normalize=0"
        )
    # Web video plays at about -16 LUFS; the raw mix sits near -28.
    mix += ",loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]"
    args += ["-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "160k", "-shortest", str(out)]  # fmt: skip
    subprocess.run(args, check=True)


def _report(model: DemoModel, server: Server) -> None:
    """Why a beat stalled: the fake's last answers, each session's last rows."""
    for e in model.log[-8:]:
        print("model:", e.get("last_person", "")[-80:].replace("\n", " "), "->",
              e.get("reply"))  # fmt: skip
    for sess in server.app.sessions.sessions.values():
        print(f"session {sess.handle} {sess.status}:")
        for e in sess.entries()[-8:]:
            print("  ", e["kind"], (e.get("title") or e.get("md") or "")[:100])


def _vtt(cues: list[Cue], out: Path) -> None:
    def ts(s: float) -> str:
        return f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"

    body = "".join(
        f"{i}\n{ts(c.t)} --> {ts(c.t + c.length)}\n{c.text}\n\n"
        for i, c in enumerate(cues, 1)
    )
    out.write_text("WEBVTT\n\n" + body)


def main() -> None:
    # A beat's `from director import ...` must get this module, not a second
    # copy whose Shell the model would not recognise.
    sys.modules["director"] = sys.modules[__name__]
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("beat", help="a module in scripts/demo/beats/")
    ap.add_argument("--out", type=Path, default=Path(".demo"))
    ap.add_argument("--audio", type=Path, default=None, help="narration clip cache")
    ap.add_argument("--tts", help="makes a missing clip: {yaml} {out} {text}")
    ap.add_argument(
        "--ffmpeg", default=os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    )
    ap.add_argument("--claude", default=shutil.which("claude"))
    ap.add_argument("--root", default="/tmp/acme-shop", help="the session's directory")
    ap.add_argument(
        "--slow", type=float, default=SLOW, help="world time per video second"
    )
    ap.add_argument("--keep", action="store_true", help="keep the temporary files")
    ap.add_argument(
        "--music",
        type=Path,
        help="a music bed under the narration (default: the beat's MUSIC)",
    )
    a = ap.parse_args()
    assert a.ffmpeg, "no ffmpeg: pass --ffmpeg or set FFMPEG"
    assert a.claude, "no claude on PATH: pass --claude"
    global _ffmpeg
    _ffmpeg = a.ffmpeg
    beat = importlib.import_module(f"beats.{a.beat}")
    a.out.mkdir(parents=True, exist_ok=True)
    audio = a.audio or a.out / "audio"
    audio.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="aegis-demo-"))
    # The client shows the root's full path, so it is a short fixed one. The
    # marker says a directory there is the director's to replace.
    root = Path(a.root)
    if root.exists():
        assert (root / ".aegis-demo").exists(), f"{root} exists and is not a demo root"
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / ".aegis-demo").write_text("")
    (root / ".aegis.yaml").write_text(CONFIG)
    for rel, body in getattr(beat, "FILES", {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)

    model = DemoModel()
    fport, port = _free_port(), _free_port()
    _serve_fake(model, fport)
    # The sessions aegis starts inherit this process's environment.
    env = _harness_env(tmp / "homes", f"http://127.0.0.1:{fport}")
    os.environ.clear()
    os.environ.update(env)
    world = getattr(beat, "WORLD", World())
    (tmp / "pypi.json").write_text(json.dumps({"info": {"version": world.version}}))
    os.environ["AEGIS_RELEASES_URL"] = (tmp / "pypi.json").as_uri()
    server = Server(root, a.claude, port, world)
    server.start()

    from playwright.sync_api import sync_playwright

    raw = a.out / f"{a.beat}.raw.mp4"
    try:
        with sync_playwright() as p:
            # The flag, not device_scale_factor: the screencast sends frames
            # in CSS pixels under the latter.
            browser = p.chromium.launch(args=[f"--force-device-scale-factor={SCALE}"])
            page = browser.new_page(viewport={"width": W, "height": H})
            page.add_init_script(f"window.__demoSlow = {a.slow};")
            page.add_init_script(path=str(Path(__file__).with_name("stage.js")))
            stage = Stage(model, page, root, audio, a.tts, server, a.slow)
            _speak_missing(beat, stage)
            page.goto(server.url)
            page.wait_for_selector("#a2:not([data-view=boot])", timeout=30000)
            if hasattr(beat, "setup"):
                beat.setup(stage)
            cdp = page.context.new_cdp_session(page)
            cdp.send("Animation.enable")
            cdp.send("Animation.setPlaybackRate", {"playbackRate": 1 / a.slow})
            stage.capture = Capture(cdp, a.ffmpeg, raw, a.slow)
            stage.wait(0.3)
            try:
                beat.run(stage)
            except Exception:
                _report(model, server)
                raise
            stage.wait(TAIL)
            length = stage.capture.stop()
            browser.close()
    finally:
        server.stop()
        if not a.keep:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)
        else:
            print(f"kept {tmp}")
    final = a.out / f"{a.beat}.mp4"
    music = a.music or getattr(beat, "MUSIC", None)
    if music and not Path(music).exists():
        print(f"no music at {music}: narration only")
        music = None
    _mux(raw, stage.cues, final, length, music)
    raw.unlink()
    _vtt(stage.cues, a.out / f"{a.beat}.vtt")
    print(f"{final}: {length:.1f} s, {stage.capture.frames} frames captured, "
          f"{len(stage.cues)} lines")  # fmt: skip


if __name__ == "__main__":
    main()
