---
title: Running a server for others
kind: tutorial
when: a tutorial to practise running a server for other people step by step after getting started
---

This tutorial sets up an aegis server that other people use. You need a shell
on the machine and the server's admin token. The tokens, service and config
pages hold the details.

## 1. Give someone a token

Run `aegis token add ana` in a shell, or add a token in Settings. You should
see a new member token printed once. Copy it to the person by a private
channel; `aegis token list` shows tokens by name and role, never their value.

## 2. Set a quota line

In Settings, find the member's token and press Customize: it copies the
default policy into the token. For Claude, choose lines. Untick 5 hours, tick
week, drag its launch handle to 60% and its stop handle to 80%, and press Save
changes. You should see the two lines on the gauge. At or over the launch line the
member's new sessions are refused with the reason; at the stop line its
running turns are interrupted and continued later.

## 3. See what a member sees

Sign in from another browser with the member's token. You should see every
session on the server, your own launch and stop lines on the quota gauges in
the session panel, and, in a session you do not own, a message box that says
you can read it and only its owner can write to it.

## 4. Keep it running

Stop the aegis you started by hand, then run `aegis service install` in the
directory it serves. It asks a few questions, writes a systemd unit and prints
the sudo commands that install and start it; it never runs sudo itself. Run
those commands, then restart the unit with `sudo systemctl restart aegis`. You
should see aegis come back after the restart, with the turns it cut
continued.

## 5. Revoke the token

Run `aegis token revoke ana`. You should see the member's browser lose its
sign-in on its next call.
