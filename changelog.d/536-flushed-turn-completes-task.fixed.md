- **A queue task completes when its worker's held-message turn ends fast.**
  When the turn that delivers held messages ended before the send that
  started it returned, the worker sat idle and its task stayed `running`
  forever; a schedule's run or a workflow step waiting on it could hang the
  same way. The end of the flush now checks again.
