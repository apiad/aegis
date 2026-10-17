#!/bin/bash
# Oracle: follow the header's rename and new parameter, then free the item only
# after its flags were read.
set -euo pipefail
cd "$1"
sed -i 's/->name/->title/g' src/main.c src/parse.c
python3 - <<'PY'
import pathlib

p = pathlib.Path("src/parse.c")
s = p.read_text()
old = """    item *it = item_new((int)id, title, (int)prio);
    if (it)
        it->flags = parse_flags(flags, bar);
    return it;"""
assert old in s
p.write_text(s.replace(old, "    return item_new((int)id, title, (int)prio, parse_flags(flags, bar));"))
PY
# The use-after-free: list_remove reads the item's flags after item_free.
python3 - <<'PY'
import pathlib

p = pathlib.Path("src/list.c")
s = p.read_text()
old = "        item_free(n->it);\n        if (n->prev)"
assert old in s
s = s.replace(old, "        if (n->prev)")
s = s.replace("            l->pinned--;\n        free(n);", "            l->pinned--;\n        item_free(n->it);\n        free(n);")
p.write_text(s)
PY
