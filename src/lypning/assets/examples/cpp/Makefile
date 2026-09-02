# Makefile — build embed.cpp and quickstart.cpp against whichever lypning this
# machine actually has. A plain Makefile on purpose: a build system would be
# the first dependency this project does not have.
#
# The one invariant, the same one the C example beside this holds: THERE ARE
# TWO SHAPES OF THIS REPOSITORY AND BOTH MUST BUILD. A source checkout has a
# freshly built library under assets/rust/target/release-lib and the headers in
# assets/include; a wheel has neither — `pip install` ships this example and
# these headers, but the library and its copy of the headers only exist where
# the installer put them, under $LYPNING_HOME (~/.lypning). Defaulting to the
# checkout's paths is not a build error in a wheel, it is a bare
# `cannot find -llypning` naming a directory that was never going to exist. So
# the choice is made here, printed on every link, and never guessed at.
#
# The same failure has a second half, which is why the headers and the library
# are real prerequisites below and not just -I and -L flags: an `embed` that is
# newer than embed.cpp but older than lypning.hpp is yesterday's binary
# reporting on today's ABI, and it would print a clean table.
#
#   make                 build embed, the contract test, preferring the
#                        checkout's freshly built library
#   make run             build and run it, from a scratch directory
#   make quickstart      build the file to copy: quickstart "<src>" [args...]
#   make INCDIR=... LIBDIR=...   link something else entirely
#
# `lypning lib --cflags --libs` answers the same question from the installed
# package, if you would rather ask it than let this file look:
#
#   c++ -std=c++17 $(lypning lib --cflags) embed.cpp -o embed $(lypning lib --libs)
#
# Build the library first, either with `lypning build --lib` or by hand:
#
#   cd ../../rust && cargo build --lib --features capi --profile release-lib
#
# In a wheel this whole directory is read-only; copy it somewhere writable
# first and it builds unchanged, because nothing below depends on where it is:
# with no checkout beside it, it links the installed tree.

here    := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
assets  := $(abspath $(here)/../..)

LYPNING_HOME ?= $(HOME)/.lypning

checkout_lib := $(assets)/rust/target/release-lib
checkout_inc := $(assets)/include
state_lib    := $(LYPNING_HOME)/lib
state_inc    := $(LYPNING_HOME)/include

# Prefer the checkout's own build output when it exists. A checkout that links
# the installed copy is a checkout whose example cannot fail on the change you
# just made — which is the whole reason this example asserts anything.
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

# $(wildcard) rather than bare paths: a missing header or library must reach
# the message below, not make's own "No rule to make target" — or, for the
# library, a bare `ld: library not found` that names neither the directory that
# was searched nor the command that fills it.
headers := $(wildcard $(INCDIR)/lypning.hpp $(INCDIR)/lypning.h)
lib     := $(wildcard $(LIBDIR)/liblypning.so $(LIBDIR)/liblypning.dylib)

# A phony prerequisite stands in for whichever of the two is missing. A missing
# file simply drops out of a rule, so without this an old binary would be "up
# to date" against a library that is not there — and the message would print
# only for someone who had never built before, which is not who needs it.
# Both headers count as one: lypning.hpp includes lypning.h and is useless
# without it, and `lypning build --lib` installs the pair.
missing := $(if $(filter 2,$(words $(headers))),,missing-header) $(if $(lib),,missing-library)
.PHONY: missing-header missing-library
missing-header missing-library:
	@test "$(words $(headers))" = 2 || echo "no lypning.hpp and lypning.h under $(INCDIR)."
	@test -n "$(lib)" || echo "no liblypning.so or liblypning.dylib under $(LIBDIR)."
	@echo "build it:  lypning build --lib"
	@echo "      or:  cd $(assets)/rust && cargo build --lib --features capi --profile release-lib"
	@echo "or point at an installed copy:  make INCDIR=... LIBDIR=..."
	@exit 1

CXX      ?= c++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic
# LDFLAGS is the user's — a distribution's hardening flags arrive in it, and
# assigning over it would cost them something they had. Ours goes in its own
# variable and both are passed. -rpath so the binaries run from anywhere
# without LD_LIBRARY_PATH: an example that needs an environment variable to
# start is an example nobody runs.
link     := -L$(LIBDIR) -Wl,-rpath,$(abspath $(LIBDIR))
LDLIBS   ?= -llypning

BIN   := embed
# Not built by default: it is the file to copy, not the test to run.
QUICK := quickstart

.PHONY: all run clean
all: $(BIN)

# One rule for both binaries: the contract test and the file to copy link
# against the same tree, and the label below says which.
$(BIN) $(QUICK): %: %.cpp $(headers) $(lib) $(missing) $(MAKEFILE_LIST)
	$(CXX) $(CXXFLAGS) -I$(INCDIR) $< -o $@ $(link) $(LDFLAGS) $(LDLIBS)
	@echo "$@: header $(INCDIR)/lypning.hpp, library $(LIBDIR) ($(from))"

# From a directory mktemp made, never this one. The programs run with this
# process's working directory, and the ones CPython answers run with no
# restriction at all — CLAUDE.md invariant 4 applies to a library call exactly
# as to a spawn. The exit status is carried across the cleanup so a failed run
# still fails make.
run: $(BIN)
	@d=$$(mktemp -d) && (cd "$$d" && "$(here)/$(BIN)"); s=$$?; rm -rf "$$d"; exit $$s

clean:
	rm -f $(BIN) $(QUICK)
