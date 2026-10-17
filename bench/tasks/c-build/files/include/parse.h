#ifndef TASKQ_PARSE_H
#define TASKQ_PARSE_H

#include <stdio.h>
#include "list.h"

/* A line is "id|priority|flags|title", flags a string of d (done) and p (pinned).
 * Returns the item, or NULL for a blank, comment (#) or malformed line. */
item *parse_line(const char *line);
/* Reads every line of f into l; returns how many lines were malformed. */
int parse_file(FILE *f, list *l);
void format_item(const item *it, char *out, size_t n);

#endif
