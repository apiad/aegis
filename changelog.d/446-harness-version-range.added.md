- **Each release names the harness versions it was tested with.** aegis now
  ships a min and a max version per harness. A person who starts a session on
  a version outside them is asked first (start once, always allow, cancel); a
  session an agent, queue, schedule or workflow starts runs anyway with a
  notice in its transcript; `aegis serve` and `aegis doctor` warn. Every
  message names the binary found and the command that installs a tested
  version. `harness_versions:` in `.aegis.yaml` turns each part off, and the
  OpenCode 2 check in `aegis doctor` is now this general one.

help: sessions#an-untested-harness-version
