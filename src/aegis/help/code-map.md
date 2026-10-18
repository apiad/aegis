---
title: The code map
kind: howto
when: learning an unfamiliar repository's layout, finding every caller of a function, or what a change would break, instead of grep
---

`aegis map` reads a git repository with tree-sitter and answers what grep
cannot say it answered completely: the repository's layout with each module's
first doc line, a file's outline, every caller of a definition, and everything
that depends on it. It runs in the shell, needs no server and works in any
language the pack of tree-sitter grammars has, from Python and TypeScript to
Go, Rust, Java and C.

## Start with the layout

Run `aegis map repo` in the repository for its top folders, with how many
classes, functions and methods each holds. Give a folder and `--depth 2` to
open it further: each file then shows its first doc line, which is often
enough to know where to look. `aegis map outline <path>` lists a file's or a
folder's definitions with their line, signature and first doc line, and
`aegis map deps <path>` says which files use which.

Read a whole file only once the map has said it is the one you need.
`aegis map show <symbol>` prints one definition's source without the rest of
its file.

## Name a definition

Every command that takes a symbol accepts a bare name (`save`), a class and
method (`Store.put`), or a path and a name (`pkg/store.py:helper`), where the
path may be any ending of the file's path. A name defined in several places
answers for each; `aegis map find <name>` lists them.

## Callers and impact

`aegis map callers <symbol>` lists every call of a definition, grouped by
file, as the enclosing definition and its line. `--depth 2` also lists the
callers of those callers, indented. `aegis map impact <symbol>` counts every
use, not only calls, and follows three levels by default: what you would have
to check if you changed it. `aegis map callees <symbol>` goes the other way.

An edge without a mark is sure: the name is defined in the same file, or the
file that refers to it imports both the target's module and the name. An edge
ending in `?` matched by name only. Methods with common names such as `close`
or `read` collect many of those, so check them before trusting them. Deeper
levels follow sure edges only.

## The index

The first command in a repository parses every file `git ls-files` lists in
a known language. That takes seconds to half a minute on a few thousand files,
and stderr says how many it parsed. After that each command re-parses only the
files whose modification time or size changed, so a query costs about a
second. The index lives in the user's cache directory, one file per
repository, and every session of the same user shares it.

Each grammar downloads the first time the map meets its language. On a host
without internet those files are skipped, and stderr names how many of which
language.
