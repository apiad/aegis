- **A quota gauge's landing no longer starts low and then jumps.** The projection divided what was spent by the time gone, with a floor of 15% of the window, so a week could read 67% one day and 253% the next. It now keeps what is spent and runs the time left at a rate between the window's own and what your last finished windows usually spent, so a new window reads your usual and the number moves smoothly. A window that usually spends a lot can read near 100% from the start, which a `pace` line sees too.

help: usage#where-a-quota-gauge-says-it-will-land
