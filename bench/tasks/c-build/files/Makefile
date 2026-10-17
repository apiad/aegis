# taskq: a to-do list CLI. `make` builds build/taskq; `make test` builds and runs
# the unit tests under AddressSanitizer.
CC = cc
CPPFLAGS = -Iinclude -D_POSIX_C_SOURCE=200809L
CFLAGS = -std=c11 -Wall -Wextra -Werror -g -O1 -fsanitize=address -fno-omit-frame-pointer
LDFLAGS = -fsanitize=address
# The tests check memory errors; leak reports are left to a separate valgrind run.
export ASAN_OPTIONS = detect_leaks=0

LIB = build/item.o build/list.o build/parse.o
TESTS = build/test_list build/test_parse

.PHONY: all test clean

all: build/taskq

build/%.o: src/%.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c $< -o $@

build/test_%.o: tests/test_%.c | build
	$(CC) $(CPPFLAGS) $(CFLAGS) -MMD -MP -c $< -o $@

build/taskq: build/main.o $(LIB)
	$(CC) $(LDFLAGS) $^ -o $@

build/test_%: build/test_%.o $(LIB)
	$(CC) $(LDFLAGS) $^ -o $@

test: $(TESTS)
	@for t in $(TESTS); do ./$$t || exit 1; done
	@echo "all tests passed"

build:
	mkdir -p build

clean:
	rm -rf build

-include $(wildcard build/*.d)
