---
title: Icons
kind: concept
when: what an icon means, where it is used, or how the icons look in each theme
hints:
  - Every icon aegis draws, what it means and where it shows. #session-states
  - Switch the theme in Settings and watch every icon here follow it. #themes
---

Every icon in aegis comes from one set of symbols, drawn in the colours of
the theme you use. This page lists them all, by what they are for: what each
one means and where it shows. In the Help tab each name is drawn beside its
icon, in the current theme.

## Themes

An icon has no colour of its own: it takes the colour of the text around it,
and the knocked-out mark inside a status badge takes the theme's background.
Change the theme in Settings with this page open and every icon here follows
it. An emoji or a symbol from a font would keep its own colours and change
shape from one device to the next, which is why aegis draws neither.

## Session states

The badge on a tab, a Fleet card and the session panel.

| Icon | Means | Where |
|---|---|---|
| `g-work` | The agent is working | tabs, Fleet cards, session panel, toasts |
| `g-need` | The session needs you: it asked something | tabs, Fleet cards, session panel |
| `g-err` | The session ended its turn on an error | tabs, Fleet cards, session panel, error toasts |
| `g-rev` | The agent left something for you to read | tabs, Fleet cards, session panel |
| `g-wait` | The session waits on a monitor, a queue task or another session | tabs, Fleet cards, the cold-cache note |
| `g-done` | The task is finished | tabs, Fleet cards, session panel |

## Session facts

What a session runs on, beside its words in the session panel, the
composer's chips and the Fleet card.

| Icon | Means | Where |
|---|---|---|
| `g-h-claude-code` | The harness is Claude Code | session panel, model chip, Fleet cards |
| `g-h-opencode` | The harness is OpenCode | session panel, model chip, Fleet cards |
| `g-h-codex` | The harness is Codex | session panel, model chip, Fleet cards |
| `g-h-cursor` | The harness is Cursor | session panel, model chip, Fleet cards |
| `g-e-low` | Effort low: one bar of five | session panel, effort chip |
| `g-e-medium` | Effort medium: two bars of five | session panel, effort chip |
| `g-e-high` | Effort high: three bars of five | session panel, effort chip |
| `g-e-xhigh` | Effort xhigh: four bars of five | session panel, effort chip |
| `g-e-max` | Effort max: five bars of five | session panel, effort chip |
| `g-p-read` | Permission read: reads and plans, changes nothing | session panel, permission chip |
| `g-p-write` | Permission write: edits files, refuses commands that need approval | session panel, permission chip |
| `g-p-auto` | Permission auto: edits, and runs the commands the harness's checks allow | session panel, permission chip |
| `g-p-full` | Permission full: runs anything without asking | session panel, permission chip |

## Session actions

The buttons of the session panel and the menus of a tab or a card.

| Icon | Means | Where |
|---|---|---|
| `g-panel` | Show or hide the session panel | the bar above the transcript, the navigator |
| `g-play` | Continue: send "Continue" to the agent | session panel, tab menu |
| `g-stop` | Stop the turn in progress | the composer's stop button |
| `g-interrupt` | Interrupt a working agent | tab menu |
| `g-sleep` | Sleep: end the process, keep the conversation | session panel, tab menu |
| `g-wake` | Wake an asleep session | tab menu |
| `g-restart` | Restart: a fresh process on the same conversation | session panel, tab menu |
| `g-reopen` | Reopen an archived session in a live tab | the archived session's panel |
| `g-again` | Do it again: reopen, reload | tab menu, a page's reload button |
| `g-archive` | Archive the session | session panel, tab menu |
| `g-quiet` | Quiet: hide the tab until the turn ends | session panel, tab menu, quiet Fleet cards |
| `g-unquiet` | Unquiet: show the tab again | tab menu |
| `g-handoff` | Hand the session off to another agent | tab menu |
| `g-pencil` | Rename, edit and resend; a journal note | menus, the Files view, the journal |
| `g-start` | Start something new: a session, the first message | menus, the navigator, the Files view |

## Composer and navigation

| Icon | Means | Where |
|---|---|---|
| `g-send` | Send the message | the composer, the spawn form |
| `g-clip` | Attach files | the composer, the spawn form |
| `g-mic` | Dictate; a message that was dictated | the composer, the transcript |
| `g-speak` | Read a reply aloud; read new replies aloud as they come | the composer, the agent's replies |
| `g-prompt` | Your message; reply or ask | the transcript's gutter, menus, the journal |
| `g-btw` | An aside the agent answers without stopping | the transcript, menus |
| `g-up` | Previous: message, match | the navigator, find |
| `g-down` | Next: message, match | the navigator, find |
| `g-latest` | Jump to the latest; download | the transcript, a file's menu |
| `g-fold` | Fold the transcript | the navigator |
| `g-sparkle` | A recap of the session | the transcript, the navigator |
| `g-caret-r` | Shut: a folder, a run of steps, a prompt | the transcript, the Files tree, workflows |
| `g-caret-d` | Open: a folder, a run of steps, a prompt | the transcript, the Files tree, workflows, the tab overflow |
| `g-left` | Previous file or page | a file card, the viewer, a browser pane |
| `g-right` | Next file or page | a file card, the viewer, a browser pane |
| `g-arrow` | Go there | menus, toasts from another session |
| `g-close` | Close; an error in the transcript; a failed step | buttons, the transcript, workflows, toasts |
| `g-bell` | Notifications | the top bar |
| `g-gear` | Settings | the top bar |
| `g-clock` | Schedules; a step's deadline | the top bar, workflows |
| `g-search` | Search; a search call | help, the journal, the transcript |

## Transcript

The gutter of each row names what the row is.

| Icon | Means | Where |
|---|---|---|
| `g-dot` | The agent's prose; a tool aegis has no kind for | the transcript, the journal, toasts |
| `g-think` | The agent thinking; a subagent | the transcript |
| `g-book` | A read call; read a page | the transcript, menus |
| `g-terminal` | A shell call; a terminal | the transcript, tabs, Fleet cards, the Files view |
| `g-globe` | A fetch call; a browser; a link with no known host | the transcript, tabs, Fleet cards |
| `g-swap` | A call to aegis itself; move a pane | the transcript, the canvas |
| `g-slash` | A slash command | the transcript |
| `g-pip` | A system line; a note in a workflow's story | the transcript, workflows |
| `g-lock` | A secret sent or asked for | the transcript |
| `g-artifact` | A page the agent made for you | the transcript |
| `g-comment` | Comment; a question in a workflow's story | menus, workflows |
| `g-unread` | An agent message you have not read | the transcript |
| `g-read` | Read; done; nothing left to commit | the transcript, workflows, repos, the journal |
| `g-copy` | Copy | buttons, menus |
| `g-link` | Copy a link | menus |
| `g-expand` | Open a card full size | file, artifact and workflow cards |
| `g-phone` | Show a page at phone width | artifact cards |
| `g-dock` | Dock beside the transcript | cards, menus |
| `g-window` | Open in a new window; a session in the journal | cards, menus, the journal |
| `g-open` | Open in a new tab | cards, menus, the journal |
| `g-arrow-up` | A pull request | the journal |

## Files

| Icon | Means | Where |
|---|---|---|
| `g-file` | A file | the Files tree, the picker, file cards |
| `g-file-gone` | A path that does not exist | the transcript's path chips |
| `g-folder` | A folder, shut | the Files tree, the picker, path chips |
| `g-folder-open` | A folder, open | the Files tree |
| `g-f-md` | A Markdown file | the Files tree, the picker |
| `g-f-code` | A source file | the Files tree, the picker |
| `g-f-config` | A config file | the Files tree, the picker |
| `g-f-image` | An image | the Files tree, the picker |
| `g-f-pdf` | A PDF | the Files tree, the picker |
| `g-f-audio` | An audio file | the Files tree, the picker |
| `g-f-video` | A video | the Files tree, the picker |
| `g-f-archive` | An archive | the Files tree, the picker |
| `g-trash` | Delete | the Files tree's menu |
| `g-branch` | A linked worktree | the session panel's repos |

## Links

A link in the transcript shows the host it goes to.

| Icon | Means | Where |
|---|---|---|
| `g-github` | A GitHub link | the transcript, the session panel's repos |
| `g-pypi` | A PyPI link | the transcript |
| `g-arxiv` | An arXiv link | the transcript |
| `g-npm` | An npm link | the transcript |
