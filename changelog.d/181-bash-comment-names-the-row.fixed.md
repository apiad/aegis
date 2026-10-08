- **A Bash row is named by the comment its command opens with.** Claude Code
  leaves out the optional `description` field for whole sessions: in early
  October, 14 of 81 sessions sent it on none of their Bash calls, and their rows
  showed the raw command. The primer now asks agents to open every Bash command
  with a line like `# Count the open issues`, and the row takes its name from
  that line first, then from `description`, then from the command itself. A
  leading comment does not stop a `Bash(git status:*)` allow rule from matching.
