- **`aegis map` comes from wayfind, a package of its own.** The code map moved
  out of aegis into [wayfind](https://github.com/apiad/wayfind), so the same
  commands run where aegis is not installed, such as a CI job (`uvx wayfind`).
  aegis depends on it and `aegis map` works as before. Its index now lives
  under `~/.cache/wayfind`, so the first command after upgrading re-indexes.
help: code-map
