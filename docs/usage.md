# Usage

`aegis` opens a full-screen TUI. Type in the input box and press
`Enter` to send. Each tab is an independent agent session.

## Keys

| Key | Action |
|---|---|
| `Enter` | Send the input |
| `Ctrl+T` | New tab with the default agent profile |
| `Ctrl+N` | New tab — pick an agent profile from a modal |
| `Ctrl+W` | Close the active tab (closing the last quits) |
| `Ctrl+1`..`Ctrl+9` | Jump directly to tab N |
| `Ctrl+Tab` / `Ctrl+→` / `Ctrl+←` | Next / previous tab |
| `Escape` | Interrupt the active turn |
| `Click on a block` | Copy that message / tool result to clipboard |
| `Ctrl+Q` | Quit |

## Tabs

Each tab is an independent agent session with:

- A **generated alliterating handle** (`adjective-laureate` —
  `lucid-knuth`, `wry-hopper`, `brisk-blum`). Handles maximize variety
  within a session: no laureate is reused until the pool is exhausted,
  no adjective is reused until its pool is exhausted, and initial
  letters cycle so one letter never dominates.
- A **state dot**: green idle, amber working, red error.
- A **sticky `*`** when a backgrounded tab finishes — plus a terminal
  bell — so you notice background work completing.
- A **scrolling tab bar** that keeps the active tab in view.

## The transcript

Each agent message, tool call, and tool result is a separate
**block**. Hover any block to see a tooltip; click to copy that block
verbatim to your clipboard — useful for grabbing tool outputs, error
messages, or generated code snippets.

While an agent is working, an inline **spinner + rotating verb + elapsed
timer** appears at the bottom of the transcript:

```
⠹ Crystallizing… (4.7s)
```

The verb rotates every few seconds (Thinking → Pondering →
Crystallizing → Synthesizing → …) so you can see the agent is still
alive even when it's silent.

## Status line & metrics

```
handle ·profile· model · permission   state   ↑<input> (<n>% cached) ↓<output> · ⚒ <tools> · <turn> / <session>
```

- `↑` is the **true** input the model ingests — uncached input **plus**
  cache creation **plus** cache read. On a typical Claude session this
  is often >90% cached.
- `<n>% cached` is the fraction of `↑` that came from cache (not
  re-billed at full rate).
- `↓` is total output tokens this session.
- `⚒` is the count of tool calls this session.
- `<turn>` is the wall-clock time of the most recent turn; `<session>`
  is the total wall-clock since this tab opened.

Numbers are **provisional** (`~` prefix) while a turn is streaming and
**exact** at turn end.

## Themes

The default **Ink** theme is calm near-black with one amber accent.
Themes are a Textual-native registry; more are drop-in additions.

## Interrupting

Press `Escape` to interrupt the active turn. The harness is notified;
the agent stops at the next safe point (after the in-flight tool call,
typically within a second). The TUI returns to idle and you can send
again.

## Headless mode

If you want the routing plane (sessions, queues, MCP) without the TUI,
run `aegis serve`. See [Configuration](configuration.md#headless-telegram)
for the Telegram bridge.
