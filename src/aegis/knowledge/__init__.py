"""Native skills and memory: knowledge that aegis keeps, finds and prunes.

The store. Each principal has ``<state>/knowledge/<principal>/`` with
``skills/<name>/SKILL.md`` and ``memories/<name>.md``. The state directory is
out of every member's reach, so every read and write goes through a server
operation that checks ownership: a member never lists, reads, searches or is
hinted another principal's items. The directory is a git repository and every
change through aegis is one commit, so a merge or a delete can be undone.
Nothing is pushed anywhere.

Names. A name is the file's stem (a skill's folder), matches ``NAME_RE`` and is
unique per principal across skills and memories together, so a ``[[name]]``
link and a usage row always mean one item. A save that would reuse another
item's name is refused before anything is written.

Search. BM25 over the name (split on ``-`` and ``_``, weight 3), the
description (2) and the first 600 characters of the body (1), with accents
folded, words cut to five letters and an English and Spanish stop list. A
spike over one server's real knowledge found it ties cheap embedding models
at no memory cost, so it is the default and imports only the standard
library. The index is built in memory and never written to disk.

The hint. Every send to a session that is not a slash command carries the
top three skills and the top three memories, with no threshold: the spike
found no score that separates messages needing a memory from the rest, and
a fixed line with the model as the filter reaches more of the ones that do.
Proposed and expired items are never hinted, but stay searchable.

Usage. Every hint, read and invocation is a row in one SQLite file beside the
journal's, so the engine can be judged on real use (the share of reads that
came from a hint, and at which rank) and unused items can be found.
"""
