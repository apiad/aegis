- **The client shows which aegis is serving it and the newest release.** The
  top bar carries the running version next to the server name, and the session
  sidebar ends with an "aegis on <host>" section: the release number with
  `✓ current` or `↑ update` against PyPI, or, for a build from git, the short
  commit with a `dev` badge, its ref and the release it is based on. A git build
  reports the last release number from its metadata, so until now two dev
  builds looked the same. The server asks PyPI at most once an hour; offline,
  the latest release reads `unknown` and nothing else changes.
