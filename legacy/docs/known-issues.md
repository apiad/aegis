# Known issues

Problems we have diagnosed and decided not to fix in aegis, because the cause is
outside it. Each entry says how to recognise the problem, why it happens, and
what to do instead.

## Transcript rows duplicate while scrolling in VTE terminals

**Symptom.** Mouse-scrolling the transcript quickly shows one or more rows
twice, with some jitter. The duplicates disappear on the next frame; nothing
stays wrong once scrolling stops.

**Affected terminals.** Every terminal built on VTE: Ptyxis, GNOME Terminal,
GNOME Console, Black Box. Measured on Ptyxis with VTE 0.84.0.

**Cause.** VTE does not implement synchronized output (DEC mode 2026), the
protocol that lets a terminal hold a frame and show it all at once. Asked with
`CSI ? 2026 $ p`, VTE answers `4`, permanently reset. Each scroll step repaints
every visible transcript row, top to bottom, which is 10 to 20 KB per frame and
several pty reads. A terminal that paints after the first read shows the new
rows above that point and the old rows below it, so the rows where they meet
appear twice. Every frame aegis sends is correct: a replay of a captured scroll
found no duplicated row at any frame boundary, and duplicated rows in 163 of
461 states cut mid-frame. The measurements and the scripts are in
[#33](https://github.com/apiad/aegis/issues/33).

**Workaround.** Use a terminal that implements mode 2026. Ghostty (in the
Ubuntu archive since 26.04: `sudo apt install ghostty`), kitty, WezTerm and
foot all do; Ghostty answers the same query with `2`, supported, and scrolls
without duplicated rows. `GSK_RENDERER=cairo`, the fix for flicker after a
display change (see the README's troubleshooting section), does not help here.

**Why it is not fixed in aegis.** No change in aegis can make a VTE frame
atomic. Sending fewer or smaller frames per scroll would make the torn states
rarer without removing them.
