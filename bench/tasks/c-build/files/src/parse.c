#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "parse.h"

static unsigned parse_flags(const char *s, const char *end)
{
    unsigned f = 0;
    for (; s < end; s++) {
        if (*s == 'd')
            f |= ITEM_DONE;
        else if (*s == 'p')
            f |= ITEM_PINNED;
    }
    return f;
}

item *parse_line(const char *line)
{
    while (isspace((unsigned char)*line))
        line++;
    if (*line == '\0' || *line == '#')
        return NULL;
    char *end;
    long id = strtol(line, &end, 10);
    if (end == line || *end != '|')
        return NULL;
    const char *p = end + 1;
    long prio = strtol(p, &end, 10);
    if (end == p || *end != '|' || prio < 1 || prio > 5)
        return NULL;
    const char *flags = end + 1;
    const char *bar = strchr(flags, '|');
    if (!bar)
        return NULL;
    char title[256];
    size_t n = strcspn(bar + 1, "\r\n");
    if (n == 0 || n >= sizeof title)
        return NULL;
    memcpy(title, bar + 1, n);
    title[n] = '\0';
    item *it = item_new((int)id, title, (int)prio);
    if (it)
        it->flags = parse_flags(flags, bar);
    return it;
}

int parse_file(FILE *f, list *l)
{
    char buf[512];
    int bad = 0;
    while (fgets(buf, sizeof buf, f)) {
        item *it = parse_line(buf);
        if (it)
            list_push(l, it);
        else {
            const char *s = buf;
            while (isspace((unsigned char)*s))
                s++;
            if (*s && *s != '#')
                bad++;
        }
    }
    return bad;
}

void format_item(const item *it, char *out, size_t n)
{
    snprintf(out, n, "%3d [%c%c] p%d  %s", it->id,
             (it->flags & ITEM_DONE) ? 'x' : ' ',
             (it->flags & ITEM_PINNED) ? '*' : ' ',
             it->priority, it->name);
}
