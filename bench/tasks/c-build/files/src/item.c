#include <stdlib.h>
#include <string.h>
#include "item.h"

item *item_new(int id, const char *title, int priority, unsigned flags)
{
    item *it = malloc(sizeof *it);
    if (!it)
        return NULL;
    it->id = id;
    it->priority = priority;
    it->flags = flags;
    it->title = strdup(title);
    if (!it->title) {
        free(it);
        return NULL;
    }
    return it;
}

void item_free(item *it)
{
    if (!it)
        return;
    free(it->title);
    free(it);
}

int item_cmp(const item *a, const item *b)
{
    int pa = (a->flags & ITEM_PINNED) != 0, pb = (b->flags & ITEM_PINNED) != 0;
    if (pa != pb)
        return pb - pa;
    if (a->priority != b->priority)
        return a->priority - b->priority;
    return a->id - b->id;
}
