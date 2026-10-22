---
title: Browsers
kind: howto
when: test a web page with the person, see what the user sees in a page, a captcha or a login the person must do, drive a browser on the server
---

A browser is a Chromium that runs on the server, held by aegis like a
terminal, and shown live in a tab. The person sees and clicks the page; an
agent reads and drives the same page through the `browser_*` tools. One of
the two holds the page at a time, and either can pass it to the other: the
agent for a captcha, a sign-in or a step it wants the person to try, the
person when they want the agent to continue.

## Setting it up

Browsers need the `browser` extra and Playwright's Chromium, once per server:

```bash
pip install 'aegis-harness[browser]'   # or: uv tool install 'aegis-harness[browser]'
playwright install chromium
```

Without them the + view shows no browser and `browser_open` answers
browser_unavailable. Browsers do not run in multi-user mode yet.

## Opening one

A person presses + and picks `browser` under Terminals; they hold the page.
An agent calls `browser_open` with a URL; the agent holds it. The browser
docks beside the agent's transcript, as a sent page does (`dock=`, right by
default; `null` leaves it out), and also has a tab and a card in the Fleet.
A browser the person gives to a session docks there too. `layout_set` moves
it like any pane, by its id. The server's own `localhost` is reachable, so a dev server on the
same machine opens with no tunnel.

Each browser has a profile, a directory that keeps cookies and storage, so a
sign-in done once stays. An agent's default profile is its handle; pass
`profile` to share one between sessions. Two running browsers cannot use the
same profile at once (profile_in_use). At most three browsers run at once.

## The tab

The bar has back, forward, reload, the address, the holder (`you` or
`agent`) and the buttons for control. While the person holds the page,
clicks, the wheel and keys typed on the focused page go to it; the page's
viewport takes the size of the pane. While the agent holds it the page only
watches, and the bar shows the agent's last action.

Frames flow only while a tab shows the browser. A browser nobody watches and
nobody uses for 30 minutes stops; its card stays, and Reopen starts it again
at its last address, still signed in. A restart of aegis stops every browser
the same way.

## The agent's tools

A `target` is a Playwright selector: `role=button[name="Send"]`,
`text=Sign in`, `label=Email`, or CSS. Each tool takes `browser`, which
defaults to the session's only browser.

- `browser_snapshot`: the page as an accessibility tree, the roles and names
  to target. Cheaper than an image; start with it.
- `browser_click`, `browser_type` (with `submit` to press Enter),
  `browser_press`: act, then return the address, the title, and what the log
  saw meanwhile (navigations, dialogs, console errors, failed requests).
- `browser_goto`: a URL, or back, forward, reload.
- `browser_screenshot`: saves a PNG and returns its path; read the file to
  see it.
- `browser_eval`: a JavaScript expression's JSON value.
- `browser_resize`: the viewport, such as 390 by 844 for a phone.
- `browser_events`: the log after a revision.
- `browser_close`: stops the browser and removes its tab; the profile stays.

Every tool that changes the page answers person_has_control while the
person holds it. `browser_snapshot`, `browser_screenshot` and
`browser_events` work either way.

## Handing over and back

The agent calls `browser_handover` with what the person should do, then
ends its turn with `turn_end(needs_you)`. The tab shows the request on a
banner with a Give back button. When the person gives the page back, the
agent is woken with `> from browser:<id> · back` and the list of what the
person did.

The person can press Take over at any moment, without being asked; the
agent's next action on the page is refused until they give it back. A
browser the person opened has no agent: Give to… hands it to one of their
open sessions, which is woken with `> from browser:<id> · given`.

## Testing a page with the person

For a smoke test, open the page, tell the person the browser's name and
what to try, and hand it over with `browser_handover`. While they hold it you
can still read it: `browser_snapshot` shows where they are, and
`browser_events` with `who=person` lists what they clicked and typed, and the
console errors and failed requests it caused. When they give it back, the
wake-up holds the same list. Report what broke from the log, not from what
they remember.

## Turning a session into a test

aegis does not record or replay. The log is enough to write a test from: when
a person has clicked through a flow, offer to turn it into a Playwright
script. Read `browser_events` with `who=person` and write a script that
reaches the same states the person reached, not one that repeats their
clicks: target elements by role and name (the log gives both), wait for what
the page shows instead of sleeping, and assert on the outcome they checked by
eye. Skip their detours and typos. Run it against the same page with your
browser, or in the project's test suite, before you hand it over.

## What the log records

Each browser keeps a log in `.aegis/state/browsers/<id>/events.jsonl`, and
each event says who acted, the agent or the person:

- navigations, and popups loaded into the page itself (a browser has one page);
- clicks and typing, with the element's role and accessible name. A password
  field records its length, never its text;
- form submits, console errors and warnings, failed requests and responses
  with status 400 or more;
- alerts, confirms and prompts, which are accepted at once because the tab
  cannot draw them;
- take over, handover and give back.

The agent's own actions are in the log too. The text an agent types appears
in its tool call, as every tool's arguments do; an agent should not type a
person's password, and should hand the page over for a sign-in.
