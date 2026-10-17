#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parse.h"

static int usage(void)
{
    fprintf(stderr, "usage: taskq FILE [list | done ID | drop ID | find TEXT]\n");
    return 2;
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
            printf("done: %s\n", it->name);
        } else
            rc = 1;
    } else if (strcmp(cmd, "drop") == 0 && argc > 3) {
        rc = list_remove(&l, atoi(argv[3])) ? 0 : 1;
    } else if (strcmp(cmd, "find") == 0 && argc > 3) {
        for (node *n = l.head; n; n = n->next)
            if (strstr(n->it->name, argv[3])) {
                format_item(n->it, line, sizeof line);
                puts(line);
            }
    } else
        rc = usage();
    list_clear(&l);
    return rc;
}
