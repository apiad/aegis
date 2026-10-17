#!/bin/bash
# Oracle: follow the header's rename and new parameter, then free the item only
# after its flags were read; then the follow-ups (below).
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
# The follow-ups: done and drop save the file through a temporary file and a
# rename, keeping the comment lines; and an add command.
cat > src/main.c <<'C'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parse.h"

static int usage(void)
{
    fprintf(stderr, "usage: taskq FILE [list | done ID | drop ID | find TEXT | add PRIORITY TITLE]\n");
    return 2;
}

static int save(const char *path, const list *l)
{
    char tmp[4096], buf[512];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *out = fopen(tmp, "w");
    if (!out)
        return 1;
    FILE *in = fopen(path, "r");
    while (in && fgets(buf, sizeof buf, in))
        if (buf[0] == '#')
            fputs(buf, out);
    if (in)
        fclose(in);
    for (node *n = l->head; n; n = n->next)
        fprintf(out, "%d|%d|%s%s|%s\n", n->it->id, n->it->priority,
                (n->it->flags & ITEM_DONE) ? "d" : "", (n->it->flags & ITEM_PINNED) ? "p" : "",
                n->it->title);
    if (fclose(out) != 0)
        return 1;
    return rename(tmp, path) == 0 ? 0 : 1;
}

int main(int argc, char **argv)
{
    if (argc < 2)
        return usage();
    FILE *f = fopen(argv[1], "r");
    if (!f) {
        perror(argv[1]);
        return 1;
    }
    list l;
    list_init(&l);
    int bad = parse_file(f, &l);
    fclose(f);
    if (bad)
        fprintf(stderr, "taskq: %d malformed line(s) skipped\n", bad);
    const char *cmd = argc > 2 ? argv[2] : "list";
    char line[320];
    int rc = 0;
    if (strcmp(cmd, "list") == 0) {
        list_sort(&l);
        for (node *n = l.head; n; n = n->next) {
            format_item(n->it, line, sizeof line);
            puts(line);
        }
    } else if (strcmp(cmd, "done") == 0 && argc > 3) {
        item *it = list_find(&l, atoi(argv[3]));
        if (it) {
            it->flags |= ITEM_DONE;
            rc = save(argv[1], &l);
            printf("done: %s\n", it->title);
        } else
            rc = 1;
    } else if (strcmp(cmd, "drop") == 0 && argc > 3) {
        rc = list_remove(&l, atoi(argv[3])) ? save(argv[1], &l) : 1;
    } else if (strcmp(cmd, "find") == 0 && argc > 3) {
        for (node *n = l.head; n; n = n->next)
            if (strstr(n->it->title, argv[3])) {
                format_item(n->it, line, sizeof line);
                puts(line);
            }
    } else if (strcmp(cmd, "add") == 0 && argc > 4) {
        int prio = atoi(argv[3]);
        size_t len = strlen(argv[4]);
        if (prio < 1 || prio > 5 || len == 0 || len > 255 || strchr(argv[4], '|'))
            rc = 1;
        else {
            int id = 0;
            for (node *n = l.head; n; n = n->next)
                if (n->it->id > id)
                    id = n->it->id;
            list_push(&l, item_new(id + 1, argv[4], prio, 0));
            rc = save(argv[1], &l);
            if (rc == 0)
                printf("added: %d\n", id + 1);
        }
    } else
        rc = usage();
    list_clear(&l);
    return rc;
}
C
