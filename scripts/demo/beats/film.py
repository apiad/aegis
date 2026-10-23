"""The landing's film: one continuous take up the six rungs.

One server, one browser, one capture. Each beat adds its rules to the fake
and moves the camera on from where the last one left it, so nothing is cut.
The lines grew from the spec's draft (docs/superpowers/specs/
2026-10-22-landing-and-video-design.html, section 05) into a longer cut that
says what each rung does, not only that it exists (#669). The film opens and
closes on the logo card, over the music bed in ../music/. Every release reads
this script against its changelog (know-how/releasing.md).
"""

from pathlib import Path

from director import Call, Rule, Say, Shell, World

VOICE = "jim"
MUSIC = Path(__file__).resolve().parents[1] / "music" / "film.mp3"
ROOT = "/tmp/acme-shop"
WORLD = World(
    cpu=14, ram=38, disk=34,
    claude={"session": 21, "weekly_all": 37},
    opencode={"rolling": 12, "weekly": 30},
)  # fmt: skip

L = {
    "open": [
        "You already pay for an AI coding agent.",
        "aegis makes it better today, and grows with you when the work does.",
        "It runs on your own machine and opens in your browser. It is free, and open source.",
    ],
    "r1": [
        "Bring the subscriptions you have: Claude Code, Codex, OpenCode, Cursor.",
        "Mix them however you like, in one interface, with every quota and its pace in view.",
        "So you always know how much of the week is left, and whether you are spending it too fast.",
    ],
    "r2": [
        "Open a tab, and the agent does more than chat.",
        "It keeps a plan you can see, so you know where it is and what comes next.",
        "It watches your build, instead of polling it.",
        "It goes quiet while the build runs, and wakes the moment it ends, without spending a token on the wait.",
        "It sends you designs as pages you can click.",
        "You make the choice on the page, and your answer goes straight back to the agent.",
        "And you can hand it a file, or simply talk to it.",
    ],
    "r3": [
        "Open more tabs, one per project.",
        "The Fleet shows every session on one screen: what each one is doing, and which one is waiting on you.",
        "Each one tells you when it needs you, and you answer with a tap.",
    ],
    "r4": [
        "Ask them to work together, and they agree among themselves,",
        "with handoffs, and queues of workers.",
        "Here one agent sends three reviewers off in parallel, and hands the API change to the session that owns it.",
        "Each result comes back to it as a message, so nobody has to watch.",
    ],
    "r5": [
        "As the problem grows, so does the team:",
        "workflows you approve with one click, schedules, and reviewers checking implementers.",
        "A workflow is a plan the server runs for you: one agent builds, another reviews, then the tests run.",
        "A schedule wakes an agent on a clock, like a backup every night.",
        "A software company, on demand.",
    ],
    "r6": [
        "Link servers together, share a machine with your team,",
        "and keep working from your phone.",
        "Every view fits a phone, so an agent that needs you can reach you anywhere.",
    ],
    "close": [
        "aegis. Start with what you have. Grow as far as the work needs.",
        "Install it with one command, and open it in your browser.",
    ],
}
LINES = [line for beat in L.values() for line in beat]


def rung(stage, n: int, kicker: str, head: str) -> None:
    """A rung's title over the live window, which keeps moving behind it."""
    stage.title(f"Rung {n} · {kicker}", head)
    stage.wait(2.0)
    stage.title()


# -- the opening ---------------------------------------------------------------


def opening(stage):
    stage.camera(1, rx=18, ry=-26, s=0.7, y=40)
    stage.drift(1)
    stage.logo("A workplace for the coding agents you already pay for")
    stage.wait(2.4)
    stage.say(L["open"][0], wait=True)
    stage.logo(None)
    stage.camera(3200, rx=5, ry=-8, s=0.92)
    stage.say(L["open"][1], wait=True)
    stage.say(L["open"][2], wait=True)


# -- rung 1: the agents you already pay for ------------------------------------


def rung1(stage):
    rung(stage, 1, "today", "The agents you already pay for")
    stage.say(L["r1"][0])
    stage.click("#tab-add")
    stage.wait(0.6)
    stage.focus("#sp-harness", s=2.0, ry=-6)
    stage.click("#sp-harness")
    stage.spotlight("pick-chip#sp-harness")
    stage.wait(3.2)
    stage.key("Escape")
    stage.spotlight()
    stage.camera(1400, rx=3, ry=6, s=0.95)
    stage.click("#tab-fleet")
    stage.say(L["r1"][1])
    stage.wait(0.6)
    stage.focus("#band-quota-col", s=1.9, rx=6)
    stage.spotlight("#band-quota-col")
    c = stage.callout("#band-quota-col", "every quota, and its pace")
    stage.wait(3.4)
    stage.say(L["r1"][2], wait=True)
    stage.uncallout(c)
    stage.spotlight()


# -- rung 2: one agent, done right ---------------------------------------------

PROMPT = "Add a dark mode to the settings page."
FILE = "phone.png"
PLAN = [
    "Read the settings page",
    "Add the theme toggle",
    "Run the build",
    "Pick the palette with you",
]


def plan(done: int) -> Call:
    states = ["done"] * done + ["doing"] + ["pending"] * len(PLAN)
    return Call(
        "plan_update",
        {"items": [{"text": t, "state": s} for t, s in zip(PLAN, states)]},
    )


PAGE = """<!doctype html>
<html lang="en"><meta charset="utf-8"><title>Pick a palette</title>
<link rel="stylesheet" href="/static/css/artifact.css">
<script src="/static/js/artifact.js"></script>
<style>
 .row{display:flex;gap:14px;margin-top:10px}
 .sw{flex:1;border-radius:12px;padding:14px;cursor:pointer;border:2px solid transparent}
 .sw:hover{border-color:var(--accent,#e0a458)}
 .sw b{display:block;margin-bottom:10px}
 .chips{display:flex;gap:6px}.chips i{width:26px;height:26px;border-radius:6px;display:block}
</style>
<body><h2>Pick a palette for dark mode</h2>
<p>Three candidates, each with its background, surface, text and accent.</p>
<div class="row">
 <div class="sw" id="warm" style="background:#17130f;color:#f1e6d6"><b>Warm</b>
  <div class="chips"><i style="background:#17130f"></i><i style="background:#2a221a"></i><i style="background:#f1e6d6"></i><i style="background:#e0a458"></i></div></div>
 <div class="sw" id="slate" style="background:#0f1419;color:#dfe7ef"><b>Slate</b>
  <div class="chips"><i style="background:#0f1419"></i><i style="background:#1c2630"></i><i style="background:#dfe7ef"></i><i style="background:#5aa9e6"></i></div></div>
 <div class="sw" id="moss" style="background:#10140f;color:#e3ecd9"><b>Moss</b>
  <div class="chips"><i style="background:#10140f"></i><i style="background:#1e261b"></i><i style="background:#e3ecd9"></i><i style="background:#8bbf6a"></i></div></div>
</div>
<script>
 aegis.ready(() => {
  for (const el of document.querySelectorAll('.sw'))
   el.onclick = () => aegis.submit({palette: el.id}, 'Palette: ' + el.id);
 });
</script></body></html>
"""

# The person's screenshot: a settings row whose button runs off the edge.
PHONE = """<body style="margin:0;background:#17130f;font:15px system-ui;color:#f1e6d6">
<div style="padding:18px"><div style="font-weight:600;margin-bottom:14px">Settings</div>
<div style="display:flex;gap:10px;align-items:center;white-space:nowrap">
<span>Theme</span><span style="padding:6px 10px;border:1px solid #5a4a38;border-radius:6px">Dark</span>
<span style="padding:6px 10px;border:1px solid #5a4a38;border-radius:6px">Follow system</span>
<span style="padding:6px 14px;background:#e0a458;color:#17130f;border-radius:6px;font-weight:600">Save changes</span>
</div></div></body>"""

BUILD = (
    "date +%s > .build-start && "
    "(nohup sh -c 'sleep 30; echo ok > .build-ok' >/dev/null 2>&1 &)"
)


def rung2(stage):
    stage.script(
        Rule(PROMPT[:30], [
            plan(1),
            Shell(BUILD, desc="Start the build"),
            Call("monitor_start", {
                "description": "npm run build",
                "done": "test -f .build-ok",
                "progress": "echo $(( ($(date +%s) - $(cat .build-start)) * 100 / 30 ))",
                "interval_s": 1,
            }),
            plan(2),
            Say("The toggle is in. The build is running; I'll pick it up when it finishes."),
        ]),
        Rule("from monitor:", [
            plan(3),
            Call("artifact_create", {"title": "Pick a palette for dark mode"}),
            Shell("cat > {result:path} <<'PAGE'\n" + PAGE + "PAGE", desc="Write the palette page"),
            Call("artifact_send", {"id": "{result:id}"}),
            Say("The build passed. Which palette do you like? Pick one on the card above."),
        ]),
        Rule("from artifact:", [
            plan(4),
            Say("Dark mode is on, with the warm palette."),
        ]),
        Rule("cut off on my phone", [
            Shell(f"ls -l {{path:{FILE}}}", desc="Look at the screenshot"),
            Say("The Save button wrapped below 400 px. I gave the row room to wrap; it fits now."),
        ]),
        titles={PROMPT[:30]: "Dark mode for settings"},
    )  # fmt: skip
    rung(stage, 2, "one agent", "An agent that does more than chat")
    stage.say(L["r2"][0])
    stage.camera(1600, rx=2, ry=-4, s=0.97)
    stage.click("#tab-add")
    stage.type("#sp-text", PROMPT)
    stage.camera(1600)
    stage.world(cpu=46, claude={"session": 23, "weekly_all": 37})

    stage.until("#s-plan li, #s-plan .item, #s-plan > *", timeout=120)
    stage.say(L["r2"][1])
    stage.focus("#s-plan", s=1.9, ry=-7, rx=3)
    stage.spotlight("#s-plan")
    c = stage.callout("#s-plan", "the plan")
    stage.wait(3.2)
    stage.uncallout(c)

    stage.until("#s-monitors :text('npm run build')", timeout=60)
    stage.say(L["r2"][2], wait=True)
    stage.spotlight("#s-monitors")
    stage.focus("#s-monitors", s=2.2, rx=7, ry=-4)
    c = stage.callout("#s-monitors", "a monitor, not a poll")
    stage.world(cpu=71, claude={"session": 26, "weekly_all": 38})
    stage.say(L["r2"][3], wait=True)
    stage.uncallout(c)
    stage.spotlight()
    stage.camera(1800, rx=3, ry=5, s=0.96)

    stage.until("iframe", timeout=120)
    stage.wait(0.8)
    stage.say(L["r2"][4])
    stage.lift("iframe")
    stage.spotlight("iframe")
    stage.focus("iframe", s=1.5, ry=9, rx=2)
    stage.wait(2.6)
    stage.say(L["r2"][5], wait=True)
    stage.camera(1200, s=1.15, y=-40)
    stage.wait(1.0)
    stage.click_frame("iframe", "#warm")
    stage.lift()
    stage.spotlight()
    stage.wait(1.0)
    stage.camera(1600, rx=-3, ry=-5, s=0.97)
    stage.world(cpu=22, claude={"session": 29, "weekly_all": 39})
    stage.wait(1.6)

    stage.say(L["r2"][6])
    stage.attach("#attach", stage.root / FILE)
    stage.spotlight("#composer")
    c = stage.callout("#mic", "or dictate")
    stage.wait(1.6)
    stage.uncallout(c)
    stage.type("#input", "The Save button is cut off on my phone.")
    stage.spotlight()
    stage.until("text=it fits now", timeout=90)
    stage.wait(1.2)


# -- rung 3: many projects -----------------------------------------------------

API = "Prepare the orders migration."


def rung3(stage):
    stage.script(
        Rule(API[:24], [
            Call("plan_update", {"items": [
                {"text": "Write the orders migration", "state": "done"},
                {"text": "Dry-run it on a copy", "state": "done"},
                {"text": "Run it on production", "state": "doing"}]}),
            Call("turn_end", {"attention": "needs_you",
                              "line": "Run the migration now, or after tonight's backup?",
                              "replies": ["run it now", "after the backup"]}),
            Say("The migration is ready and the dry run is clean. Run it now, or after tonight's backup?"),
        ]),
        Rule("Profile the slow checkout", [
            Call("plan_update", {"items": [
                {"text": "Profile the checkout", "state": "doing"},
                {"text": "Fix the slowest query", "state": "pending"}]}),
            Say("The checkout spends 800 ms in one query; I'm on it.", delay=1),
        ]),
        Rule("Update the docs for", [
            Call("plan_update", {"items": [
                {"text": "Document the theme setting", "state": "doing"}]}),
            Say("Documenting the theme setting.", delay=1),
        ]),
        Rule("after the backup", [
            Say("I'll run it after the backup, at 02:00, and tell you how it went."),
        ]),
        titles={API[:24]: "Orders migration", "Profile the slow checkout": "Slow checkout",
                "Update the docs for": "Docs for dark mode"},
    )  # fmt: skip
    rung(stage, 3, "many projects", "Several projects at once")
    stage.say(L["r3"][0])
    s = stage.vars
    # Started from the dark mode tab, the three join its group: one stack.
    dark = stage.page.locator(".tab:has-text('Dark mode')").first.get_attribute(
        "data-id"
    )
    s["api"] = stage.spawn(f"{ROOT}/api", API, parent=dark)
    stage.wait(0.8)
    s["web"] = stage.spawn(f"{ROOT}/web", "Profile the slow checkout.", parent=dark)
    stage.wait(0.8)
    s["docs"] = stage.spawn(
        f"{ROOT}/docs", "Update the docs for dark mode.", parent=dark
    )
    stage.wait(1.2)
    stage.camera(1600, rx=10, ry=-12, s=0.85)
    stage.click("#tab-fleet")
    stage.wait(1.0)
    stack = "#cards .stack.need"
    stage.until(f"{stack} .alert", timeout=120)
    stage.say(L["r3"][1], wait=True)
    stage.say(L["r3"][2])
    stage.camera(1400, rx=4, ry=-4, s=1.0)
    stage.spotlight(stack)
    stage.lift(stack)
    c = stage.callout(f"{stack} .alert", "one stack, and who needs you")
    stage.wait(2.6)
    stage.uncallout(c)
    stage.lift()
    stage.spotlight()
    stage.click(f"{stack} .card")
    card = f'#cards .gopen .card[data-id="{s["api"]["log_id"]}"]'
    stage.until(card, timeout=30)
    stage.camera(1200, rx=6, ry=-8, s=0.9)
    stage.spotlight(card)
    stage.wait(1.6)
    stage.spotlight()
    stage.click(card)
    stage.wait(1.0)
    stage.until("#replies-pills .rp", timeout=30)
    stage.focus("#replies", s=1.8, rx=-4)
    stage.spotlight("#replies")
    stage.wait(1.4)
    stage.click("#replies-pills .rp >> nth=1")
    stage.spotlight()
    stage.camera(1400, rx=2, ry=4, s=0.97)
    stage.wait(1.6)


# -- rung 4: together ----------------------------------------------------------

TEAM = "Get three reviews of the dark mode PR, and tell the api session about the theme field."
REVIEWS = {
    "for accessibility": "Contrast passes AA, except the disabled buttons.",
    "for performance": "No layout shift; the theme switch costs one repaint.",
    "for security": "Nothing to flag: the theme is a plain enum.",
}


def rung4(stage):
    api = stage.vars["api"]["handle"]
    stage.script(
        Rule(TEAM[:30], [
            *[Call("queue_enqueue", {"queue": "reviews",
                                     "payload": f"Review the dark mode PR {k}. Reply in one line."})
              for k in REVIEWS],
            Call("peer_handoff", {"target": api,
                                  "context": "The settings API needs a theme field: dark, light or system."}),
            Say("Three reviewers are on it, and the api session has the theme field."),
        ]),
        *[Rule(k, [Say(v, delay=2)]) for k, v in REVIEWS.items()],
        Rule("needs a theme field", [
            Call("plan_update", {"items": [
                {"text": "Add theme to the settings API", "state": "done"}]}),
            Say("Added theme to the settings API, with a migration."),
        ]),
        Rule("from queue:", [Say("A review is in.")]),
    )  # fmt: skip
    rung(stage, 4, "together", "Agents that work together")
    stage.say(L["r4"][0])
    stage.click(".tab:has-text('Dark mode')")
    stage.wait(0.6)
    stage.type("#input", TEAM, cps=40)
    stage.say(L["r4"][1])
    stage.until("text=Three reviewers are on it", timeout=120)
    stage.camera(1600, rx=12, ry=10, s=0.84)
    stage.click("#tab-fleet")
    stage.say(L["r4"][2], wait=True)
    stage.camera(1600, rx=3, ry=-3, s=0.98)
    stage.say(L["r4"][3], wait=True)


# -- rung 5: a company ---------------------------------------------------------

LAUNCH = (
    "Ship v2 of the settings page as a workflow, and back up the database every night."
)
WORKFLOW = """\
id: ship-v2
title: Ship v2 of the settings page
say: One agent builds v2, a second reviews it, then the tests run.
root:
  seq:
    - agent: {agent: sonnet, prompt: "Build v2 of the settings page in a worktree and open a PR."}
      id: build
      say: Build v2 and open a PR.
    - agent: {agent: opus, prompt: "Review the v2 settings PR, harshly."}
      id: review
      say: A second agent reviews the PR.
    - sh: "sleep 3"
      id: tests
      say: Run the tests.
  id: all
  say: Build, review, test.
"""


def rung5(stage):
    stage.script(
        Rule(LAUNCH[:30], [
            Call("workflow_arm", {"definition": WORKFLOW, "dock": "right"}),
            Call("schedule_create", {"name": "nightly-backup", "clock": {"cron": "0 2 * * *"},
                                     "fire": {"target": "new"},
                                     "prompt": "Back up the database and report the size."}),
            Say("The workflow is beside us: approve it to start. The backup runs every night at 02:00."),
        ]),
        Rule("Build v2 of the settings page", [
            Call("workflow_result", {"value": "PR #42",
                                     "summary": "PR #42 is open: v2 of the settings page, with tests."}),
            Say("PR #42 is open.", delay=1)]),
        Rule("Review the v2 settings PR", [
            Call("workflow_result", {"value": "approved",
                                     "summary": "Approved, after two small fixes, both pushed."}),
            Say("Approved.", delay=1)]),
        Rule("from workflow:", [Say("v2 is built, reviewed and tested.")]),
    )  # fmt: skip
    rung(stage, 5, "a company", "A software company on demand")
    stage.say(L["r5"][0])
    stage.click(".tab:has-text('Dark mode')")
    stage.wait(0.4)
    stage.type("#input", LAUNCH, cps=40)
    stage.until("button:has-text('Approve and run')", timeout=120)
    approve = stage.mark("button:has-text('Approve and run')", "approve")
    stage.say(L["r5"][1])
    stage.focus(approve, s=1.5, ry=-8)
    stage.spotlight(approve)
    stage.wait(1.6)
    stage.click(approve)
    stage.spotlight()
    stage.camera(1600, rx=3, ry=-6, s=0.96)
    stage.world(cpu=63, claude={"session": 34, "weekly_all": 41})
    stage.say(L["r5"][2], wait=True)
    c = stage.callout("#schedules-btn", "every night at 02:00")
    stage.say(L["r5"][3], wait=True)
    stage.uncallout(c)
    stage.say(L["r5"][4])
    stage.camera(2400, rx=14, ry=-18, s=0.82)
    stage.wait(3.0)


# -- rung 6: beyond ------------------------------------------------------------


def rung6(stage):
    rung(stage, 6, "beyond", "Beyond one machine and one person")
    stage.say(L["r6"][0])
    stage.click("#tab-fleet")
    stage.camera(1800, rx=6, ry=14, s=0.78, x=-180)
    stage.lower(
        "Linked servers · a team on one server",
        "aegis link add lab https://lab.example",
    )
    stage.wait(2.4)
    stage.say(L["r6"][1])
    stage.lower()
    stage.phone(True)
    stage.wait(2.0)
    stage.say(L["r6"][2], wait=True)


# -- the close -----------------------------------------------------------------

LADDER = [
    ("1", "today", "The agents you already pay for"),
    ("2", "one agent", "An agent that does more than chat"),
    ("3", "many projects", "Several projects at once"),
    ("4", "together", "Agents that work together"),
    ("5", "a company", "A software company on demand"),
    ("6", "beyond", "Beyond one machine and one person"),
]


def close(stage):
    stage.phone(False)
    stage.camera(2600, rx=20, ry=-20, s=0.6, y=40)
    rows = "".join(
        f'<div class="d-rung" style="transition-delay:{i * 0.12:.2f}s">'
        f'<b>{n}</b><span>{k}</span>{t}</div>'
        for i, (n, k, t) in enumerate(LADDER)
    )  # fmt: skip
    stage.overlay(
        "<style>.d-ladder{position:absolute;inset:0;display:grid;place-items:center;"
        "background:rgba(11,13,16,.78)}.d-ladder .in{width:640px;margin-top:-110px}"
        ".d-rung{display:grid;grid-template-columns:44px 150px 1fr;align-items:baseline;"
        "padding:10px 0;border-bottom:1px solid #2a2622;font-size:24px}"
        ".d-rung b{font:600 30px DemoSerif,serif;color:#e0a458}"
        ".d-rung span{font-size:14px;letter-spacing:.14em;text-transform:uppercase;color:#9b958b}"
        "</style>"
        f'<div class="d-ladder"><div class="in">{rows}</div></div>'
    )
    stage.wait(3.2)
    stage.say(L["close"][0], wait=True)
    stage.overlay()
    stage.logo("<code>uv tool install aegis-harness</code>")
    stage.wait(1.2)
    stage.say(L["close"][1], wait=True)
    stage.wait(3.0)


def setup(stage):
    stage.render(PHONE, stage.root / FILE, 300, 220)


def run(stage):
    for beat in (opening, rung1, rung2, rung3, rung4, rung5, rung6, close):
        beat(stage)
