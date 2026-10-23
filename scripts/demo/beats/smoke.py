"""The plumbing: a session starts, plans, answers; the camera moves once."""

from director import Call, Rule, Say

LINES = []


def run(stage):
    stage.script(
        Rule(
            "tidy the readme",
            [
                Call(
                    "plan_update",
                    {
                        "items": [
                            {"text": "Read the README", "state": "done"},
                            {"text": "Tighten the install section", "state": "doing"},
                            {"text": "Open a PR", "state": "pending"},
                        ]
                    },
                ),
                Say("The install section now fits on one screen."),
            ],
        ),
        titles={"tidy the readme": "Tidy the README"},
    )
    stage.title("Rung 2", "One agent, done right", "A plan you can see")
    stage.wait(2)
    stage.title()
    stage.click("#tab-add")
    stage.type("#sp-text", "Please tidy the readme's install section.")
    stage.say("The agent keeps a plan you can see.")
    stage.camera(1500, rx=8, ry=-10, s=0.9)
    stage.wait(6)
    stage.camera(1200)
    stage.wait(2)
