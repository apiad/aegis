#include <string.h>
#include "check.h"
#include "parse.h"

int main(void)
{
    item *it = parse_line("7|2|p|renew passport\n");
    CHECK(it && it->id == 7 && it->priority == 2 && it->flags == ITEM_PINNED);
    CHECK(it && strcmp(it->title, "renew passport") == 0);
    char out[320];
    format_item(it, out, sizeof out);
    CHECK(strcmp(out, "  7 [ *] p2  renew passport") == 0);
    item_free(it);

    CHECK(parse_line("# a comment") == NULL);
    CHECK(parse_line("   ") == NULL);
    CHECK(parse_line("8|9||out of range") == NULL);
    CHECK(parse_line("x|1||no id") == NULL);

    FILE *f = tmpfile();
    fputs("1|3||one\n\n# skip\nbroken line\n2|1|dp|two\n", f);
    rewind(f);
    list l;
    list_init(&l);
    CHECK(parse_file(f, &l) == 1);
    CHECK(l.len == 2 && (list_find(&l, 2)->flags == (ITEM_DONE | ITEM_PINNED)));
    fclose(f);
    list_clear(&l);
    CHECK_DONE("test_parse");
}
