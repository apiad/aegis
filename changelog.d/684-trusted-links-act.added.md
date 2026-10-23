- **Agents act across a trusted link as at home.** `queue_enqueue` with `queue@server` enqueues on the other server and the result comes back to the inbox; `task_status`, `task_cancel` and `task_resume` take `task-id@server`; `monitor_sessions` waits on `handle@server`; and `session_close` closes `handle@server` under the same two-step rule as at home.
help: links#trusted-links
