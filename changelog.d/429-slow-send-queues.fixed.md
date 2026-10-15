- On a weak connection a message no longer hangs in the box. A plain-text
  send with no answer within 5 seconds becomes the session's queued draft,
  the page reconnects, and the draft goes out by itself, once. Before, the box
  handed the text back after 3 seconds and asked to send it again (#429).
