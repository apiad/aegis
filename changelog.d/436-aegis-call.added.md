- **An agent can call its aegis tools from its shell.** `aegis call <tool>
  key=value ...` runs one tool as the session whose shell ran it, with the same
  permissions and result, and `aegis tools` lists them. A loop over many calls
  is one Bash call, and a result piped into `wc`, `jq` or `grep` never enters
  the agent's context: counting the words of 22 help pages cost a Sonnet
  session $0.18 to $0.33 this way, against $1.44 through the tools, which it
  read every page through and then pasted into `wc -w`.

help: commands#tools-from-an-agent-s-shell
