# Makefile — build embed.cpp against whichever lypning this machine actually
# has. A plain Makefile on purpose: a build system would be the first
# dependency this project does not have.
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
#   make                 build and run against whichever tree was found
#   make embed           build only
#   make INCDIR=... LIBDIR=...   link something else entirely
#
# `lypning lib --cflags --libs` answers the same question from the installed
# package, if you would rather ask it than let this file look:
#
#   g++ -std=c++17 $(lypning lib --cflags) embed.cpp -o embed $(lypning lib --libs)
#
# Build the library first, either with `lypning build --lib` or by hand:
#
#   cd ../../rust && cargo build --lib --features capi --profile release-lib

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
ifneq ($(LIBDIR),$(default_lib))
from := LIBDIR override
endif

CXX      ?= c++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra -Wpedantic
# -rpath so the example runs from anywhere without LD_LIBRARY_PATH: an example
# that needs an environment variable to start is an example nobody runs.
LDFLAGS  := -L$(LIBDIR) -Wl,-rpath,$(abspath $(LIBDIR))
LDLIBS   := -llypning

BIN := embed

all: run

$(BIN): embed.cpp $(wildcard $(INCDIR)/lypning.hpp) $(wildcard $(INCDIR)/lypning.h)
	@test -f "$(INCDIR)/lypning.hpp" -a -f "$(INCDIR)/lypning.h" || { \
	  echo "no lypning.hpp/lypning.h under $(INCDIR)."; \
	  echo "build and install them:  lypning build --lib"; \
	  echo "or, in a checkout:  cd $(assets)/rust && cargo build --lib --features capi --profile release-lib"; \
	  echo "or point at a copy you already have:  make INCDIR=... LIBDIR=..."; \
	  exit 1; }
	@test -f "$(LIBDIR)/liblypning.so" -o -f "$(LIBDIR)/liblypning.dylib" || { \
	  echo "no liblypning under $(LIBDIR)."; \
	  echo "build it:  lypning build --lib"; \
	  echo "or point at an installed copy:  make INCDIR=... LIBDIR=..."; \
	  exit 1; }
	$(CXX) $(CXXFLAGS) -I$(INCDIR) embed.cpp -o $@ $(LDFLAGS) $(LDLIBS)
	@echo "$@: header $(INCDIR)/lypning.hpp, library $(LIBDIR) ($(from))"

run: $(BIN)
	./$(BIN)

clean:
	rm -f $(BIN)

.PHONY: all run clean
