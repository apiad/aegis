- **A Claude session's text streams into the transcript as it is written.**
  aegis did not pass `--include-partial-messages` to `claude`, so a reply landed
  one whole message at a time, while an OpenCode reply already streamed. Claude's
  text and thinking deltas now draw a live row that the finished block replaces,
  and, as with OpenCode, the deltas are never written to the transcript store.
