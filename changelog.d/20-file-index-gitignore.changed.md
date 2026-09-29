- **The file picker honours `.gitignore`.** Inside a repo, `git ls-files`
  decides what the picker lists, so every `.gitignore`, `info/exclude` and the
  global excludes count exactly as git counts them. A repo inside a gitignored
  directory, such as `repos/<name>` in a workspace that ignores `repos/`, is
  indexed by its own rules. Submodules are included, and worktrees are not.
  On the Workspace the index went from 61,486 paths to 36,414, and the walk
  from 5.9 s to 1.7 s of CPU. Scratch trees under a gitignored `.playground/`
  no longer enter it, and the delete burst behind #18 came from one of those.
