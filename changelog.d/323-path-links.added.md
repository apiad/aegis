- **A file path in an agent's reply opens the file.** Click
  `src/app.py:142` in a reply and the viewer opens that file scrolled to line
  142, with the line marked. Agents are now asked to quote files relative to
  the folder aegis serves from, so the link finds them; a path that is not
  there says where it looked, and one that names two files, in that folder
  and in the session's own, asks which to open.
