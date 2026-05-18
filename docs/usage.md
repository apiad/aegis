# Usage

`aegis` opens a full-screen TUI. Type in the input box and press `Enter`.

## Keys

| Key | Action |
|---|---|
| `Enter` | Send |
| `Ctrl+T` | New tab (default agent) |
| `Ctrl+N` | New tab — pick an agent profile |
| `Ctrl+W` | Close tab (closing the last quits) |
| `Ctrl+1`..`Ctrl+9` | Jump to tab N |
| `Ctrl+Tab` / `Ctrl+→` / `Ctrl+←` | Next / prev tab |
| `Escape` | Interrupt the active turn |
| `Ctrl+Q` | Quit |

## Tabs

Each tab is an independent agent session with a generated handle
(`adjective-laureate`, e.g. `lucid-knuth`) and a state dot: green idle,
amber working, red error. A backgrounded tab that finishes shows a sticky
`*` and rings the terminal bell until you switch to it. The tab bar scrolls
sideways to keep the active tab in view.

## Status line & metrics

`handle ·profile· model · permission   state   ↑<input> (<n>% cached)
↓<output> · ⚒ <tools> · <turn> / <session>`

`↑` is the **true** input the model ingests — uncached input **plus** cache
creation **plus** cache read (often >90% cached). Figures are provisional
(`~`) while a turn streams and exact at turn end.

## Themes

The default **Ink** theme is calm near-black with one amber accent. Themes
are a Textual-native registry; more are drop-in additions.
