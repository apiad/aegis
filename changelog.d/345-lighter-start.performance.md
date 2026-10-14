- **The server starts about 7 MB smaller, and a reload into a session asks for
  it sooner.** The Web Push code imported `cryptography` with the server, so
  every server carried its bindings whether or not any device had turned push
  on; it is now imported the first time a message is signed. A reload into a
  session read this device's copy of it only after the Fleet's snapshot had
  arrived, and the subscription waited for that read; the copy is now read
  while the socket connects, so the transcript is asked for about 30 ms sooner.
