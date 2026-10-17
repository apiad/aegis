#ifndef TASKQ_ITEM_H
#define TASKQ_ITEM_H

/* Flags an item can carry. */
#define ITEM_DONE    0x1u
#define ITEM_PINNED  0x2u

/* One task. `title` is owned by the item (renamed from `name` in 0.4). */
typedef struct item {
    int id;
    int priority;          /* 1 (highest) to 5 */
    unsigned flags;        /* ITEM_* */
    char *title;
} item;

item *item_new(int id, const char *title, int priority, unsigned flags);
void item_free(item *it);
int item_cmp(const item *a, const item *b);   /* pinned first, then priority, then id */

#endif
