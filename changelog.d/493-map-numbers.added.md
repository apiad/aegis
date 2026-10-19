- **`aegis map metrics`, `cycles`, `unused` and `diff` give a codebase's
  numbers.** Per function, file and folder: lines, complexity as ruff's C901
  counts it, docs, typed parameters and returns, calls on receivers of
  unknown type, and fan-in; cycles of uses between files or folders;
  definitions nothing names; and, for a branch, each changed definition with
  its sizes before and after and its users. They judge nothing: each takes
  `--json`, and a review or a CI gate is a `jq` filter over it.
help: code-map#numbers-for-a-review
