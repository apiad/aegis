- **Ctrl+F finds text anywhere in the open transcript.** The browser's find saw
  only the rows mounted and laid out, and none of a closed row's output, so most
  of a long session could not be searched. With the transcript focused, Ctrl+F
  (Cmd+F on a Mac) opens a find bar that searches every entry: the text the
  page holds, and through a new `transcript.search` operation the tool output,
  arguments, diffs and thinking the page fetches only when a row opens. Matches
  are highlighted; Enter and Shift+Enter step through them, opening the folded
  run and the closed row they are in; the bar shows "n of m" and Esc closes it.
  A second Ctrl+F inside the bar is the browser's own find (#250).
