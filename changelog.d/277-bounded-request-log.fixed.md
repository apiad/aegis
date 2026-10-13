- **A large request no longer logs its whole params.** `aegis serve` logged
  every call's params at info level in full, so one artifact submit near the
  64 KB cap wrote a 65 KB line; where stdout was a pipe nobody drained, that
  line could block the server on its own log. Each field is now cut at 200
  characters, with its total size.
