- **A monitor whose progress is unknown shows a moving bar, not an empty one.**
  With no `progress` command, before its command has printed a reading, or at
  a reading of 0, the bar on the monitor's row slides instead of sitting at 0%,
  which read as stalled. With reduced motion asked for, it pulses instead.
