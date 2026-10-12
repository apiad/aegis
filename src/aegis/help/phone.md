---
title: On a phone: install, offline, push
kind: howto
when: installing on a phone home screen, opening with no connection, messages written offline, push notifications, docked panes as tabs on a phone
---

The client runs in a browser, and on a phone it can live on the home screen
like an app: it opens without a connection, keeps messages written offline
until the connection returns, and receives push notifications while it is
closed. All of it needs the server reached over https, or on localhost; over
plain http the page works as before, without any of this. The last section
covers a session's docked pages and files on a small screen.

## Install it as an app

When the browser judges the app installable, the Fleet shows a card at the
top offering to install it. Install opens a dialog with three rows. The first
is the download of this version of the client to the device, file by file,
with a bar that fills as they arrive. The second opens the browser's own
confirmation; on Android, Chrome then puts it on the home screen, which takes
a few seconds and reports no progress. The third turns notifications on. Not
now hides the card for 30 days. Settings, in its This browser section, offers
the same Install button while the browser allows it, and shows the version
this device keeps and its size. A browser that never offers it can still
install it from its own menu.

When a new version is served, the device downloads it in the background and a
toast offers Reload. Every open tab moves to the new version together.

## Without a connection

The app opens with no network from the copy on the device. A banner says the
connection is down, when this device last saw the server, and when it tries
again; Retry now tries at once, and so does the app coming back to the front
or the network returning. The Fleet shows the sessions as this device last saw
them, and the twenty transcripts opened most recently read as they were. A
session never opened on this device says so. A tool row's detail needs the
connection.

Anything that needs the server is disabled offline: starting a session,
archiving or closing one, attachments, dictation and saving Settings. When the
connection returns, everything turns live without a reload. A browser whose
sign-in stops working forgets everything it kept.

## Messages written offline

A message written with no connection becomes the session's draft, shown after
the transcript's last row as queued. Writing again before the connection
returns extends the same draft. Edit puts the text back in the message box and
Discard drops it. The draft is kept on the device, so a reload does not lose
it.

When the connection returns, each draft goes out by itself if nothing a person
reads has happened in its session since it was written. If the agent wrote, or
someone else answered, the draft stays and says the session moved on, with
Send anyway and Discard. A draft for a closed session offers Copy and Discard,
and one the server refused for another reason shows the reason. Only plain
text waits like this: a slash command or a message with files needs the
connection.

A connection can also stall without dropping, which a phone on a weak signal
does often. While a message is on its way the box is locked and the send
button spins. If the server has not answered within 3 seconds, the box unlocks
with the text still in it and says the send was not confirmed. Sending it again
is safe: the server recognizes the same message and delivers it once, and if
the first attempt arrives late the box clears by itself.

## Push notifications

The bell turns push on for this device and chooses what it hears about: a
session that needs you, an error, something to read, finished work, and an
agent's toast. A new device starts with needs you, errors and toasts. Send a
test checks that a push reaches it. A push arrives with the app closed, and
tapping it opens the session; a session already open in front of you on that
device stays quiet. Settings lists every device that receives push for your
token, with a Remove button for each, so a lost phone can be cut off from
another device.

Push is built for Chrome, on Android and on the desktop, and for the other
desktop browsers. Safari on an iPhone supports push for apps on the home
screen, but this has not been tried there.

## For whoever runs the server

The server keeps its push key and the devices' subscriptions under the state
directory, in `push/`, readable by its user only: an endpoint and its keys are
enough to send to that device. Each token can register ten devices. Pushes go
only to the known push services (Google's, Mozilla's, Apple's and
Microsoft's); a self-hosted one is allowed with an environment variable naming
its hosts, such as `AEGIS_PUSH_HOSTS=push.example.org`. A push that waits more
than five minutes behind a slow service is dropped, and a device whose push
service refuses it, or that fails five times in a row, is removed.

## The canvas on a phone

A session's canvas, its transcript and the panes of pages and files around
it, draws as tabs on a touch screen under 760 px wide. A strip under the header starts
with a tab for the transcript, then has one for each docked pane, in reading
order: column by column, each from top to bottom. The open tab's pane fills
the screen. The others keep running behind it, so a page you leave keeps its
state and the agent's updates still reach it.

The message box stays at the foot on every tab, so you can write to the agent
while you look at a page or a file.

A tab whose pane changed while another tab was open gets a dot: a new version
of a docked file, a page the agent sent again or gave a new state, or
something newly docked. The transcript's tab gets one when the agent writes
while another tab is open, and that reply stays unread until you open the
transcript. Opening a tab clears its dot. The agent docking something never
switches the tab you are on; Show on a docked card does.

Dragging panes and resizing them is for a desktop. On a phone, press and hold
a tab other than the transcript's for half a second to get Undock, which puts
the page or file back on its card in the transcript. The canvas itself does
not change on a phone: a desktop on the same session still shows its columns.

A phone has one window. When the canvas spreads over several browser
windows on a desktop, the phone's strip holds the panes of every window,
window 1's before the others', and Move to window, Pop out and the window
chips are not shown.
