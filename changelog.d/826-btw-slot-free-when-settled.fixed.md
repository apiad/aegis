- **A side note right after a failed one no longer hits `btw_busy`.** The block
  read failed a moment before its slot was freed, so asking again at once could
  be refused with a side note "already running". The slot is now free before
  the block changes.
