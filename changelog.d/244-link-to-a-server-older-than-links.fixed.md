- **Linking to a server older than links says why it failed.** `aegis link add`
  against a server from before 2.4.0 printed a bare `HTTP 403`. It now says the
  far server most likely predates links and that both ends need 2.4.0 or newer,
  and the README states the requirement (#244).
