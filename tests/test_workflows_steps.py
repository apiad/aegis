"""Steps that are not agents against a real server: sh, wait and check, the
quoting of values in a command, and who may arm a command at all."""

import asyncio
import json
import time

import pytest

from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import ok
from .test_workflows_parallel import script

WRITER = (
    "  writer: {harness: claude-code, model: opus, effort: high, permission: write}\n"
)


@pytest.fixture
async def world(tmp_path, fake_claude):
    text = (
        CONFIG.replace("queues:\n", WRITER + "queues:\n")
        + "workflows: {max_agents: 2}\n"
    )
    (tmp_path / ".aegis.yaml").write_text(text)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def flow(*steps: str, args: str = "", extra: str = "") -> str:
    body = "\n".join(f"    - {s}" for s in steps)
    return f"id: steps\ntitle: Steps\nsay: Steps.\n{args}{extra}root:\n  seq:\n{body}\n  say: All of it.\n"


def verdict(yes: bool, wait: float = 0.5) -> str:
    why = "It holds." if yes else "It does not hold."
    return script(
        ["sleep", wait],
        ["workflow_result", {"value": {"pass": yes, "why": why}, "summary": why}],
    )


async def run(
    world, definition: str, args: dict | None = None, agent: str = "opus"
) -> str:
    r = await world.app.registry.call("session.spawn", {"agent": agent})
    main = world.app.sessions.sessions[r["log_id"]]
    armed = ok(
        await turn(main, mcp("workflow_arm", definition=definition, args=args or {}))
    )
    await world.app.registry.call("workflow.approve", {"run": armed["run"]})
    return armed["run"]


async def ended(world, run: str, timeout: float = 30):
    wf = world.app.workflows
    await until(lambda: wf.runs[run].status != "running", timeout=timeout)
    return wf.runs[run]


async def test_an_sh_runs_in_the_runs_cwd_and_its_output_is_a_value(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{sh: "printf hello", id: a, say: Say hello.}',
                '{sh: "test {{a.out}} = hello && pwd > where", say: Check it.}',
            ),
        ),
    )
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["a"].result["code"] == 0
    assert (world.root / "where").read_text().strip() == str(world.root.resolve())
    assert any(s["text"] == "Say hello. exit 0: hello" for s in st.story)


async def test_a_value_with_shell_syntax_stays_one_argument(world):
    args = "args: {type: object, properties: {title: {type: string}}}\n"
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{sh: "echo {{args.title}} > said", say: Write the title.}', args=args
            ),
            {"title": "x; touch pwned $(touch sub) `touch tick` 'q'"},
        ),
    )
    assert st.status == "done"
    said = "x; touch pwned $(touch sub) `touch tick` 'q'\n"
    assert (world.root / "said").read_text() == said
    assert not any((world.root / f).exists() for f in ("pwned", "sub", "tick"))


async def test_a_failing_sh_fails_its_node_with_the_code_and_stderr(world):
    st = await ended(
        world,
        await run(world, flow('{sh: "echo nope >&2; exit 3", id: bad, say: Fail.}')),
    )
    assert st.status == "failed"
    assert st.nodes["bad"].error == "exit 3: nope"


async def test_only_a_full_session_arms_a_command(world):
    r = await world.app.registry.call("session.spawn", {"agent": "writer"})
    caller = Caller("agent", r["log_id"])
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "workflow.arm",
            {"definition": flow('{sh: "true", say: Do it.}'), "args": {}},
            caller,
        )
    assert e.value.code == "not_allowed" and "full" in e.value.message
    probe = flow('{check: {sh: "true"}, say: Check it.}')
    with pytest.raises(OpError):
        await world.app.registry.call(
            "workflow.arm", {"definition": probe, "args": {}}, caller
        )
    fine = flow("{wait: 1s, say: Pause.}")
    await world.app.registry.call(
        "workflow.arm", {"definition": fine, "args": {}}, caller
    )


async def test_a_wait_waits_on_wall_time(world):
    t0 = time.time()
    st = await ended(
        world, await run(world, flow("{wait: 2s, id: pause, say: Pause.}"))
    )
    assert st.status == "done" and st.nodes["pause"].until - t0 >= 2
    assert time.time() - t0 < 10  # woken at its end, not on the 30 s pass


@pytest.mark.slow
async def test_a_check_polls_until_its_probe_passes(world):
    run_id = await run(
        world,
        flow(
            '{check: {sh: "test -f flag", every: 5s}, id: ci, say: Wait for the flag.}'
        ),
    )
    wf = world.app.workflows
    await until(lambda: wf.runs[run_id].nodes.get("ci") is not None, timeout=10)
    assert wf.runs[run_id].nodes["ci"].status == "running"
    (world.root / "flag").write_text("")
    st = await ended(world, run_id, timeout=20)
    assert st.status == "done"


async def test_an_expr_check_passes_on_known_values(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{sh: "printf 7", id: a, say: Print seven.}',
                "{check: {expr: \"a.out == '7' and a.code == 0\"}, say: Check it is seven.}",
            ),
        ),
    )
    assert st.status == "done"


async def test_a_judge_that_says_yes_passes_its_check_and_is_archived(world):
    j = json.dumps(verdict(True))
    st = await ended(
        world,
        await run(
            world,
            flow(
                f"{{check: {{judge: {{agent: opus, prompt: {j}, inputs: []}}}}, id: ok, say: Ask a judge.}}"
            ),
        ),
    )
    assert st.status == "done"
    lid = st.nodes["ok"].judges[""]["log_id"]
    assert lid not in world.app.sessions.sessions  # archived once it judged
    assert any(s["text"] == "Judge: It holds." for s in st.story)


async def test_a_judge_that_says_no_fails_its_check_with_its_reason(world):
    j = json.dumps(verdict(False))
    st = await ended(
        world,
        await run(
            world,
            flow(
                f"{{check: {{judge: {{agent: opus, prompt: {j}}}}}, id: no, say: Ask a judge.}}"
            ),
        ),
    )
    assert st.status == "failed"
    assert st.nodes["no"].error == "the judge said no: It does not hold."


@pytest.mark.slow
async def test_judges_count_in_the_pool(world):
    """max_agents is 2: an agent and two judges side by side never hold
    three sessions open. The agent comes first, so only the judges' own
    room check keeps the third out."""
    j = json.dumps(verdict(True, wait=2))
    a = json.dumps(
        script(["sleep", 2], ["workflow_result", {"value": {}, "summary": "Done."}])
    )
    peak = [0]

    async def watch():
        while True:
            n = sum(1 for s in world.app.sessions.sessions.values() if s.workflow)
            peak[0] = max(peak[0], n)
            await asyncio.sleep(0.05)

    w = asyncio.create_task(watch())
    text = f"""id: three
title: Three
say: Two judges and an agent.
root:
  par:
    - {{agent: {{agent: opus, prompt: {a}}}, say: Work.}}
    - {{check: {{judge: {{agent: opus, prompt: {j}}}}}, say: Judge one.}}
    - {{check: {{judge: {{agent: opus, prompt: {j}}}}}, say: Judge two.}}
  say: All three.
"""
    st = await ended(world, await run(world, text), timeout=40)
    w.cancel()
    assert st.status == "done" and peak[0] == 2


async def test_an_if_takes_one_branch_and_skips_the_other(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{sh: "printf 7", id: a, say: Print seven.}',
                """{if: {when: {expr: "a.out == '7'"},
                 then: {sh: "touch went-then", id: yes, say: Go then.},
                 else: {sh: "touch went-else", id: no, say: Go else.}},
            id: pick, say: Pick a way.}""",
            ),
        ),
    )
    assert st.status == "done" and st.nodes["pick"].took == "then"
    assert (world.root / "went-then").exists()
    assert not (world.root / "went-else").exists()
    rows = {r["id"]: r["state"] for r in world.app.workflows.wire(st.run)["tree"]}
    assert rows["no"] == "skipped"


async def test_an_if_with_no_else_and_a_false_probe_is_done(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{if: {when: {sh: "false"}, then: {sh: "touch never", say: Never.}}, say: Maybe.}',
            ),
        ),
    )
    assert st.status == "done" and not (world.root / "never").exists()


async def test_a_loop_repeats_until_its_probe_passes(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                """{loop: {max: 5, until: {expr: "bump.out == '3'"},
                   do: {sh: "echo x >> n && wc -l < n | tr -d ' \\\\n'", id: bump, say: Count up.}},
            id: count, say: Count to three.}""",
                '{sh: "test {{count.bump.out}} = 3", say: Read the last round.}',
            ),
        ),
    )
    assert st.status == "done", {k: (n.status, n.error) for k, n in st.nodes.items()}
    assert st.nodes["count"].round == 3
    assert sorted(k for k in st.nodes if k.startswith("count.")) == [
        "count.1.bump",
        "count.2.bump",
        "count.3.bump",
    ]


async def test_a_loop_that_runs_out_of_rounds_fails(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{loop: {max: 2, until: {expr: "false"}, do: {wait: 1s, say: Pause.}}, id: never, say: Never done.}',
            ),
        ),
    )
    assert st.status == "failed"
    assert st.nodes["never"].error == "until did not pass in 2 rounds"


async def test_a_loop_without_until_runs_max_rounds(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{loop: {max: 2, do: {sh: "echo x >> twice", say: Once more.}}, say: Twice.}'
            ),
        ),
    )
    assert st.status == "done" and (world.root / "twice").read_text() == "x\nx\n"


async def test_a_leaf_past_its_deadline_fails(world):
    st = await ended(
        world,
        await run(
            world, flow('{sh: "sleep 30", id: slow, deadline: 2s, say: Too slow.}')
        ),
    )
    assert st.status == "failed"
    slow = st.nodes["slow"]
    assert slow.error == "ran past its deadline of 2s"
    # The start asks for a pass at the deadline, not the 30 s heartbeat.
    assert slow.ended - slow.started < 2 + 4


@pytest.mark.slow
async def test_a_group_past_its_deadline_stops_the_agent_inside_it(world):
    slow = json.dumps("/sleep 30")
    text = f"""id: group
title: Group
say: A group with a deadline.
root:
  seq:
    - {{agent: {{agent: opus, prompt: {slow}}}, id: work, say: Work slowly.}}
  deadline: 3s
  say: The group.
"""
    t0 = time.time()
    st = await ended(world, await run(world, text))
    assert st.status == "failed"
    assert st.ended - t0 < 3 + 5
    lid = st.nodes["work"].log_id
    s = world.app.sessions.sessions[lid]  # kept for inspection, with a tab
    await until(lambda: s.status != "working", timeout=10)
    assert not s.quiet


async def test_the_runs_deadline_bounds_everything(world):
    st = await ended(
        world,
        await run(
            world, flow("{wait: 1h, say: Wait an hour.}", extra="deadline: 2s\n")
        ),
    )
    assert st.status == "failed"


async def test_an_sh_retries_and_succeeds(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{sh: "echo x >> tries && test $(wc -l < tries) -ge 2", id: flaky, retries: 2, say: Try again.}'
            ),
        ),
    )
    assert st.status == "done" and st.nodes["flaky"].attempts == 1
    assert any("Retrying, attempt 2 of 3." in s["text"] for s in st.story)


async def test_a_retried_agent_leaves_one_open_session(world):
    first = json.dumps("no result here")  # nudged, then fails: retried
    st_id = await run(
        world,
        flow(
            f"{{agent: {{agent: opus, prompt: {first}}}, id: w, retries: 1, say: Work.}}"
        ),
    )
    st = await ended(world, st_id, timeout=40)
    assert st.status == "failed" and st.nodes["w"].attempts == 1
    open_ = [
        s
        for s in world.app.sessions.sessions.values()
        if (s.workflow or {}).get("node") == "w"
    ]
    assert len(open_) == 1  # the first attempt's session was archived


@pytest.mark.slow
async def test_a_deadline_retry_runs_the_next_attempt_and_ends_it(world):
    from aegis import restarts

    from .test_workflows_restart import lines

    run_id = await run(
        world,
        flow(
            '{sh: "echo x >> tries && sleep 30", id: s, deadline: 2s, retries: 1, say: Slow.}'
        ),
    )
    st = await ended(world, run_id, timeout=20)
    s = st.nodes["s"]
    assert s.attempts == 1 and s.error == "ran past its deadline of 2s", s.error
    assert (world.root / "tries").read_text().count("x") == 2
    pids = [r["pid"] for r in lines(world, run_id) if r.get("ev") == "pid"]
    assert len(pids) == 2
    await until(lambda: all(restarts.started_at(p) is None for p in pids), timeout=5)


@pytest.mark.slow
@pytest.mark.parametrize("kind", ["check", "if"])
async def test_a_stop_ends_a_probe_and_everything_it_started(world, kind):
    marker = world.root / "probed"
    probe = f'{{sh: "sleep 2 && touch {marker} && false", every: 5s}}'
    node = (
        f"{{check: {probe}, id: c, say: Probe.}}"
        if kind == "check"
        else f"{{if: {{when: {probe}, then: {{wait: 1s, say: Then.}}}}, id: c, say: Probe.}}"
    )
    run_id = await run(world, flow(node))
    wf = world.app.workflows
    await until(lambda: (run_id, "c") in wf._tasks, timeout=10)
    await asyncio.sleep(0.5)
    await wf.stop_run(run_id)
    await asyncio.sleep(3)
    assert not marker.exists()


async def test_an_agent_whose_prompt_has_no_value_is_not_retried(world):
    args = "args: {type: object, properties: {x: {type: string}}}\n"
    run_id = await run(
        world,
        flow(
            '{agent: {agent: opus, prompt: "do {{args.x}}"}, id: a, retries: 2, say: Work.}',
            args=args,
        ),
    )
    st = await ended(world, run_id, timeout=20)
    assert st.status == "failed" and st.nodes["a"].attempts == 0
    assert st.nodes["a"].error.startswith("a reference has no value")
    assert not any("Retrying" in s["text"] for s in st.story)


async def test_a_wait_that_can_never_come_is_not_retried(world):
    text = """id: never
title: Never
say: Never.
root:
  par:
    - {map: {over: [{id: a}], as: it, do: {sh: "true", id: one, say: One.}}, id: m, say: Each.}
    - {sh: "true", id: late, after: [m.b], retries: 2, say: Late.}
  say: All.
"""
    st = await ended(world, await run(world, text), timeout=20)
    late = st.nodes["late"]
    assert st.status == "failed" and late.attempts == 0, late.error
    assert "can never come" in late.error and late.say == "Late."


async def test_an_agent_that_cannot_start_is_not_retried(world, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("no harness")

    monkeypatch.setattr(world.app.workflows, "_spawn_node", boom)
    run_id = await run(
        world,
        flow('{agent: {agent: opus, prompt: "go"}, id: a, retries: 2, say: Work.}'),
    )
    st = await ended(world, run_id, timeout=20)
    a = st.nodes["a"]
    assert a.attempts == 0 and a.error == "it did not start: no harness"
    assert a.say == "Work."


async def test_a_false_expr_check_fails_at_once(world):
    st = await ended(
        world,
        await run(
            world,
            flow(
                '{sh: "printf 6", id: a, say: Print six.}',
                "{check: {expr: \"a.out == '7'\"}, id: c, retries: 2, say: Seven.}",
            ),
        ),
        timeout=10,
    )
    c = st.nodes["c"]
    assert st.status == "failed" and c.attempts == 0
    assert c.error == "a.out == '7' is false"


async def test_a_judge_gives_one_verdict_and_no_early_output(world):
    j = json.dumps(script(["sleep", 20]))
    run_id = await run(
        world,
        flow(f"{{check: {{judge: {{agent: opus, prompt: {j}}}}}, id: c, say: Judge.}}"),
    )
    wf = world.app.workflows
    await until(
        lambda: (
            wf.runs[run_id].nodes.get("c") is not None
            and wf.runs[run_id].nodes["c"].judges.get("", {}).get("log_id")
        ),
        timeout=15,
    )
    s = world.app.sessions.sessions[wf.runs[run_id].nodes["c"].judges[""]["log_id"]]
    with pytest.raises(OpError) as e:
        wf.output(s, "early", 1, "An early output.")
    assert e.value.code == "judge"
    yes = {"pass": True, "why": "It holds."}
    assert wf.result(s, yes, "It holds.") == {"accepted": True}
    with pytest.raises(OpError) as e:
        wf.result(s, {"pass": False, "why": "No."}, "No.")
    assert e.value.code == "verdict_given"
    assert wf.runs[run_id].nodes["c"].judges[""]["pass"] is True


@pytest.mark.parametrize(
    "node",
    [
        '{sh: "echo {{args.t}}", id: s, retries: 2, say: Echo.}',
        '{check: {sh: "test -n {{args.t}}"}, id: s, retries: 2, say: Echo.}',
        '{if: {when: {sh: "test -n {{args.t}}"}, then: {wait: 1s, say: T.}}, id: s, say: Echo.}',
    ],
)
async def test_a_nul_byte_in_a_value_fails_its_command(world, node):
    args = "args: {type: object, properties: {t: {type: string}}}\n"
    run_id = await run(world, flow(node, args=args), {"t": "a\x00b"})
    st = await ended(world, run_id, timeout=10)
    s = st.nodes["s"]
    assert st.status == "failed" and s.attempts == 0
    assert s.error == "the command holds a NUL byte, which bash cannot take"


# The member holds no pipe of the leader's, so nothing waits on it.
MEMBER = "(trap '' TERM; exec sleep 30) >/dev/null 2>&1 & echo $! > member && sleep 30"


@pytest.mark.slow
async def test_a_deadline_kills_a_member_that_ignores_sigterm(world):
    from aegis import restarts

    run_id = await run(
        world, flow(f"{{sh: {json.dumps(MEMBER)}, id: s, deadline: 2s, say: Stub.}}")
    )
    st = await ended(world, run_id, timeout=15)
    assert st.status == "failed"
    pid = int((world.root / "member").read_text())
    await until(lambda: restarts.started_at(pid) is None, timeout=5)
