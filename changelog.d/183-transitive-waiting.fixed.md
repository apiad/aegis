- **A session waits on a child that waits.** A parent whose spawned session
  armed a monitor, enqueued a task or ran a background command showed done as
  soon as the child's turn ended. It now shows waiting until the whole chain
  below it finishes, at any depth.
