---
title: The code map
kind: howto
when: learning an unfamiliar repository's layout, finding every caller of a function or what a change would break, measuring a codebase (sizes, complexity, typing, cycles, dead code) or reviewing a branch, instead of grep or a one-off script
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
to check if you changed it, or what would break. `aegis map callees <symbol>` goes the other way.

A call on a receiver, such as `self.queues.turn_ended()`, resolves through the
receiver's type wherever the source states one: a parameter or variable
annotation, a constructor call assigned to it, a class field, an import under
another name. When nothing in the receiver's class types an attribute (it is
set to `None` there and assigned from outside), the map takes the one class
every assignment to an attribute of that name agrees on, preferring
assignments through a receiver of the same class, and leaving out tests' fakes
when it answers for code outside the tests. A call through a base class or
protocol method also reaches every override in a subclass, since that is what
may run: `callers` of an implementation lists the calls made through its
protocol.

An edge without a mark is sure. An edge ending in `?` is not: either the
receiver's type is stated nowhere, so the call matched every method of that
name, or the receiver may hold more than one class. A call on a receiver never
matches a module-level function by name, and a call on a type from outside the
repository (`Path`, `dict`, `json`) matches nothing. Deeper levels follow sure
edges only.

The receiver types work in Python, JavaScript and TypeScript. Other languages
get the rest of the map with calls matched by name; another language is
one more `Spec` in `aegis/codemap/specs.py` (its docstring says how).

## Numbers for a review

Four views give numbers and no verdicts. Which numbers matter, and where a
line falls, is yours to decide: each takes `--json`, and a gate or a review is
a filter over it.

- `aegis map metrics [path]` has one row per definition, file and
  folder: lines, complexity (counted as ruff's C901 counts it for Python, and
  as decision points for other languages), whether it has a doc, parameters
  with a type out of all, a stated return type, uses of `Any`, calls on a
  receiver of unknown type (the missing types that cost navigation), and
  fan-in and fan-out. Folders carry which folders they use. The text view
  ranks the top rows; `--sort complexity` or `fan-in` and `--top` change it.
- `aegis map cycles [path]` lists the files, or with `--level package` the
  folders, that use each other in a cycle, with one cycle spelled out. In
  Python a use in an annotation counts.
- `aegis map unused [path]` lists definitions no reference names outside
  their own body, and those only tests name. It goes by name, so a function
  reached through a string (a `getattr` dispatch, a route table, HTML) is
  listed; dunders, decorated registrations, overrides, handlers assigned to
  `on…` properties and members of object literals are left out.
- `aegis map diff [base]` reads a branch: every definition the branch touched
  since `base` (by default the merge-base with `origin/main`, uncommitted work
  included), whether it was added, removed, moved or modified, its lines and
  complexity before and after, the sure users outside the branch, and for a
  removed one every place that still names it.

For example, the definitions over 50 lines and those ruff's C901 would flag:

```bash
aegis map metrics src --json | jq '[.functions[] | select(.lines > 50)] | length'
aegis map metrics src --json | jq -r '.functions[] | select(.complexity > 10) | .id'
```

## The index

The first command in a repository parses every file `git ls-files` lists in
a known language, in a pool of processes. That takes a few seconds on a
thousand files, and stderr says how many it parsed. Ignored files are never
indexed; `--exclude <path or glob>` (or `-x`, repeatable) leaves out more,
such as a `legacy/` folder or generated code, from the views and from what a
name may match. An excluded file keeps its place in the index, so changing
the exclusions re-parses nothing. After that each command re-parses only the
files whose modification time or size changed, so a query costs about a
second. The index lives in the user's cache directory, one file per
repository, and every session of the same user shares it.

Each grammar downloads the first time the map meets its language. On a host
without internet those files are skipped, and stderr names how many of which
language.
