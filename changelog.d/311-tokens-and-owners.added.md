A server holds many tokens, each a principal with a name and a role, and every
session and queue task has an owner. A member reads every session and changes only
the ones it owns; server settings, links, tokens and spawning on a linked server are
for admins. Admins manage tokens in Settings and with `aegis token add`, `list` and
`revoke`, and a link acts as the token it presents. (#311)
