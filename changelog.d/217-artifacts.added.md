- **Agents hand the person an interactive page and hear the answer.**
  `artifact_create` writes a working skeleton, the agent edits it,
  `artifact_send` runs it hidden in the browser and lands it in the transcript
  only when it starts. A click, a submit or a script error reaches the agent
  as an inbox turn; state the page keeps is read with `artifact_read`; the
  agent pushes new state or a new page with `artifact_update`. Until now an
  agent could only send a static file and ask in prose.
