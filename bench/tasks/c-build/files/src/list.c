#include <stdlib.h>
#include "list.h"

void list_init(list *l)
{
    l->head = l->tail = NULL;
    l->len = 0;
    l->pinned = 0;
}

void list_push(list *l, item *it)
{
    node *n = malloc(sizeof *n);
    if (!n)
        abort();
    n->it = it;
    n->next = NULL;
    n->prev = l->tail;
    if (l->tail)
        l->tail->next = n;
    else
        l->head = n;
    l->tail = n;
    l->len++;
    if (it->flags & ITEM_PINNED)
        l->pinned++;
}

item *list_find(const list *l, int id)
{
    for (node *n = l->head; n; n = n->next)
        if (n->it->id == id)
            return n->it;
    return NULL;
}

int list_remove(list *l, int id)
{
    for (node *n = l->head; n; n = n->next) {
        if (n->it->id != id)
            continue;
        item_free(n->it);
        if (n->prev)
            n->prev->next = n->next;
        else
            l->head = n->next;
        if (n->next)
            n->next->prev = n->prev;
        else
            l->tail = n->prev;
        l->len--;
        if (n->it->flags & ITEM_PINNED)
            l->pinned--;
        free(n);
        return 1;
    }
    return 0;
}

/* Insertion sort on the links: stable, and the lists are short. */
void list_sort(list *l)
{
    node *sorted = NULL, *last = NULL;
    node *n = l->head;
    while (n) {
        node *next = n->next;
        node *at = last;
        while (at && item_cmp(at->it, n->it) > 0)
            at = at->prev;
        n->prev = at;
        n->next = at ? at->next : sorted;
        if (n->next)
            n->next->prev = n;
        else
            last = n;
        if (at)
            at->next = n;
        else
            sorted = n;
        n = next;
    }
    l->head = sorted;
    l->tail = last;
}

void list_clear(list *l)
{
    node *n = l->head;
    while (n) {
        node *next = n->next;
        item_free(n->it);
        free(n);
        n = next;
    }
    list_init(l);
}
