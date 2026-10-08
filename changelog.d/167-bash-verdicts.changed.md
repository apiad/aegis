- **Agents end each Bash call on a line of counts, and that line is the row's
  verdict.** The primer now asks for a final line the command computed
  (`&& echo "$n files changed"`), chained with `&&` so a failure keeps its exit
  code. It also warns that `n=$(grep -c …) &&` stops the chain on a count of
  zero, against piping a test run without `pipefail`, and against `pkill -f`,
  which kills the call that runs it. In 124,529 legacy Bash calls,
  4,007 test runs printed failures and still showed green because of a pipe,
  and 180 `pkill -f` calls died with exit code 144.
