- **A secret pasted into a `secret_request` card keeps its line breaks.** The
  field was a one-line password input, which deleted them, so a private key,
  a PEM certificate or a `.env` arrived as one unusable line. It is now a
  masked multi-line field: Enter adds a line, Ctrl+Enter or Provide sends.
