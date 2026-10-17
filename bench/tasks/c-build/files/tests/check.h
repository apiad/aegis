#ifndef TASKQ_CHECK_H
#define TASKQ_CHECK_H

#include <stdio.h>
#include <stdlib.h>

static int check_failures;
#define CHECK(cond)                                                          \
    do {                                                                     \
        if (!(cond)) {                                                       \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, \
                    #cond);                                                  \
            check_failures++;                                                \
        }                                                                    \
    } while (0)
#define CHECK_DONE(name)                                                      \
    do {                                                                      \
        printf("%s: %s\n", name, check_failures ? "FAILED" : "ok");           \
        return check_failures ? 1 : 0;                                        \
    } while (0)

#endif
