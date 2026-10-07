- **Agents can hand you files.** The new `file_send` tool copies a file into the
  server's state and puts it in the session's transcript: images, PDFs, HTML
  reports, audio and video preview inline, Markdown renders, text and CSV show
  their first 40 lines, and every file has Open and Download. aegis serves it at
  an unguessable `/files/<id>/<name>` link, so it works the same from a browser
  on the server's machine and from one reaching it remotely. Until now an agent
  could only print a path. HTML, SVG and XML run sandboxed in an opaque origin,
  so a report's script cannot read the token that drives your agents.
