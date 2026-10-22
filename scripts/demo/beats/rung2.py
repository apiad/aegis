"""Rung 2: one agent, done right. A plan in the panel, a monitor on a real
build, a page the person answers, a file dropped in the composer."""

from director import Call, Rule, Say, Shell, World

VOICE = "jim"
LINES = [
    "Open a tab, and the agent does more than chat.",
    "It keeps a plan you can see.",
    "It watches your build, instead of polling it.",
    "It sends you designs as pages you can click.",
    "And you can hand it a file, or simply talk to it.",
]
PROMPT = "Add a dark mode to the settings page."
WORLD = World(cpu=14, ram=38, disk=34, claude={"session": 21, "weekly_all": 37})
FILE = "phone.png"

PLAN = [
    "Read the settings page",
    "Add the theme toggle",
    "Run the build",
    "Pick the palette with you",
]


def plan(done: int) -> Call:
    states = ["done"] * done + ["doing"] + ["pending"] * (len(PLAN) - done - 1)
    items = [{"text": t, "state": s} for t, s in zip(PLAN, states[: len(PLAN)])]
    return Call("plan_update", {"items": items})


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


def setup(stage):
    stage.render(PHONE, stage.root / FILE, 300, 220)


def run(stage):
    stage.script(
        Rule(
            PROMPT[:30],
            [
                plan(1),
                Shell(BUILD, desc="Start the build"),
                Call(
                    "monitor_start",
                    {
                        "description": "npm run build",
                        "done": "test -f .build-ok",
                        "progress": "echo $(( ($(date +%s) - $(cat .build-start)) * 100 / 30 ))",
                        "interval_s": 1,
                    },
                ),
                plan(2),
                Say(
                    "The toggle is in. The build is running; I'll pick it up when it finishes."
                ),
            ],
        ),
        Rule(
            "from monitor:",
            [
                plan(3),
                Call("artifact_create", {"title": "Pick a palette for dark mode"}),
                Shell(
                    "cat > {result:path} <<'PAGE'\n" + PAGE + "PAGE",
                    desc="Write the palette page",
                ),
                Call("artifact_send", {"id": "{result:id}"}),
                Say(
                    "The build passed. Which palette do you like? Pick one on the card above."
                ),
            ],
        ),
        Rule(
            "from artifact:",
            [
                plan(4),
                Say("Dark mode is on, with the warm palette."),
            ],
        ),
        Rule(
            "cut off on my phone",
            [
                Shell(f"ls -l {{path:{FILE}}}", desc="Look at the screenshot"),
                Say(
                    "The Save button wrapped below 400 px. I gave the row room to wrap; it fits now."
                ),
            ],
        ),
        titles={PROMPT[:30]: "Dark mode for settings"},
    )

    # The window floats in, tilted, and turns to face the viewer.
    stage.camera(1, rx=16, ry=-24, s=0.72, y=30)
    stage.drift(1)
    stage.title("Rung 2", "One agent, done right")
    stage.wait(0.3)
    stage.camera(2600, rx=4, ry=-6, s=0.94)
    stage.wait(2.2)
    stage.title()
    stage.say(LINES[0])
    stage.click("#tab-add")
    stage.type("#sp-text", PROMPT)
    stage.camera(1600)
    stage.world(cpu=46, claude={"session": 23, "weekly_all": 37})

    stage.until("#s-plan li, #s-plan .item, #s-plan > *", timeout=120)
    stage.say(LINES[1])
    stage.focus("#s-plan", s=1.9, ry=-7, rx=3)
    stage.spotlight("#s-plan")
    c = stage.callout("#s-plan", "the plan")
    stage.wait(3.2)
    stage.uncallout(c)

    stage.until("#s-monitors :text('npm run build')", timeout=60)
    stage.say(LINES[2])
    stage.spotlight("#s-monitors")
    stage.focus("#s-monitors", s=2.2, rx=7, ry=-4)
    c = stage.callout("#s-monitors", "a monitor, not a poll")
    stage.world(cpu=71, claude={"session": 26, "weekly_all": 38})
    stage.wait(3.6)
    stage.uncallout(c)
    stage.spotlight()
    stage.camera(1800, rx=3, ry=5, s=0.96)

    stage.until("iframe", timeout=120)
    stage.wait(0.8)
    stage.say(LINES[3])
    stage.lift("iframe")
    stage.spotlight("iframe")
    stage.focus("iframe", s=1.5, ry=9, rx=2)
    stage.wait(2.6)
    stage.camera(1200, s=1.15, y=-40)
    stage.wait(1.0)
    stage.click_frame("iframe", "#warm")
    stage.lift()
    stage.spotlight()
    stage.wait(1.0)
    stage.camera(1600, rx=-3, ry=-5, s=0.97)
    stage.world(cpu=22, claude={"session": 29, "weekly_all": 39})
    stage.wait(1.6)

    stage.say(LINES[4])
    stage.attach("#attach", stage.root / FILE)
    stage.spotlight("#composer")
    c = stage.callout("#mic", "or dictate")
    stage.wait(1.6)
    stage.uncallout(c)
    stage.type("#input", "The Save button is cut off on my phone.")
    stage.spotlight()
    stage.until("text=it fits now", timeout=90)
    stage.camera(2400, rx=10, ry=16, s=0.8)
    stage.wait(2.6)
