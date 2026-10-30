---
title: Secrets
kind: howto
when: handing the person a password, token, API key or private key, or asking them for one, without it entering the chat, the transcript or a log
hints:
  - An agent can hand you a password through a card that never enters the transcript. #send-a-new-password
  - Give an agent a token without pasting it into the chat. #ask-the-person-for-a-secret
---

A password, a token or a key must never be written where it is kept: a
message, a commit, a log line or a command's output. Two tools move one
between an agent and the person without the transcript ever holding it.
`secret_send` hands the person a secret that only they can read, once.
`secret_request` asks the person for one and writes it to a file.

## Send a new password

Generate it straight into a file, readable only by you, and never print it:

```bash
umask 077 && openssl rand -base64 18 > /tmp/db-pw
```

Then call `secret_send` with the path, a caption saying what it is, and
`delete_source=true` when the file was made only to carry it. The server reads
the file. You get back an id, the size and when it expires, never the content.
The person sees a card with a Reveal button. `ttl="2h"` keeps it revealable for
two hours; the default is 10 minutes, the range 30 seconds to 24 hours.

If the password must also go somewhere else (a service's config), write it there
from the same file in the same command, before you send it, still without
printing it.

## Send a token that already sits in a file

Send the file's path with `delete_source=false`, the default: the file is the token's
home. Do not cat it first to check it: the check puts it in your context.

## What the person sees

The card shows the caption, the size and when it expires. Reveal shows the
value with a Copy button, in that browser tab only. The first reveal burns
it: a reload, another browser, or a linked server's page then shows "Already
viewed". An unrevealed secret shows "expired" after its ttl. A restart of
aegis forgets every unrevealed secret, and its card says to ask you to send it
again: send it again.

## Ask the person for a secret

When you need a secret only the person has, do not ask them to paste it in
chat. Call `secret_request` with a path that does not exist yet, in a directory
that does, and a prompt that says what you need and why. Then end your turn
with `turn_end` and `attention=needs_you`. The person types or pastes it into a masked field on
the card, line breaks and all (a private key works as pasted); the server writes it to the path with mode 0600 and wakes you with
`> from secret:<id> · provided · path=… · N B`. If they do not answer within the
ttl (30 minutes by default), you are woken with `expired` and nothing is
written. A path that exists is refused with exists, so a request never
overwrites a file.

## Use a provided secret by reference

Pass the file, not its contents: `--password-file /tmp/gh-token`,
`curl -H @/tmp/auth-header`, or `GH_TOKEN=$(< /tmp/gh-token) gh auth status`.
Never print it. aegis removes the file when your session closes, unless you
moved it first, so move it where it belongs if it must stay.

## Rotate a credential

Write the new one to a file, put it in place, send it to the person with
`secret_send`, and only then retire the old one, so nobody is locked out while
the card waits.

## What it protects and what it does not

The secret never enters your context, the transcript, the archive or the server
log. A sent secret is stored only as its XOR with a random pad that the server
keeps in memory and forgets on the first reveal, on expiry or on a restart;
the copy on the card is noise from then on. It does not stop an agent from
reading the file; it gives you a route that does not need to. It does not
protect against someone who controls the server before the reveal. The card
shows the secret's length, and a clipboard manager on the person's machine may
keep what they copy.
