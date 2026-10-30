- **A person's launch no longer decides how a concurrent wake is judged.** Who
  started a session was one attribute set around awaits, so an unattended wake
  of the same session waiting on its version check could write the notice as
  if the person had asked (or the other way round). Who launched is now passed
  down the launch path, and each launch is judged as itself.
