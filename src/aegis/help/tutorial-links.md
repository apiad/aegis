---
title: Linked servers
kind: tutorial
when: a tutorial to practise linked servers step by step after getting started
---

This tutorial links a second aegis server to this one and works with its
sessions from here. You need a shell on this machine, a second aegis server
you can reach, and the admin token of this one. The links and handoffs pages
hold the details.

## 1. Add the link

This step reaches another server.

Pipe the far server's token into `aegis link add <name> <url>` from a shell
on this machine, where the name is the one the far server calls itself, or
add it in Settings, under Servers, with its URL and token. Then run `aegis
link list`. You should see the link by name and URL, the far server's
sessions in the tab bar with its name beside each handle, and in the Fleet
under a band that names it.

## 2. Start a session there

This step starts an agent on the other server and spends its quota.

Open a new tab and pick the far server in its server picker, then send a
first message. You should see the session's tab carry the far server's name,
and its card appear in both servers' Fleets.

## 3. Hand it work from an agent here

This step starts an agent here, spends quota and reaches another server.

In a session here, ask "List the sessions with session_list, then use
peer_handoff to ask the one on the other server to list its directory." You
should see the far session receive a message headed from agent: with this
session's handle and this server's name.

## 4. When the link is down

Stop the far server, or block it. You should see its band in the Fleet say
offline since a time and retrying, its cards and tabs dimmed, and the message
box of its sessions say nothing can be sent until it is back. Start it again:
the sessions come back without a restart here.

## 5. Remove the link

Run `aegis link remove <name>`. You should see the far sessions leave the tab
bar and the Fleet. They keep running on their own server.
