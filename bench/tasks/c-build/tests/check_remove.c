/* Hidden check: removal at every position, links intact, under ASan. */
#include <stdio.h>
#include "list.h"

static int fails;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "check_remove:%d: %s\n", __LINE__, #c); fails++; } } while (0)

static void fill(list *l, int n)
{
    list_init(l);
    for (int i = 1; i <= n; i++)
        list_push(l, item_new(i, "x", 1 + i % 5, 0));
}

static void links_ok(const list *l)
{
    size_t n = 0;
    const node *prev = NULL;
    for (const node *x = l->head; x; x = x->next, n++) {
        CHECK(x->prev == prev);
        prev = x;
    }
    CHECK(prev == l->tail && n == l->len);
}

int main(void)
{
    list l;
    fill(&l, 1);
    CHECK(list_remove(&l, 1) && l.head == NULL && l.tail == NULL && l.len == 0);
    for (int victim = 1; victim <= 5; victim++) {
        fill(&l, 5);
        CHECK(list_remove(&l, victim) == 1);
        CHECK(list_find(&l, victim) == NULL);
        links_ok(&l);
        list_clear(&l);
    }
    fill(&l, 6);
    for (int id = 6; id >= 1; id -= 2)
        CHECK(list_remove(&l, id));
    links_ok(&l);
    list_sort(&l);
    links_ok(&l);
    list_clear(&l);
    printf("check_remove: %s\n", fails ? "FAILED" : "ok");
    return fails != 0;
}
