- **Typing costs the same in a long session and with many tabs open.** Each
  keystroke made the browser hit-test and repaint every transcript row, and
  every working session made it rebuild every tab four times a second. With a
  2,263-entry transcript a key cost 21 ms of main thread and now costs 6.4 ms.
  Going from 10 to 100 open tabs, with 5 working, added 4.3 ms per key and now
  adds 0.5 ms. The transcript mounts its last 200 rows and loads earlier ones as
  you scroll up. Session cards reach the browser merged, at most four times a
  second, and only the changed tab and card are redrawn.
