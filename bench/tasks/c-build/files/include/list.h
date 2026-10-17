#ifndef TASKQ_LIST_H
#define TASKQ_LIST_H

#include <stddef.h>
#include "item.h"

typedef struct node {
    item *it;
    struct node *prev, *next;
} node;

typedef struct list {
    node *head, *tail;
    size_t len;
    size_t pinned;                          /* items with ITEM_PINNED */
} list;

void list_init(list *l);
void list_push(list *l, item *it);          /* takes ownership */
int list_remove(list *l, int id);           /* frees the item; 1 if found */
item *list_find(const list *l, int id);
void list_sort(list *l);                    /* by item_cmp, stable */
void list_clear(list *l);

#endif
