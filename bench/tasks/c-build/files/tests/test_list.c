#include "check.h"
#include "list.h"

static list three(void)
{
    list l;
    list_init(&l);
    list_push(&l, item_new(1, "buy milk", 3, 0));
    list_push(&l, item_new(2, "call the bank", 1, 0));
    list_push(&l, item_new(3, "water plants", 3, ITEM_PINNED));
    return l;
}

int main(void)
{
    list l = three();
    CHECK(l.len == 3 && l.pinned == 1);
    CHECK(list_find(&l, 2) != NULL);
    CHECK(list_find(&l, 9) == NULL);

    CHECK(list_remove(&l, 2) == 1);
    CHECK(l.len == 2 && l.head->it->id == 1 && l.tail->it->id == 3);
    CHECK(l.head->next == l.tail && l.tail->prev == l.head);
    CHECK(list_remove(&l, 2) == 0);
    CHECK(list_remove(&l, 3) == 1 && l.pinned == 0 && l.len == 1);
    list_clear(&l);

    l = three();
    list_sort(&l);
    CHECK(l.head->it->id == 3 && l.head->next->it->id == 2 && l.tail->it->id == 1);
    CHECK(l.tail->prev->it->id == 2 && l.head->prev == NULL && l.tail->next == NULL);
    list_clear(&l);
    CHECK(l.len == 0 && l.head == NULL);
    CHECK_DONE("test_list");
}
