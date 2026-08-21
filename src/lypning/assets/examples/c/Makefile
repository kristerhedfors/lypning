# Makefile — build embed.c against whichever lypning this machine actually has.
#
# The one invariant: there are two shapes of this repository and both must
# build. A source checkout has a freshly built library under
# assets/rust/target/release-lib and a header in assets/include; a wheel has
# neither, only the copies the installer put in $LYPNING_HOME (~/.lypning).
# Picking the wrong one is not a build error — it links, it runs, and it tests
# yesterday's library — so the choice is made here, printed on every link, and
# never guessed at by the reader.
#
# The same failure has a second half, which is why the header and the library
# are real prerequisites below and not just -I and -L flags: an `embed` that is
# newer than embed.c but older than lypning.h is yesterday's binary reporting on
# today's ABI, and it would say `all assertions held`.
#
#   make                 build, preferring the checkout's freshly built library
#   make run             build and run it
#   make valgrind        the same, under valgrind if this machine has one
#   make LIBDIR=... INCDIR=...   link something else entirely
#
# No pkg-config: an example whose build depends on a tool the reader may not
# have is an example about that tool.
#
# In a wheel this whole directory is read-only; copy it somewhere writable
# first and it builds unchanged, because nothing below refers to where it is.

here    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
assets  := $(abspath $(here)/../..)

LYPNING_HOME ?= $(HOME)/.lypning

checkout_lib := $(assets)/rust/target/release-lib
checkout_inc := $(assets)/include
state_lib    := $(LYPNING_HOME)/lib
state_inc    := $(LYPNING_HOME)/include

# Prefer the checkout's own build output when it exists. A checkout that links
# the installed copy is a checkout whose example cannot fail on the change you
# just made — which is the entire reason this example asserts anything.
ifneq ($(wildcard $(checkout_lib)/liblypning.so $(checkout_lib)/liblypning.dylib),)
default_lib := $(checkout_lib)
default_inc := $(checkout_inc)
from        := source checkout
else
default_lib := $(state_lib)
default_inc := $(state_inc)
from        := installed tree
endif

LIBDIR ?= $(default_lib)
INCDIR ?= $(default_inc)
# Both, separately. Overriding only INCDIR gives a header from one tree and a
# library from another, which is the exact mismatch the printed label exists to
# make visible — so the label has to notice it.
ifneq ($(LIBDIR),$(default_lib))
from := override
endif
ifneq ($(INCDIR),$(default_inc))
from := override
endif

# $(wildcard) rather than a bare path: a missing header must reach the message
# below, not make's own "No rule to make target".
header := $(wildcard $(INCDIR)/lypning.h)
lib    := $(wildcard $(LIBDIR)/liblypning.so $(LIBDIR)/liblypning.dylib)

CC      ?= cc
# -DNDEBUG is deliberately absent and embed.c refuses to compile with it: the
# assertions are the test, and -O2 must not be a way to delete them.
CFLAGS  ?= -std=c11 -O2 -Wall -Wextra -Wpedantic
# LDFLAGS is the user's — a distribution's hardening flags arrive in it, and
# assigning over it would cost them something they had. Ours goes in its own
# variable and both are passed.
link    := -L$(LIBDIR) -Wl,-rpath,$(LIBDIR)
LDLIBS  ?= -llypning

BIN := embed
# Written by one of the programs, removed by the same run — unless the run
# aborts, which is what the assertions are for.
LITTER := lypning-embed-example.txt

.PHONY: all run valgrind clean
all: $(BIN)

$(BIN): embed.c $(header) $(lib) $(MAKEFILE_LIST)
	@test -n "$(header)" || { \
	  echo "no lypning.h under $(INCDIR)."; \
	  echo "build it:  cd $(assets)/rust && cargo build --lib --features capi --profile release-lib"; \
	  echo "or point at an installed copy:  make INCDIR=... LIBDIR=..."; \
	  exit 1; }
	$(CC) $(CFLAGS) -I$(INCDIR) embed.c -o $@ $(link) $(LDFLAGS) $(LDLIBS)
	@echo "$@: header $(INCDIR)/lypning.h, library $(LIBDIR) ($(from))"

run: $(BIN)
	./$(BIN)

# The example frees every handle it takes, so a leak here is the library's or
# ours and either is worth knowing. --trace-children stays off on purpose: the
# fallback exec's python3, and valgrinding CPython measures CPython.
valgrind: $(BIN)
	@command -v valgrind >/dev/null 2>&1 || { \
	  echo "valgrind is not installed on this machine; skipping"; exit 0; }; \
	valgrind --leak-check=full --show-leak-kinds=definite,indirect \
	         --errors-for-leak-kinds=definite,indirect --error-exitcode=9 \
	         --trace-children=no ./$(BIN)

clean:
	rm -f $(BIN) $(LITTER)
